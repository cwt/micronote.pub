import hashlib
import re
import urllib
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import bleach
import flask
import timeago
from active_boxes import activitypub as ap
from active_boxes.activitypub import _to_list, get_backend
from active_boxes.errors import ActivityGoneError, ActivityNotFoundError
from bleach.sanitizer import ALLOWED_ATTRIBUTES as BLEACH_DEFAULT_ATTRIBUTES
from cachetools import TTLCache
from dateutil import parser
from flask import current_app
from html2text import html2text
from neosqlite.objectid import ObjectId

from micronote.config import CDN_URL, DB, ID, MEDIA_CACHE, TIMEZONE
from micronote.utils.emoji import extract_custom_emojis, render_custom_emojis, render_custom_emojis_in_html
from micronote.utils.highlight import highlight_code_blocks
from micronote.utils.media import Kind

blueprint = flask.Blueprint("filters", __name__, template_folder="templates")

_GRIDFS_CACHE: TTLCache[tuple[Kind, str, int | None], str] = TTLCache(maxsize=4096, ttl=3600)
_PENDING_CACHE_JOBS: TTLCache[tuple[Kind, str], bool] = TTLCache(maxsize=4096, ttl=300)

# HTML/templates helper
ALLOWED_TAGS = [
    "a",
    "abbr",
    "acronym",
    "b",
    "br",
    "blockquote",
    "code",
    "pre",
    "em",
    "i",
    "li",
    "ol",
    "strong",
    "ul",
    "span",
    "div",
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
]

_CODE_CLASS_RE = re.compile(r"^language-[\w+.#-]+$")


def _allow_code_class(tag, attr, value):
    return all(_CODE_CLASS_RE.match(part) for part in value.split())


ALLOWED_ATTRIBUTES = {
    **BLEACH_DEFAULT_ATTRIBUTES,
    "code": _allow_code_class,
}


def _clean_html(html):
    try:
        return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
    except Exception:
        return ""


def _public_media_url(path: str) -> str:
    return f"{CDN_URL}{path}" if path.startswith("/") else path


def _enqueue_media_cache(url: str, kind: Kind) -> None:
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
                "status": {"$in": ["pending", "processing"]},
            }
        )
        if existing:
            return

        from micronote.tasks import enqueue_job

        enqueue_job("cache_media_item", iri=url, payload={"kind": kind.value})
    except Exception as exc:
        try:
            current_app.logger.warning(f"failed to enqueue media cache for {url}: {exc}")
        except Exception:
            pass


def _get_file_url(url, size, kind):
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

    _enqueue_media_cache(url, kind)
    try:
        current_app.logger.info(f"cache not available for {url}/{size}/{kind}; enqueued background caching")
    except Exception:
        pass
    return url


@blueprint.app_template_filter()
def remove_mongo_id(dat):
    if isinstance(dat, list):
        return [remove_mongo_id(item) for item in dat]
    if "_id" in dat:
        dat["_id"] = str(dat["_id"])
    for k, v in dat.items():
        if isinstance(v, dict):
            dat[k] = remove_mongo_id(dat[k])
    return dat


@blueprint.app_template_filter()
def get_video_link(data):
    for link in data:
        if link.get("mimeType", "").startswith("video/"):
            return link.get("href")
    return None


@blueprint.app_template_filter()
def get_actor_icon_url(url, size):
    return _get_file_url(url, size, Kind.ACTOR_ICON)


@blueprint.app_template_filter()
def get_attachment_url(url, size):
    return _get_file_url(url, size, Kind.ATTACHMENT)


@blueprint.app_template_filter()
def get_og_image_url(url, size=100):
    try:
        return _get_file_url(url, size, Kind.OG_IMAGE)
    except Exception:
        return ""


@blueprint.app_template_filter()
def get_custom_emoji_url(url):
    try:
        return _get_file_url(url, None, Kind.CUSTOM_EMOJI)
    except Exception:
        return url


@blueprint.app_template_filter()
def permalink_id(val):
    if not val:
        return ""
    return hashlib.sha256(str(val).encode("utf-8")).hexdigest()[:12]


@blueprint.app_template_filter()
def quote_plus(t):
    return urllib.parse.quote_plus(t)


@blueprint.app_template_filter()
def is_from_outbox(t):
    return t.startswith(ID)


@blueprint.app_template_filter()
def clean(html):
    return _clean_html(html)


def _cached_actor_emojis(actor_id) -> dict[str, str]:
    """Custom emojis for an actor from the local actor cache (no network)."""
    if not actor_id or not isinstance(actor_id, str):
        return {}
    try:
        doc = DB.actors.find_one({"remote_id": actor_id})
    except Exception:
        return {}
    if not doc:
        return {}
    return extract_custom_emojis((doc.get("data") or {}).get("tag"))


