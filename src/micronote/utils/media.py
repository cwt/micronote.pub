import mimetypes
import os
from enum import StrEnum
from gzip import GzipFile
from io import BytesIO
from typing import Any, NamedTuple

import requests
from neosqlite.gridfs import GridFSBucket
from neosqlite.gridfs.grid_file import GridOut
from PIL import Image, ImageOps

WEBP_QUALITY = 85
WEBP_METHOD = 6
WEBP_MIMETYPE = "image/webp"

MAX_REMOTE_ATTACHMENT_BYTES = 20 * 1024 * 1024


class StoredUpload(NamedTuple):
    oid: str
    filename: str
    mimetype: str | None


def _webp_filename(filename: str) -> str:
    """Swaps the extension for .webp, so URLs describe the stored bytes."""
    return os.path.splitext(filename)[0] + ".webp"


def load(url, user_agent):
    """Initializes a decoded `PIL.Image` from the URL."""
    with requests.get(url, stream=True, headers={"User-Agent": user_agent}) as resp:
        resp.raise_for_status()
        content_type = resp.headers.get("content-type") or ""
        if not content_type.startswith("image/"):
            raise ValueError(f"bad content-type {resp.headers.get('content-type')}")

        resp.raw.decode_content = True
        raw = resp.raw.read(MAX_REMOTE_ATTACHMENT_BYTES + 1)
        if len(raw) > MAX_REMOTE_ATTACHMENT_BYTES:
            raise ValueError(f"image over size cap: {url}")
        img = Image.open(BytesIO(raw))
        img.load()
        return img


class Kind(StrEnum):
    ATTACHMENT = "attachment"
    ACTOR_ICON = "actor_icon"
    UPLOAD = "upload"
    OG_IMAGE = "og"
    CUSTOM_EMOJI = "custom_emoji"


