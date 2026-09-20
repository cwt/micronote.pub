import json
import logging
import mimetypes
import os
import traceback
from datetime import UTC, datetime, timedelta
from typing import Any

from active_boxes import activitypub as ap
from active_boxes.activitypub import ActivityType, _to_list, clean_activity, get_backend
from active_boxes.errors import ActivityGoneError, Error
from active_boxes.httpsig import verify_request_sync
from active_boxes.webfinger import get_actor_url_sync, get_remote_follow_template_sync
from flask import Flask, Response, abort, redirect, render_template, request, send_from_directory, session, url_for
from flask import jsonify as flask_jsonify
from flask_wtf.csrf import CSRFProtect
from itsdangerous import BadSignature

from micronote import activitypub, admin, api, config, feeds, filters, indieauth, tasks
from micronote.activitypub import Box, embed_collection
from micronote.config import (
    BASE_URL,
    CDN_URL,
    DB,
    DOMAIN,
    HEADERS,
    ICON_URL,
    ID,
    KEY,
    ME,
    MEDIA_CACHE,
    NAME,
    SCHEME,
    SUMMARY,
    THEME_COLOR,
    USERNAME,
    VERSION,
)
from micronote.utils.headers import noindex
from micronote.utils.key import get_secret_key
from micronote.utils.login import login_required
from micronote.utils.query import paginated_query
from micronote.utils.thread import _build_thread

back = activitypub.MicroblogPubBackend()
ap.use_backend(back)

MY_PERSON = ap.Person(**ME)

