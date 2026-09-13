from datetime import datetime
from enum import Enum
import mimetypes
import os
import subprocess
import threading

from itsdangerous import URLSafeTimedSerializer
from little_boxes import strtobool
from little_boxes.activitypub import DEFAULT_CTX
from neosqlite import ASCENDING
from neosqlite import Connection
import requests
import sass
import yaml

from utils.key import KEY_DIR
from utils.key import get_key
from utils.key import get_secret_key
from utils.media import MediaCache


class ThemeStyle(Enum):
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

try:
    if os.path.isdir('.git'):
        VERSION = (
            subprocess.check_output(
                ["git", "describe", "--always"]
            ).split()[0].decode("utf-8")
        )
    elif os.path.isdir('.hg'):
        VERSION = (
            subprocess.check_output(
                ["hg", "id", "-i"]
            ).split()[0].decode("utf-8")
        )
except:
    VERSION = "-"

DEBUG_MODE = strtobool(os.getenv("MICROBLOGPUB_DEBUG", "false"))

HEADERS = [
    "application/activity+json",
    "application/ld+json;profile=https://www.w3.org/ns/activitystreams",
    'application/ld+json; profile="https://www.w3.org/ns/activitystreams"',
    "application/ld+json",
]

with open(os.path.join(KEY_DIR, "me.yml")) as f:
    conf = yaml.load(f, Loader=yaml.FullLoader)

    USERNAME = conf["username"]
    NAME = conf["name"]
    DOMAIN = conf["domain"]
    SCHEME = "https" if conf.get("https", True) else "http"
    BASE_URL = SCHEME + "://" + DOMAIN
    ID = BASE_URL
    SUMMARY = conf["summary"]
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
    YANDEX_TRANSLATE_API = conf.get("yandex_translate_api_key", "")
    NO_TRANSLATE = conf.get("no_translate", [])
    TARGET_LANG = conf.get("target_lang", "en")
    SIMILARITY_THRESHOLD = conf.get("similarity_threshold", 94)
    IMAGE_MAX_SIZE = (
        conf.get("image_max_size", {}).get("width", 1920),
        conf.get("image_max_size", {}).get("height", 1920)
    )

SASS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sass")
theme_css = f"$primary-color: {THEME_COLOR};\n"
with open(os.path.join(SASS_DIR, f"{THEME_STYLE.value}.scss")) as f:
    theme_css += f.read()
    theme_css += "\n"
with open(os.path.join(SASS_DIR, "base_theme.scss")) as f:
    raw_css = theme_css + f.read()
    CSS = sass.compile(string=raw_css, output_style="compressed")

USER_AGENT = (
    f"{requests.utils.default_user_agent()} (microblog.pub/{VERSION}; +{BASE_URL})"
)


def _db_path(db_name):
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, f"{db_name}.db")


_DB_CONNECTION = threading.local()


def create_db_connection():
    """Thread-local NeoSQLite connection (one SQLite file, WAL mode).

    SQLite handles are pinned to their creating thread, so each thread
    gets its own connection to the same file.
    """
    if getattr(_DB_CONNECTION, "connection", None) is None:
        _DB_CONNECTION.connection = Connection(
            _db_path(DB_NAME),
            journal_mode=os.getenv("MICRONOTE_JOURNAL_MODE", "WAL"),
            ttl_sweep_interval_s=int(os.getenv("MICRONOTE_TTL_SWEEP_INTERVAL_S", "60")),
        )
    return _DB_CONNECTION.connection


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


DB_NAME = "{}_{}".format(USERNAME, DOMAIN.replace(".", "_").replace(":", "_"))
DB = create_db_client(DB_NAME)
MEDIA_CACHE = MediaCache(create_db_connection, USER_AGENT)


def create_indexes():
    DB.activities.create_index([("remote_id", ASCENDING)])
    DB.activities.create_index([("activity.object.id", ASCENDING)])
    DB.activities.create_index([
        ("activity.object.id", ASCENDING),
        ("meta.deleted", ASCENDING),
    ])
    DB.cache2.create_index([("path", ASCENDING), ("type", ASCENDING), ("arg", ASCENDING)])
    DB.cache2.create_index("date", expireAfterSeconds=3600 * 12)
    DB.translate.create_index([("hash", ASCENDING), ("target_lang", ASCENDING)])

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


def _drop_db():
    if not DEBUG_MODE:
        return

    create_db_connection().drop_database(DB_NAME)


KEY = get_key(ID, USERNAME, DOMAIN)

JWT_SECRET = get_secret_key("jwt")
JWT = URLSafeTimedSerializer(JWT_SECRET)


def _admin_jwt_token() -> str:
    return JWT.dumps(# type: ignore
        {"me": "ADMIN", "ts": datetime.now().timestamp()}
    )


ADMIN_API_KEY = get_secret_key("admin_api_key", _admin_jwt_token)

ME = {
    "@context": DEFAULT_CTX,
    "type": "Person",
    "id": ID,
    "following": ID + "/following",
    "followers": ID + "/followers",
    "featured": ID + "/featured",
    "liked": ID + "/liked",
    "inbox": ID + "/inbox",
    "outbox": ID + "/outbox",
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
    "publicKey": KEY.to_dict(),
}