def _encode_image(img) -> bytes:
    """Encodes a `PIL.Image` as WebP bytes.

    Applies EXIF orientation first (re-encoding drops EXIF with it),
    preserves alpha for dark-theme rendering, and saves animated images
    as their first frame only.
    """
    img = ImageOps.exif_transpose(img)
    img.seek(0)
    if img.mode == "P":
        if "transparency" in img.info:
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
    elif img.mode == "LA":
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    save_options = {"quality": WEBP_QUALITY, "method": WEBP_METHOD}
    if "icc_profile" in img.info:
        save_options["icc_profile"] = img.info["icc_profile"]
    with BytesIO() as buf:
        img.save(buf, format="WEBP", **save_options)
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
        """Stores bytes with lookup metadata attached.

        Storage invariant: image/webp entries hold raw bytes, everything
        else holds gzip-compressed bytes. serve_grid_file sniffs the gzip
        magic instead of trusting metadata, so pre-WebP entries keep
        working with no migration.
        """
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
        found = self._bucket.find(
            {
                "metadata.url": url,
                "metadata.size": size,
                "metadata.kind": kind.value,
            }
        )
        for grid_out in found:
            return grid_out
        return None

    def get_media(self, file_id: Any) -> GridOut | None:
        try:
            return self._bucket.open_download_stream(file_id)
        except Exception:
            return None

    def cache_og_image(self, url: str) -> None:
        if self.get_file(url, 100, Kind.OG_IMAGE):
            return
        i = load(url, self.user_agent)
        # Save the thumbnail as WebP
        i.thumbnail((100, 100))
        self._store(_encode_image(i), url, 100, WEBP_MIMETYPE, Kind.OG_IMAGE)

    def cache_attachment(self, url: str) -> None:
        if self.get_file(url, None, Kind.ATTACHMENT) or self.get_file(url, 720, Kind.ATTACHMENT):
            return
        try:
            img = load(url, self.user_agent)
        except requests.HTTPError:
            raise
        except (ValueError, OSError):
            # Not a decodable image (UnidentifiedImageError is an OSError),
            # store the raw bytes instead.
            self._cache_generic_attachment(url)
            return
        # Save the original attachment as WebP
        self._store(_encode_image(img), url, None, WEBP_MIMETYPE, Kind.ATTACHMENT)
        # Save a thumbnail as WebP
        img.thumbnail((720, 720))
        self._store(_encode_image(img), url, 720, WEBP_MIMETYPE, Kind.ATTACHMENT)

    def _cache_generic_attachment(self, url: str) -> None:
        # The attachment is not an image, download and save it anyway
        # (capped: remote hosts are untrusted, never buffer unbounded bytes).
        with requests.get(url, stream=True, headers={"User-Agent": self.user_agent}) as resp:
            resp.raise_for_status()
            with BytesIO() as buf, GzipFile(mode="wb", fileobj=buf) as gzipped:
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        downloaded += len(chunk)
                        if downloaded > MAX_REMOTE_ATTACHMENT_BYTES:
                            raise ValueError(f"attachment over size cap: {url}")
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
        try:
            i = load(url, self.user_agent)
        except Exception:
            return
        for size in [50, 80]:
            t1 = i.copy()
            t1.thumbnail((size, size))
            self._store(_encode_image(t1), url, size, WEBP_MIMETYPE, Kind.ACTOR_ICON)

    def save_upload(self, obuf: BytesIO, filename: str, max_size: tuple[int, int]) -> StoredUpload:
        mtype = mimetypes.guess_type(filename)[0]
        if mtype and mtype.startswith("image"):
            # Re-encoding as WebP drops EXIF (after applying orientation),
            # so no separate EXIF-strip step is needed. The stored filename
            # takes a .webp extension to match the stored bytes.
            filename = _webp_filename(filename)
            obuf.seek(0)
            img = Image.open(obuf)
            img.load()
            if (img.width > max_size[0]) or (img.height > max_size[1]):
                img.thumbnail(size=max_size)
            raw = _encode_image(img)
            mtype = WEBP_MIMETYPE
        else:
            obuf.seek(0)
            with BytesIO() as gbuf, GzipFile(mode="wb", fileobj=gbuf) as gzipfile:
                gzipfile.write(obuf.getvalue())
            raw = gbuf.getvalue()

        oid = self._store(
            raw,
            filename,
            None,
            mtype,
            Kind.UPLOAD,
        )
        return StoredUpload(str(oid), filename, mtype)

    def cache_custom_emoji(self, url: str) -> None:
        if self.get_file(url, None, Kind.CUSTOM_EMOJI):
            return
        try:
            img = load(url, self.user_agent)
        except requests.HTTPError:
            raise
        except (ValueError, OSError):
            self._cache_generic_emoji(url)
            return
        except Exception:
            return
        if (img.width > 128) or (img.height > 128):
            img.thumbnail((128, 128))
        self._store(_encode_image(img), url, None, WEBP_MIMETYPE, Kind.CUSTOM_EMOJI)

    def _cache_generic_emoji(self, url: str) -> None:
        with requests.get(url, stream=True, headers={"User-Agent": self.user_agent}) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type") or mimetypes.guess_type(url)[0] or ""
            with BytesIO() as buf, GzipFile(mode="wb", fileobj=buf) as gzipped:
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        downloaded += len(chunk)
                        if downloaded > MAX_REMOTE_ATTACHMENT_BYTES:
                            raise ValueError(f"emoji over size cap: {url}")
                        gzipped.write(chunk)
            self._store(
                buf.getvalue(),
                url,
                None,
                content_type,
                Kind.CUSTOM_EMOJI,
            )

    def cache(self, url: str, kind: Kind) -> None:
        match kind:
            case Kind.ACTOR_ICON:
                self.cache_actor_icon(url)
            case Kind.OG_IMAGE:
                self.cache_og_image(url)
            case Kind.CUSTOM_EMOJI:
                self.cache_custom_emoji(url)
            case _:
                self.cache_attachment(url)

    def get_actor_icon(self, url: str, size: int) -> Any:
        return self.get_file(url, size, Kind.ACTOR_ICON)

    def get_attachment(self, url: str, size: int) -> Any:
        return self.get_file(url, size, Kind.ATTACHMENT)

    def get_custom_emoji(self, url: str) -> Any:
        return self.get_file(url, None, Kind.CUSTOM_EMOJI)