app = Flask(__name__)
app.register_blueprint(admin.blueprint)
app.register_blueprint(api.blueprint)
app.register_blueprint(feeds.blueprint)
app.register_blueprint(filters.blueprint)
app.register_blueprint(indieauth.blueprint)
app.secret_key = get_secret_key("flask")
app.config.update(
    WTF_CSRF_CHECK_DEFAULT=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SCHEME == "https",
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True
csrf = CSRFProtect(app)

logger = logging.getLogger(__name__)

# Hook up Flask logging with gunicorn
root_logger = logging.getLogger()
if os.getenv("FLASK_DEBUG"):
    logger.setLevel(logging.DEBUG)
    root_logger.setLevel(logging.DEBUG)
else:
    gunicorn_logger = logging.getLogger("gunicorn.error")
    root_logger.handlers = gunicorn_logger.handlers
    root_logger.setLevel(gunicorn_logger.level)

# active_boxes logs a full traceback at ERROR for every failed fetch, including
# the expected 404 when a handle cannot be resolved. That drowns the logs, so
# silence its logger here. Our app handles lookup failures in the UI instead.
logging.getLogger("active_boxes").setLevel(logging.CRITICAL)


@app.context_processor
def inject_config():
    q = {
        "type": "Create",
        "activity.object.type": "Note",
        "activity.object.inReplyTo": None,
        "meta.deleted": False,
    }
    notes_count = DB.activities.count_documents(
        {"box": Box.OUTBOX.value, "$or": [q, {"type": "Announce", "meta.undo": False}]}
    )
    q = {"type": "Create", "activity.object.type": "Note", "meta.deleted": False}
    with_replies_count = DB.activities.count_documents(
        {"box": Box.OUTBOX.value, "$or": [q, {"type": "Announce", "meta.undo": False}]}
    )
    liked_count = DB.activities.count_documents(
        {
            "box": Box.OUTBOX.value,
            "meta.deleted": False,
            "meta.undo": False,
            "type": ActivityType.LIKE.value,
        }
    )
    followers_q = {
        "box": Box.INBOX.value,
        "type": ActivityType.FOLLOW.value,
        "meta.undo": False,
    }
    following_q = {
        "box": Box.OUTBOX.value,
        "type": ActivityType.FOLLOW.value,
        "meta.undo": False,
    }

    return {
        "micronote_version": VERSION,
        "config": config,
        "logged_in": session.get("logged_in", False),
        "followers_count": DB.activities.count_documents(followers_q),
        "following_count": DB.activities.count_documents(following_q),
        "notes_count": notes_count,
        "liked_count": liked_count,
        "with_replies_count": with_replies_count,
        "me": ME,
    }


@app.after_request
def set_x_powered_by(response):
    response.headers["X-Powered-By"] = "micronote.pub"
    if (
        request.path == "/login"
        or request.path.startswith(("/admin", "/indieauth", "/token"))
        or (request.path.startswith("/api/") and session.get("logged_in"))
    ):
        # Private, account-specific responses must never sit in a browser
        # or shared (proxy/CDN) cache.
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def jsonify(**data):
    if "@context" not in data:
        data["@context"] = config.DEFAULT_CTX
    return Response(
        response=activitypub.json_dumps(data),
        headers={"Content-Type": "application/json" if app.debug else "application/activity+json"},
    )


def is_api_request() -> bool:
    h = request.headers.get("Accept")
    if h is None:
        return False
    media_type = h.split(",")[0]
    return media_type in HEADERS or media_type == "application/json"


def wants_html() -> bool:
    """True when the client sent a browser-like Accept header."""
    return "text/html" in request.headers.get("Accept", "")


@app.errorhandler(ValueError)
def handle_value_error(error):
    logger.error(f"caught value error: {error!r}, {traceback.format_tb(error.__traceback__)}")
    message = error.args[0] if error.args else "invalid request"
    if wants_html():
        return render_template("error.html", message=message), 400
    response = flask_jsonify(message=message)
    response.status_code = 400
    return response


@app.errorhandler(Error)
def handle_activitypub_error(error):
    logger.error(f"caught activitypub error {error!r}, {traceback.format_tb(error.__traceback__)}")
    status_code = getattr(error, "status_code", 400)
    if wants_html():
        message = getattr(error, "message", "") or str(error) or error.__class__.__name__
        return render_template("error.html", message=message), status_code
    to_dict = getattr(error, "to_dict", None)
    payload = to_dict() if callable(to_dict) else {"error": str(error) or error.__class__.__name__}
    response = flask_jsonify(payload)
    response.status_code = status_code
    return response


@app.errorhandler(500)
def handle_500(e):
    return render_template("500.html"), 500


# @app.errorhandler(Exception)
# def handle_other_error(error):
#    logger.error(
#        f"caught error {error!r}, {traceback.format_tb(error.__traceback__)}"
#    )
#    response = flask_jsonify({})
#    response.status_code = 500
#    return response

# App migrations


ROBOTS_TXT = """User-agent: *
Disallow: /login
Disallow: /admin/
Disallow: /static/
Disallow: /media/
Disallow: /uploads/"""


@app.route("/robots.txt")
def robots_txt():
    return Response(response=ROBOTS_TXT, headers={"Content-Type": "text/plain"})


GZIP_MAGIC = b"\x1f\x8b"


def serve_grid_file(grid_out):
    data = grid_out.read()
    upload_date = grid_out.upload_date
    try:
        parsed_date = datetime.fromisoformat(upload_date)
        last_modified = parsed_date.strftime("%a, %d %b %Y %H:%M:%S GMT")
    except (TypeError, ValueError):
        last_modified = upload_date
    content_type = (grid_out.metadata or {}).get("content_type") or "application/octet-stream"
    resp = app.response_class(data, mimetype=content_type)
    resp.headers.set("Content-Length", len(data))
    resp.headers.set("ETag", grid_out.md5)
    resp.headers.set("Last-Modified", last_modified)
    resp.headers.set("Cache-Control", "public,max-age=31536000,immutable")
    resp.headers.set("X-Content-Type-Options", "nosniff")
    if data[:2] == GZIP_MAGIC:
        # Legacy entries and non-image blobs are gzip-compressed;
        # WebP entries are stored raw.
        resp.headers.set("Content-Encoding", "gzip")
    return resp


@app.route("/media/<media_id>")
@noindex
def serve_media(media_id):
    grid_out = MEDIA_CACHE.get_media(media_id)
    if grid_out is None:
        abort(404)
    return serve_grid_file(grid_out)


@app.route("/uploads/<oid>/<fname>")
def serve_uploads(oid, fname):
    grid_out = MEDIA_CACHE.get_media(oid)
    if grid_out is None:
        abort(404)
    return serve_grid_file(grid_out)


@app.route("/remote_follow", methods=["GET", "POST"])
def remote_follow():
    if request.method == "GET":
        return render_template("remote_follow.html")

    csrf.protect()
    profile = (request.form.get("profile") or "").strip()
    if not profile:
        abort(400)
    if not profile.startswith("@"):
        profile = f"@{profile}"
    template = get_remote_follow_template_sync(profile)
    if not template:
        abort(404)
    return redirect(template.format(uri=f"{USERNAME}@{DOMAIN}"))


@app.route("/authorize_follow", methods=["GET", "POST"])
@login_required
def authorize_follow():
    if request.method == "GET":
        return render_template("authorize_remote_follow.html", profile=request.args.get("profile"))

    csrf.protect()
    profile = request.form.get("profile")
    actor = get_actor_url_sync(profile) if profile else None
    if not actor:
        abort(404)

    q = {
        "box": Box.OUTBOX.value,
        "type": ActivityType.FOLLOW.value,
        "meta.undo": False,
        "activity.object": actor,
    }
    if DB.activities.count_documents(q) > 0:
        return redirect("/following")

    follow = ap.Follow(actor=MY_PERSON.id, object=actor)
    tasks.post_to_outbox(follow)

    return redirect("/following")


@app.route("/webauthn/register", methods=["GET", "POST"])
@login_required
def webauthn_register():
    from fido2.webauthn import PublicKeyCredentialUserEntity

    from micronote.utils.webauthn import (
        clear_state,
        credential_options,
        get_server,
        load_state,
        save_credential,
        save_state,
        stored_credentials,
    )

    server = get_server()
    if request.method == "GET":
        user = PublicKeyCredentialUserEntity(id=b"admin", name=USERNAME)
        options, state = server.register_begin(user, credentials=stored_credentials())
        save_state("register", state)
        return render_template("webauthn_register.html", options=credential_options(options))

    csrf.protect()
    raw_cred = request.form.get("credential")
    state = load_state("register")
    if not raw_cred or not state:
        abort(400)
    try:
        credential = json.loads(raw_cred)
        auth_data = server.register_complete(state, credential)
    except (ValueError, json.JSONDecodeError, Exception):
        abort(400)
    finally:
        clear_state("register")
    save_credential(auth_data)
    return redirect("/admin")


#######
# Activity pub migrations
@app.route("/drop_cache")
@login_required
def drop_cache():
    DB.actors.drop()
    DB.cache2.delete_many({})
    return "Done"


CACHING = True


def _get_cached(type_="html", arg=None):
    if not CACHING:
        return None
    logged_in = session.get("logged_in")
    if not logged_in:
        cached = DB.cache2.find_one({"path": request.path, "type": type_, "arg": arg})
        if cached:
            app.logger.info("from cache")
            return cached["response_data"]
    return None


def _cache(resp, type_="html", arg=None):
    if not CACHING:
        return
    logged_in = session.get("logged_in")
    if not logged_in:
        DB.cache2.update_one(
            {"path": request.path, "type": type_, "arg": arg},
            {"$set": {"response_data": resp, "date": datetime.now(UTC)}},
            upsert=True,
        )


@app.route("/")
def index():
    if is_api_request():
        return jsonify(**ME)
    cache_arg = f"{request.args.get('older_than', '')}:{request.args.get('newer_than', '')}"
    cached = _get_cached("html", cache_arg)
    if cached:
        return cached

    q = {
        "box": Box.OUTBOX.value,
        "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
        "activity.object.inReplyTo": None,
        "meta.deleted": False,
        "meta.undo": False,
        "$or": [{"meta.pinned": False}, {"meta.pinned": {"$exists": False}}],
    }

    pinned = []
    # Only fetch the pinned notes if we're on the first page
    if not request.args.get("older_than") and not request.args.get("newer_than"):
        q_pinned = {
            "box": Box.OUTBOX.value,
            "type": ActivityType.CREATE.value,
            "meta.deleted": False,
            "meta.undo": False,
            "meta.pinned": True,
        }
        pinned = list(DB.activities.find(q_pinned))

    outbox_data, older_than, newer_than = paginated_query(DB.activities, q, limit=25 - len(pinned))

    resp = render_template(
        "index.html",
        outbox_data=outbox_data,
        older_than=older_than,
        newer_than=newer_than,
        pinned=pinned,
    )
    _cache(resp, "html", cache_arg)
    return resp


@app.route("/with_replies")
@login_required
def with_replies():
    q = {
        "box": Box.OUTBOX.value,
        "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
        "meta.deleted": False,
        "meta.undo": False,
    }
    outbox_data, older_than, newer_than = paginated_query(DB.activities, q)

    return render_template(
        "index.html",
        outbox_data=outbox_data,
        older_than=older_than,
        newer_than=newer_than,
    )


def _collect_actors(note_data: dict[str, Any], activity_type: ActivityType) -> list[dict]:
    """Collects the cached actors of Like/Announce activities targeting the note."""
    object_id = note_data["activity"]["object"]["id"]
    docs = DB.activities.find(
        {
            "meta.undo": False,
            "meta.deleted": False,
            "type": activity_type.value,
            "$or": [
                # FIXME(tsileo): remove all the useless $or
                {"activity.object.id": object_id},
                {"activity.object": object_id},
            ],
        }
    )
    actors = []
    for doc in docs:
        try:
            actors.append(doc["meta"]["actor"])
        except Exception:
            app.logger.exception(f"invalid doc: {doc!r}")
    return actors


@app.route("/note/<note_id>")
def note_by_id(note_id):
    if is_api_request():
        return redirect(url_for("outbox_activity", item_id=note_id))

    data = DB.activities.find_one({"box": Box.OUTBOX.value, "remote_id": back.activity_url(note_id)})
    if not data:
        abort(404)
    if data["meta"].get("deleted", False):
        abort(410)
    thread = _build_thread(data)
    app.logger.info(f"thread={thread!r}")

    likes = _collect_actors(data, ActivityType.LIKE)
    app.logger.info(f"likes={likes!r}")
    shares = _collect_actors(data, ActivityType.ANNOUNCE)
    app.logger.info(f"shares={shares!r}")

    return render_template("note.html", likes=likes, shares=shares, thread=thread, note=data)


@app.route("/nodeinfo")
def nodeinfo():
    response = _get_cached("api")
    cached = True
    if not response:
        cached = False
        q = {
            "box": Box.OUTBOX.value,
            "meta.deleted": False,  # TODO(tsileo): retrieve deleted and expose tombstone
            "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
        }

        response = json.dumps(
            {
                "version": "2.0",
                "software": {
                    "name": "micronote.pub",
                    "version": f"micronote.pub {VERSION}",
                },
                "protocols": ["activitypub"],
                "services": {"inbound": [], "outbound": []},
                "openRegistrations": False,
                "usage": {"users": {"total": 1}, "localPosts": DB.activities.count_documents(q)},
                "metadata": {
                    "sourceCode": "https://github.com/cwt/micronote.pub",
                    "nodeName": f"@{USERNAME}@{DOMAIN}",
                },
            }
        )

    if not cached:
        _cache(response, "api")
    return Response(
        headers={"Content-Type": "application/json; profile=http://nodeinfo.diaspora.software/ns/schema/2.0#"},
        response=response,
    )


@app.route("/manifest.json")
def pwa_manifest():
    """Web app manifest, branded per instance (replaces the old symlinked static manifests)."""
    return Response(
        headers={
            "Content-Type": "application/manifest+json",
            "Cache-Control": "public,max-age=86400",
        },
        response=activitypub.json_dumps(
            {
                "name": f"{NAME}'s micronote.pub",
                "short_name": USERNAME,
                "description": SUMMARY,
                "id": "/",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "background_color": "#eee",
                "theme_color": THEME_COLOR,
                "icons": [
                    {
                        "src": f"{CDN_URL}/static/pwa/icon-192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                    },
                    {
                        "src": f"{CDN_URL}/static/pwa/icon-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": f"{CDN_URL}/static/pwa/icon-maskable-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "maskable",
                    },
                ],
            }
        ),
    )


@app.route("/.well-known/nodeinfo")
def wellknown_nodeinfo():
    return flask_jsonify(
        links=[
            {
                "rel": "http://nodeinfo.diaspora.software/ns/schema/2.0",
                "href": f"{ID}/nodeinfo",
            }
        ]
    )


@app.route("/.well-known/webfinger")
def wellknown_webfinger():
    """Enable WebFinger support, required for Mastodon interopability."""
    # TODO(tsileo): move this to little-boxes?
    resource = request.args.get("resource")
    if resource not in [f"acct:{USERNAME}@{DOMAIN}", ID]:
        abort(404)

    out = {
        "subject": f"acct:{USERNAME}@{DOMAIN}",
        "aliases": [ID],
        "links": [
            {
                "rel": "http://webfinger.net/rel/profile-page",
                "type": "text/html",
                "href": BASE_URL,
            },
            {"rel": "self", "type": "application/activity+json", "href": ID},
            {
                "rel": "http://ostatus.org/schema/1.0/subscribe",
                "template": f"{BASE_URL}/authorize_follow?profile={{uri}}",
            },
            {"rel": "magic-public-key", "href": KEY.to_magic_key()},
            {
                "href": ICON_URL,
                "rel": "http://webfinger.net/rel/avatar",
                "type": mimetypes.guess_type(ICON_URL)[0],
            },
        ],
    }

    return Response(
        response=json.dumps(out),
        headers={"Content-Type": "application/jrd+json; charset=utf-8" if not app.debug else "application/json"},
    )


def add_extra_collection(raw_doc: dict[str, Any]) -> dict[str, Any]:
    if raw_doc["activity"]["type"] != ActivityType.CREATE.value:
        return raw_doc

    raw_doc["activity"]["object"]["replies"] = embed_collection(
        raw_doc.get("meta", {}).get("count_direct_reply", 0),
        f"{raw_doc['remote_id']}/replies",
    )

    raw_doc["activity"]["object"]["likes"] = embed_collection(
        raw_doc.get("meta", {}).get("count_like", 0), f"{raw_doc['remote_id']}/likes"
    )

    raw_doc["activity"]["object"]["shares"] = embed_collection(
        raw_doc.get("meta", {}).get("count_boost", 0), f"{raw_doc['remote_id']}/shares"
    )

    return raw_doc


def remove_context(activity: dict[str, Any]) -> dict[str, Any]:
    activity = activity.copy()
    activity.pop("@context", None)
    return activity


def activity_from_doc(raw_doc: dict[str, Any], embed: bool = False) -> dict[str, Any]:
    raw_doc = add_extra_collection(raw_doc)
    activity = clean_activity(raw_doc["activity"])
    if embed:
        return remove_context(activity)
    return activity


def activity_from_doc_embedded(raw_doc: dict[str, Any]) -> dict[str, Any]:
    return activity_from_doc(raw_doc, embed=True)


def activity_object_from_doc(raw_doc: dict[str, Any]) -> dict[str, Any]:
    return raw_doc["activity"]["object"]


def activity_object_id_from_doc(raw_doc: dict[str, Any]) -> str:
    return raw_doc["activity"]["object"]["id"]


def activity_actor_from_doc(raw_doc: dict[str, Any]) -> str:
    return raw_doc["activity"]["actor"]


def activity_without_context(raw_doc: dict[str, Any]) -> dict[str, Any]:
    return remove_context(raw_doc["activity"])


@app.route("/outbox", methods=["GET", "POST"])
def outbox():
    if request.method == "GET":
        if not is_api_request():
            abort(404)
        # TODO(tsileo): returns the whole outbox if authenticated
        q = {
            "box": Box.OUTBOX.value,
            "meta.deleted": False,
            "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
        }
        return jsonify(
            **activitypub.build_ordered_collection(
                DB.activities,
                q=q,
                cursor=request.args.get("cursor"),
                map_func=activity_from_doc_embedded,
                col_name="outbox",
            )
        )

    # Handle POST request
    try:
        api._api_required()
    except BadSignature:
        abort(401)

    data = request.get_json(force=True)
    app.logger.debug(data)
    activity = ap.parse_activity(data)
    activity_id = tasks.post_to_outbox(activity)

    return Response(status=201, headers={"Location": activity_id})


@app.route("/outbox/<item_id>")
def outbox_detail(item_id):
    doc = DB.activities.find_one({"box": Box.OUTBOX.value, "remote_id": back.activity_url(item_id)})
    if not doc:
        abort(404)

    if doc["meta"].get("deleted", False):
        obj = ap.parse_activity(doc["activity"])
        resp = jsonify(**obj.get_tombstone().to_dict())
        resp.status_code = 410
        return resp
    return jsonify(**activity_from_doc(doc))


@app.route("/outbox/<item_id>/activity")
def outbox_activity(item_id):
    data = DB.activities.find_one({"box": Box.OUTBOX.value, "remote_id": back.activity_url(item_id)})
    if not data:
        abort(404)
    obj = activity_from_doc(data)
    if data["meta"].get("deleted", False):
        obj = ap.parse_activity(data["activity"])
        resp = jsonify(**obj.get_object_sync().get_tombstone().to_dict())
        resp.status_code = 410
        return resp

    if obj["type"] != ActivityType.CREATE.value:
        abort(404)
    return jsonify(**obj["object"])


@app.route("/outbox/<item_id>/replies")
def outbox_activity_replies(item_id):
    if not is_api_request():
        abort(404)
    data = DB.activities.find_one(
        {
            "box": Box.OUTBOX.value,
            "remote_id": back.activity_url(item_id),
            "meta.deleted": False,
        }
    )
    if not data:
        abort(404)
    obj = ap.parse_activity(data["activity"])
    if obj.ACTIVITY_TYPE != ActivityType.CREATE:
        abort(404)

    q = {
        "meta.deleted": False,
        "type": ActivityType.CREATE.value,
        "activity.object.inReplyTo": obj.get_object_sync().id,
    }

    return jsonify(
        **activitypub.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=activity_object_from_doc,
            col_name=f"outbox/{item_id}/replies",
            first_page=request.args.get("page") == "first",
        )
    )


@app.route("/outbox/<item_id>/likes")
def outbox_activity_likes(item_id):
    if not is_api_request():
        abort(404)
    data = DB.activities.find_one(
        {
            "box": Box.OUTBOX.value,
            "remote_id": back.activity_url(item_id),
            "meta.deleted": False,
        }
    )
    if not data:
        abort(404)
    obj = ap.parse_activity(data["activity"])
    if obj.ACTIVITY_TYPE != ActivityType.CREATE:
        abort(404)

    q = {
        "meta.undo": False,
        "type": ActivityType.LIKE.value,
        "$or": [
            {"activity.object.id": obj.get_object_sync().id},
            {"activity.object": obj.get_object_sync().id},
        ],
    }

    return jsonify(
        **activitypub.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=activity_without_context,
            col_name=f"outbox/{item_id}/likes",
            first_page=request.args.get("page") == "first",
        )
    )


