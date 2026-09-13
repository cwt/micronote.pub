import base64
import mimetypes
from enum import Enum
from gzip import GzipFile
from io import BytesIO
from typing import Any

import piexif
import requests
from neosqlite.gridfs import GridFSBucket
from neosqlite.gridfs.errors import NoFile
from neosqlite.gridfs.grid_file import GridOut
from PIL import Image


def load(url, user_agent):
    """Initializes a `PIL.Image` from the URL."""
    with requests.get(url, stream=True,
                      headers={"User-Agent": user_agent}) as resp:
        resp.raise_for_status()
        content_type = resp.headers.get('content-type') or ''
        if not content_type.startswith('image/'):
            raise ValueError(
                f"bad content-type {resp.headers.get('content-type')}"
            )

        resp.raw.decode_content = True
        return Image.open(BytesIO(resp.raw.read()))


def info(img):
    """Returns image info dictionary to be used with `PIL.Image.save()`"""
    if not isinstance(img, Image.Image):
        raise TypeError('`img` must be an instance of `PIL.Image.Image`')

    INFO_KEYS = ['duration',
                 'gamma',
                 'icc_profile',
                 'interlace',
                 'loop',
                 'transparency']
    return dict(
        [(key, img.info.get(key)) for key in INFO_KEYS if key in img.info]
    )


def to_data_uri(img):
    out = BytesIO()
    img.save(out, format=img.format)
    out.seek(0)
    data = base64.b64encode(out.read()).decode("utf-8")
    return f"data:{img.get_format_mimetype()};base64,{data}"


class Kind(Enum):
    ATTACHMENT = "attachment"
    ACTOR_ICON = "actor_icon"
    UPLOAD = "upload"
    OG_IMAGE = "og"


def _gzip_image(img) -> bytes:
    """Encodes a `PIL.Image` as gzipped bytes."""
    # NB: thumbnail copies lose `.format`, fall back to PNG then.
    image_format = img.format or "PNG"
    with BytesIO() as buf:
        with GzipFile(mode="wb", fileobj=buf) as gzipped:
            img.save(gzipped, format=image_format,
                     optimize=True, progressive=True, **info(img))
        return buf.getvalue()


class MediaCache:
    def __init__(self, connection_factory, user_agent: str) -> None:
        self._connection_factory = connection_factory
        self.user_agent = user_agent

    @property
    def _bucket(self) -> GridFSBucket:
        # Resolved per call: buckets hold their thread's SQLite handle.
        return GridFSBucket(self._connection_factory().db)

    def _store(self, data: bytes, url: str, size: int | None, content_type: str | None, kind: Kind):
        """Stores gzipped bytes, with lookup metadata attached."""
        return self._bucket.upload_from_stream(
            filename=url,
            source=BytesIO(data),
            metadata={
                "url": url,
                "size": size,
                "content_type": content_type,
                "kind": kind.value,
            },
        )

    def get_file(self, url: str, size: int | None, kind: Kind) -> GridOut | None:
        found = self._bucket.find({
            "metadata.url": url,
            "metadata.size": size,
            "metadata.kind": kind.value,
        })
        for grid_out in found:
            return grid_out
        return None

    def get_media(self, file_id: Any) -> GridOut | None:
        try:
            return self._bucket.open_download_stream(file_id)
        except NoFile:
            return None

    def cache_og_image(self, url: str) -> None:
        if self.get_file(url, 100, Kind.OG_IMAGE):
            return
        i = load(url, self.user_agent)
        # Save the thumbnail (gzipped)
        i.thumbnail((100, 100))
        self._store(_gzip_image(i), url, 100, i.get_format_mimetype(), Kind.OG_IMAGE)

    def cache_attachment(self, url: str) -> None:
        if self.get_file(url, None, Kind.ATTACHMENT) or self.get_file(url, 720, Kind.ATTACHMENT):
            return
        if (
            url.endswith(".png")
            or url.endswith(".jpg")
            or url.endswith(".jpeg")
            or url.endswith(".gif")
        ):
            i = load(url, self.user_agent)
            # Save the original attachment (gzipped)
            self._store(_gzip_image(i), url, None, i.get_format_mimetype(), Kind.ATTACHMENT)
            # Save a thumbnail (gzipped)
            i.thumbnail((720, 720))
            self._store(_gzip_image(i), url, 720, i.get_format_mimetype(), Kind.ATTACHMENT)
            return

        # The attachment is not an image, download and save it anyway
        with requests.get(
            url, stream=True, headers={"User-Agent": self.user_agent}
        ) as resp:
            resp.raise_for_status()
            with BytesIO() as buf:
                with GzipFile(mode="wb", fileobj=buf) as gzipped:
                    for chunk in resp.iter_content():
                        if chunk:
                            gzipped.write(chunk)
                self._store(
                    buf.getvalue(),
                    url,
                    None,
                    mimetypes.guess_type(url)[0],
                    Kind.ATTACHMENT,
                )

    def cache_actor_icon(self, url: str) -> None:
        if self.get_file(url, 50, Kind.ACTOR_ICON):
            return
        i = load(url, self.user_agent)
        mimetype = i.get_format_mimetype()
        for size in [50, 80]:
            t1 = i.copy()
            t1.thumbnail((size, size))
            self._store(_gzip_image(t1), url, size, mimetype, Kind.ACTOR_ICON)

    def save_upload(self, obuf: BytesIO, filename: str, max_size: tuple) -> str:
        # Remove EXIF metadata
        if filename.lower().endswith(".jpg") \
        or filename.lower().endswith(".jpeg"):
            obuf.seek(0)
            with BytesIO() as buf2:
                piexif.remove(obuf.getvalue(), buf2)
                obuf.truncate(0)
                obuf.write(buf2.getvalue())

        mtype = mimetypes.guess_type(filename)[0]
        thumbnail_buf = BytesIO()
        if mtype and mtype.startswith('image'):
            i = Image.open(obuf, 'r')
            if (i.width > max_size[0]) or (i.height > max_size[1]):
                i.thumbnail(size=max_size)
                i.save(thumbnail_buf, format=i.format,
                       optimize=True, progressive=True, **info(i))
        obuf.seek(0)
        with BytesIO() as gbuf:
            with GzipFile(mode="wb", fileobj=gbuf) as gzipfile:
                gzipfile.write(thumbnail_buf.getvalue() or obuf.getvalue())

            oid = self._store(
                gbuf.getvalue(),
                filename,
                None,
                mtype,
                Kind.UPLOAD,
            )
            return str(oid)

    def cache(self, url: str, kind: Kind) -> None:
        if kind == Kind.ACTOR_ICON:
            self.cache_actor_icon(url)
        elif kind == Kind.OG_IMAGE:
            self.cache_og_image(url)
        else:
            self.cache_attachment(url)

    def get_actor_icon(self, url: str, size: int) -> Any:
        return self.get_file(url, size, Kind.ACTOR_ICON)

    def get_attachment(self, url: str, size: int) -> Any:
        return self.get_file(url, size, Kind.ATTACHMENT)