@blueprint.app_template_filter()
def emojize(html, obj=None, actor=None):
    if not html:
        return ""

    emojis: dict[str, str] = {}
    if actor and isinstance(actor, dict):
        emojis.update(actor.get("emojis") or extract_custom_emojis(actor.get("tag")))
        if not emojis and actor.get("id"):
            emojis.update(_cached_actor_emojis(actor.get("id")))

    if obj and isinstance(obj, dict):
        if obj.get("attributedTo") and not emojis:
            attr = obj.get("attributedTo")
            attr_id = attr if isinstance(attr, str) else attr.get("id") if isinstance(attr, dict) else None
            if attr_id:
                emojis.update(_cached_actor_emojis(attr_id))
        obj_emojis = obj.get("emojis") or extract_custom_emojis(obj.get("tag"))
        if obj_emojis:
            emojis.update(obj_emojis)
    elif isinstance(obj, list):
        emojis.update(extract_custom_emojis(obj))

    return render_custom_emojis_in_html(html, emojis, url_resolver=get_custom_emoji_url)


@blueprint.app_template_filter()
def display_name(actor):
    """Renders an actor's display name, with custom emojis as images.

    Emoji data comes from the stored actor (or its full fetched form);
    older stored actors fall back to the local actor cache. Unknown
    shortcodes are left as-is, so output is always safe to render.
    """
    if not isinstance(actor, dict):
        return ""
    emojis = actor.get("emojis") or extract_custom_emojis(actor.get("tag"))
    if not emojis:
        emojis = _cached_actor_emojis(actor.get("id"))
    return render_custom_emojis(
        actor.get("name") or actor.get("preferredUsername") or "",
        emojis,
        url_resolver=get_custom_emoji_url,
    )


@blueprint.app_template_filter()
def highlight_code(html):
    return highlight_code_blocks(html)


@blueprint.app_template_filter()
def html2plaintext(body):
    return html2text(body)


@blueprint.app_template_filter()
def domain(url):
    return urlparse(url).netloc


@blueprint.app_template_filter()
def url_or_id(d):
    match d:
        case {"url": str(url)}:
            return url
        case {"id": id_val}:
            return id_val
        case _:
            return ""


@blueprint.app_template_filter()
def get_url(u):
    current_app.logger.debug(f"GET_URL({u!r})")
    if isinstance(u, list):
        for link in u:
            if isinstance(link, dict) and link.get("mimeType") == "text/html":
                u = link
                break
    match u:
        case {"href": href}:
            return href
        case _:
            return u


@blueprint.app_template_filter()
def get_actor(url):
    if not url:
        return None
    match url:
        case [first, *_]:
            url = first
        case {"id": _, "type": str(_)}:
            return url
        case {"id": _, "preferredUsername": str(_)}:
            return url
        case {"id": actor_id}:
            url = actor_id

    if not isinstance(url, str):
        return None

    try:
        doc = DB.actors.find_one({"remote_id": url})
        if doc and doc.get("data"):
            return doc["data"]
    except Exception:
        pass

    current_app.logger.debug(f"GET_ACTOR {url}")
    try:
        data = get_backend().fetch_iri_sync(url)
        if isinstance(data, dict):
            try:
                DB.actors.update_one(
                    {"remote_id": url},
                    {"$set": {"remote_id": url, "data": data}},
                    upsert=True,
                )
            except Exception:
                pass
        return data
    except (ActivityNotFoundError, ActivityGoneError):
        return f"Deleted<{url}>"
    except Exception as exc:
        return f"Error<{url}/{exc!r}>"


@blueprint.app_template_filter()
def format_time(val):
    if val:
        dt = val if isinstance(val, datetime) else parser.parse(val)
        tz = timedelta(hours=TIMEZONE)
        if TIMEZONE == 0:
            tz_name = " UTC"
        elif TIMEZONE > 0:
            tz_name = f" GMT+{TIMEZONE}"
        else:
            tz_name = f" GMT{TIMEZONE}"
        return (dt + tz).strftime("%b %d, %Y, %H:%M:%S") + tz_name
    return val


@blueprint.app_template_filter()
def event_time(doc):
    """Best-effort event time for an activity doc.

    Prefers the activity's published date; inbox records like Follow
    rarely carry one, so falls back to the document insertion time.
    """
    if not isinstance(doc, dict):
        return None
    published = (doc.get("activity") or {}).get("published")
    if published:
        return published
    try:
        return datetime.fromtimestamp(ObjectId(str(doc["_id"])).generation_time(), UTC)
    except Exception:
        return None


@blueprint.app_template_filter()
def format_timeago(val):
    if val:
        dt = val if isinstance(val, datetime) else parser.parse(val)
        return timeago.format(dt, datetime.now(UTC))
    return val


@blueprint.app_template_filter()
def has_type(doc, _types):
    doc_types = _to_list(doc["type"])
    return any(_type in doc_types for _type in _to_list(_types))


@blueprint.app_template_filter()
def has_actor_type(doc):
    return any(has_type(doc, t.value) for t in ap.ACTOR_TYPES)


def _is_img(filename: str) -> bool:
    return filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg"))


@blueprint.app_template_filter()
def not_only_imgs(attachment):
    for a in attachment:
        match a:
            case {"url": url} if not _is_img(url):
                return True
            case str() if not _is_img(a):
                return True
    return False


@blueprint.app_template_filter()
def is_img(filename):
    return _is_img(filename)