@app.route("/outbox/<item_id>/shares")
def outbox_activity_shares(item_id):
    if not is_api_request():
        abort(404)
    data = DB.activities.find_one(
        {
            "box": Box.OUTBOX.value,
            "remote_id": back.activity_url(item_id),
            "meta.deleted": False,
        }
    )
    if not data:
        abort(404)
    obj = ap.parse_activity(data["activity"])
    if obj.ACTIVITY_TYPE != ActivityType.CREATE:
        abort(404)

    q = {
        "meta.undo": False,
        "type": ActivityType.ANNOUNCE.value,
        "$or": [
            {"activity.object.id": obj.get_object_sync().id},
            {"activity.object": obj.get_object_sync().id},
        ],
    }

    return jsonify(
        **activitypub.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=activity_without_context,
            col_name=f"outbox/{item_id}/shares",
            first_page=request.args.get("page") == "first",
        )
    )


@app.route("/inbox", methods=["GET", "POST"])
def inbox():
    if request.method == "GET":
        if not is_api_request():
            abort(404)
        try:
            api._api_required()
        except BadSignature:
            abort(404)

        return jsonify(
            **activitypub.build_ordered_collection(
                DB.activities,
                q={"meta.deleted": False, "box": Box.INBOX.value},
                cursor=request.args.get("cursor"),
                map_func=activity_without_context,
                col_name="inbox",
            )
        )

    data = request.get_json(force=True)
    if not isinstance(data, dict) or "id" not in data or "type" not in data:
        abort(400)
    logger.debug(f"req_headers={request.headers}")
    logger.debug(f"raw_data={data}")
    try:
        if not verify_request_sync(request.method, request.path, request.headers, request.data):
            raise Exception("failed to verify request")
    except Exception:
        logger.exception("failed to verify request, trying to verify the payload by fetching the remote")
        try:
            data = get_backend().fetch_iri_sync(data["id"])
        except ActivityGoneError:
            # XXX Mastodon sends Delete activities that are not dereferencable, it's the actor url with #delete
            # appended, so an `ActivityGoneError` kind of ensure it's "legit"
            if (
                data["type"] == ActivityType.DELETE.value
                and isinstance(data.get("object"), str)
                and data["id"].startswith(data["object"])
            ):
                logger.info(f"received a Delete for an actor {data!r}")
                if get_backend().inbox_check_duplicate(MY_PERSON, data["id"]):
                    # The activity is already in the inbox
                    logger.info(f"received duplicate activity {data!r}, dropping it")
                    return Response(status=201)

                DB.activities.insert_one(
                    {
                        "box": Box.INBOX.value,
                        "activity": data,
                        "type": _to_list(data["type"]),
                        "remote_id": data["id"],
                        "meta": {"undo": False, "deleted": False},
                    }
                )
                # TODO(tsileo): write the callback the the delete external actor event
                return Response(status=201)
        except Exception:
            logger.exception(f"failed to fetch remote id at {data['id']}")
            return Response(
                status=422,
                headers={"Content-Type": "application/json"},
                response=json.dumps({"error": "failed to verify request (using HTTP signatures or fetching the IRI)"}),
            )
    activity = ap.parse_activity(data)
    logger.debug(f"inbox activity={activity}/{data}")
    tasks.post_to_inbox(activity)

    return Response(status=201)


