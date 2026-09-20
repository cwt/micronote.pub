"""Media URL resolution: cache lookups and on-demand cache job enqueueing.

This is the only module that reads the media cache and enqueues media
cache jobs; template filters are thin wrappers over these functions.
"""

import logging

from cachetools import TTLCache

from micronote.config import CDN_URL, DB, MEDIA_CACHE
from micronote.jobs import enqueue_job
from micronote.utils.media import Kind

log = logging.getLogger(__name__)

_GRIDFS_CACHE: TTLCache[tuple[Kind, str, int | None], str] = TTLCache(maxsize=4096, ttl=3600)
_PENDING_CACHE_JOBS: TTLCache[tuple[Kind, str], bool] = TTLCache(maxsize=4096, ttl=300)


def _public_media_url(path: str) -> str:
    return f"{CDN_URL}{path}" if path.startswith("/") else path


def enqueue_media_cache(url: str, kind: Kind) -> None:
    if not url or not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
        return

    cache_key = (kind, url)
    if cache_key in _PENDING_CACHE_JOBS:
        return

    _PENDING_CACHE_JOBS[cache_key] = True

    try:
        existing = DB.jobs.find_one(
            {
                "type": "cache_media_item",
                "iri": url,
                "payload.kind": kind.value,
                "status": {"$in": ["pending", "processing"]},
            }
        )
        if existing:
            return

        enqueue_job("cache_media_item", iri=url, payload={"kind": kind.value})
    except Exception as exc:
        log.warning(f"failed to enqueue media cache for {url}: {exc}")


def get_file_url(url, size, kind):
    if not url:
        return ""
    k = (kind, url, size)
    cached = _GRIDFS_CACHE.get(k)
    if cached:
        return _public_media_url(cached)

    doc = MEDIA_CACHE.get_file(url, size, kind)
    if doc:
        u = f"/media/{doc._id}"
        _GRIDFS_CACHE[k] = u
        return _public_media_url(u)

    enqueue_media_cache(url, kind)
    log.info(f"cache not available for {url}/{size}/{kind}; enqueued background caching")
    return url


def actor_icon_url(url, size):
    return get_file_url(url, size, Kind.ACTOR_ICON)


def attachment_url(url, size):
    return get_file_url(url, size, Kind.ATTACHMENT)


def og_image_url(url, size=100):
    try:
        return get_file_url(url, size, Kind.OG_IMAGE)
    except Exception:
        return ""


def custom_emoji_url(url):
    try:
        return get_file_url(url, None, Kind.CUSTOM_EMOJI)
    except Exception:
        return url
