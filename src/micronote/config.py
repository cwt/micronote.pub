import mimetypes
import os
import subprocess
import threading
from datetime import UTC, datetime
from enum import StrEnum
from functools import cache
from importlib.metadata import version as package_version
from pathlib import Path

import requests
import yaml
from active_boxes import strtobool
from active_boxes.activitypub import DEFAULT_CTX
from itsdangerous import URLSafeTimedSerializer
from neosqlite import ASCENDING, Connection

from micronote.utils.emoji import unicode_emojize
from micronote.utils.key import KEY_DIR, get_key, get_secret_key
from micronote.utils.media import MediaCache


class ThemeStyle(StrEnum):
    LIGHT = "light"
    DARK = "dark"


DEFAULT_THEME_STYLE = ThemeStyle.LIGHT.value

DEFAULT_THEME_PRIMARY_COLOR = {
    ThemeStyle.LIGHT: "#1d781d",  # Green
    ThemeStyle.DARK: "#33ff00",  # Purple
}


def noop():
    pass


CUSTOM_CACHE_HOOKS = False
try:
    from cache_hooks import purge as custom_cache_purge_hook
except ModuleNotFoundError:
    custom_cache_purge_hook = noop


def _detect_version() -> str:
    """Version string from the VCS checkout, or the installed package metadata."""
    if Path(".git").is_dir():
        command = ["git", "describe", "--always"]
    elif Path(".hg").is_dir():
        command = ["hg", "id", "-i"]
    else:
        return package_version("micronote-pub")

    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return result.stdout.split()[0]


@cache
def version() -> str:
    """VCS/package version, resolved on first use (never at import)."""
    try:
        return _detect_version()
    except Exception:
        return "-"


DEBUG_MODE = strtobool(os.getenv("MICRONOTE_DEBUG", "false"))

HEADERS = [
    "application/activity+json",
    "application/ld+json;profile=https://www.w3.org/ns/activitystreams",
    'application/ld+json; profile="https://www.w3.org/ns/activitystreams"',
    "application/ld+json",
]

with (KEY_DIR / "me.yml").open() as f:
    conf = yaml.safe_load(f)

USERNAME = conf["username"]
# :alias: shortcodes are converted to Unicode once here, so the served
# actor JSON, the manifest and the page titles all carry real characters
# (remote nodes cannot resolve our shortcodes).
NAME = unicode_emojize(conf["name"])
DOMAIN = conf["domain"]
SCHEME = "https" if conf.get("https", True) else "http"
BASE_URL = f"{SCHEME}://{DOMAIN}"
ID = BASE_URL
SUMMARY = unicode_emojize(conf["summary"])
ICON_URL = conf["icon_url"]
PASS = conf["pass"]
EXTRA_INBOXES = conf.get("extra_inboxes", [])

HIDE_FOLLOWING = conf.get("hide_following", True)

# Theme-related config
theme_conf = conf.get("theme", {})
THEME_STYLE = ThemeStyle(theme_conf.get("style", DEFAULT_THEME_STYLE))
THEME_COLOR = theme_conf.get("color", DEFAULT_THEME_PRIMARY_COLOR[THEME_STYLE])
TIMEZONE = int(conf.get("timezone_hours", 0))
CDN_URL = conf.get("cdn_url", "")
IMAGE_MAX_SIZE = (conf.get("image_max_size", {}).get("width", 1920), conf.get("image_max_size", {}).get("height", 1920))


@cache
def user_agent() -> str:
    return f"{requests.utils.default_user_agent()} (micronote.pub/{version()}; +{BASE_URL})"


DATA_DIR = Path(os.getenv("MICRONOTE_DATA_DIR", os.path.abspath("data")))