@app.route("/followers")
def followers():
    q = {"box": Box.INBOX.value, "type": ActivityType.FOLLOW.value, "meta.undo": False}

    if is_api_request():
        return jsonify(
            **activitypub.build_ordered_collection(
                DB.activities,
                q=q,
                cursor=request.args.get("cursor"),
                map_func=activity_actor_from_doc,
                col_name="followers",
            )
        )

    raw_followers, older_than, newer_than = paginated_query(DB.activities, q)
    followers = [doc["meta"]["actor"] for doc in raw_followers if "actor" in doc.get("meta", {})]
    return render_template(
        "followers.html",
        followers_data=followers,
        older_than=older_than,
        newer_than=newer_than,
    )


@app.route("/following")
def following():
    q = {"box": Box.OUTBOX.value, "type": ActivityType.FOLLOW.value, "meta.undo": False}

    if is_api_request():
        return jsonify(
            **activitypub.build_ordered_collection(
                DB.activities,
                q=q,
                cursor=request.args.get("cursor"),
                map_func=activity_object_from_doc,
                col_name="following",
            )
        )

    if config.HIDE_FOLLOWING and not session.get("logged_in", False):
        abort(404)

    following, older_than, newer_than = paginated_query(DB.activities, q)
    following = [
        (doc["remote_id"], doc["meta"]["object"])
        for doc in following
        if "remote_id" in doc and "object" in doc.get("meta", {})
    ]
    return render_template(
        "following.html",
        following_data=following,
        older_than=older_than,
        newer_than=newer_than,
    )


@app.route("/tags/<tag>")
def tags(tag):
    if not DB.activities.count_documents(
        {
            "box": Box.OUTBOX.value,
            "activity.object.tag.type": "Hashtag",
            "activity.object.tag.name": f"#{tag}",
        }
    ):
        abort(404)
    if not is_api_request():
        return render_template(
            "tags.html",
            tag=tag,
            outbox_data=DB.activities.find(
                {
                    "box": Box.OUTBOX.value,
                    "type": ActivityType.CREATE.value,
                    "meta.deleted": False,
                    "activity.object.tag.type": "Hashtag",
                    "activity.object.tag.name": f"#{tag}",
                }
            ),
        )
    q = {
        "box": Box.OUTBOX.value,
        "meta.deleted": False,
        "meta.undo": False,
        "type": ActivityType.CREATE.value,
        "activity.object.tag.type": "Hashtag",
        "activity.object.tag.name": f"#{tag}",
    }
    return jsonify(
        **activitypub.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=activity_object_id_from_doc,
            col_name=f"tags/{tag}",
        )
    )


@app.route("/featured")
def featured():
    if not is_api_request():
        abort(404)
    q = {
        "box": Box.OUTBOX.value,
        "type": ActivityType.CREATE.value,
        "meta.deleted": False,
        "meta.undo": False,
        "meta.pinned": True,
    }
    data = [clean_activity(doc["activity"]["object"]) for doc in DB.activities.find(q)]
    return jsonify(**activitypub.simple_build_ordered_collection("featured", data))


@app.route("/liked")
def liked():
    if not is_api_request():
        q = {
            "box": Box.OUTBOX.value,
            "type": ActivityType.LIKE.value,
            "meta.deleted": False,
            "meta.undo": False,
        }

        liked, older_than, newer_than = paginated_query(DB.activities, q)

        return render_template("liked.html", liked=liked, older_than=older_than, newer_than=newer_than)

    q = {"meta.deleted": False, "meta.undo": False, "type": ActivityType.LIKE.value}
    return jsonify(
        **activitypub.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=activity_object_from_doc,
            col_name="liked",
        )
    )


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        directory=os.path.join(app.root_path, "static"), path="favicon.ico", mimetype="image/vnd.microsoft.icon"
    )