def _db_path(db_name: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{db_name}.db"


_DB_CONNECTION = threading.local()


def create_db_connection():
    """Thread-local NeoSQLite connection (one SQLite file, WAL mode).

    SQLite handles are pinned to their creating thread, so each thread
    gets its own connection to the same file.
    """
    if getattr(_DB_CONNECTION, "connection", None) is None:
        ttl_sweep_env = os.getenv("MICRONOTE_TTL_SWEEP_INTERVAL_S")
        ttl_sweep = int(ttl_sweep_env) if ttl_sweep_env else None
        _DB_CONNECTION.connection = Connection(
            _db_path(DB_NAME),
            journal_mode=os.getenv("MICRONOTE_JOURNAL_MODE", "WAL"),
            ttl_sweep_interval_s=ttl_sweep,
        )
    return _DB_CONNECTION.connection


def close_db_connection():
    """Closes and unbinds the SQLite connection for the current thread."""
    conn = getattr(_DB_CONNECTION, "connection", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _DB_CONNECTION.connection = None


def create_db_client(db_name):
    return _ThreadLocalDB(db_name)


class _ThreadLocalDB:
    """Proxy exposing collections from the calling thread's connection.

    Attribute access (e.g. `DB.activities`) resolves against the current
    thread's connection on every use, so module-level `DB` imports stay
    safe in threaded servers.
    """

    def __init__(self, db_name):
        self._db_name = db_name

    def __getattr__(self, name):
        return getattr(create_db_connection().get_database(self._db_name), name)


DB_NAME = f"{USERNAME}_{DOMAIN.replace('.', '_').replace(':', '_')}"
DB = create_db_client(DB_NAME)
MEDIA_CACHE = MediaCache(create_db_connection, user_agent)


def create_indexes():
    DB.activities.create_index([("remote_id", ASCENDING)])
    DB.activities.create_index([("activity.object.id", ASCENDING)])
    DB.activities.create_index(
        [
            ("activity.object.id", ASCENDING),
            ("meta.deleted", ASCENDING),
        ]
    )
    DB.cache2.create_index([("path", ASCENDING), ("type", ASCENDING), ("arg", ASCENDING)])
    DB.cache2.create_index("date", expireAfterSeconds=3600 * 12)

    # Index for the block query
    DB.activities.create_index(
        [
            ("box", ASCENDING),
            ("type", ASCENDING),
            ("meta.undo", ASCENDING),
        ]
    )

    # Index for count queries
    DB.activities.create_index(
        [
            ("box", ASCENDING),
            ("type", ASCENDING),
            ("meta.undo", ASCENDING),
            ("meta.deleted", ASCENDING),
        ]
    )

    DB.activities.create_index(
        [
            ("type", ASCENDING),
            ("activity.object.type", ASCENDING),
            ("activity.object.inReplyTo", ASCENDING),
            ("meta.deleted", ASCENDING),
        ]
    )


def drop_db():
    if not DEBUG_MODE:
        return

    create_db_connection().drop_database(DB_NAME)


@cache
def key():
    """The instance RSA key, generated on first use (never at import)."""
    return get_key(ID, USERNAME, DOMAIN)


@cache
def jwt() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_secret_key("jwt"))


def _admin_jwt_token() -> str:
    return jwt().dumps({"me": "ADMIN", "ts": datetime.now(UTC).timestamp()})


@cache
def admin_api_key() -> str:
    return get_secret_key("admin_api_key", _admin_jwt_token)


@cache
def flask_secret_key() -> str:
    return get_secret_key("flask")


@cache
def me() -> dict:
    """The local actor document, built on first use."""
    return {
        "@context": DEFAULT_CTX,
        "type": "Person",
        "id": ID,
        "following": f"{ID}/following",
        "followers": f"{ID}/followers",
        "featured": f"{ID}/featured",
        "liked": f"{ID}/liked",
        "inbox": f"{ID}/inbox",
        "outbox": f"{ID}/outbox",
        "preferredUsername": USERNAME,
        "name": NAME,
        "summary": SUMMARY,
        "endpoints": {},
        "url": ID,
        "manuallyApprovesFollowers": False,
        "attachment": [],
        "icon": {
            "mediaType": mimetypes.guess_type(ICON_URL)[0],
            "type": "Image",
            "url": ICON_URL,
        },
        "publicKey": key().to_dict(),
    }


def __getattr__(name):
    """Lazy compatibility aliases: Jinja templates use `config.ME.*`."""
    if name == "VERSION":
        return version()
    if name == "KEY":
        return key()
    if name == "ME":
        return me()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
