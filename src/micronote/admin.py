import json
from urllib.parse import urlparse

import bcrypt
import flask
from active_boxes import activitypub as ap
from active_boxes.activitypub import ActivityType, get_backend
from active_boxes.errors import (
    ActivityGoneError,
    ActivityNotFoundError,
    ActivityUnavailableError,
    BadActivityError,
    UnexpectedActivityTypeError,
)
from flask import abort, current_app, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect

from micronote.activitypub import Box
from micronote.config import BASE_URL, DB, DOMAIN, PASS, USERNAME
from micronote.utils.headers import noindex
from micronote.utils.login import login_required, safe_next_url
from micronote.utils.lookup import lookup
from micronote.utils.query import paginated_query
from micronote.utils.thread import _build_thread

blueprint = flask.Blueprint("admin", __name__, template_folder="templates")
csrf = CSRFProtect(current_app)


def verify_pass(pwd):
    return bcrypt.checkpw(pwd.encode("utf-8"), PASS.encode("utf-8"))


def requested_limit(default=25, maximum=100) -> int:
    try:
        return min(max(int(request.args.get("limit", default)), 1), maximum)
    except (TypeError, ValueError):
        return default


def _following_map() -> dict[str, str]:
    """Actors we already follow: actor IRI -> outbox Follow remote_id.

    Lets follow-related pages offer "unfollow" instead of "follow back".
    """
    following_map = {}
    for doc in DB.activities.find(
        {
            "box": Box.OUTBOX.value,
            "type": ActivityType.FOLLOW.value,
            "meta.undo": False,
        }
    ):
        target = (doc.get("activity") or {}).get("object")
        if isinstance(target, dict):
            target = target.get("id")
        if target and doc.get("remote_id"):
            following_map[target] = doc["remote_id"]
    return following_map


@blueprint.route("/admin", methods=["GET"])
@login_required
def admin():
    q = {
        "meta.deleted": False,
        "meta.undo": False,
        "type": ActivityType.LIKE.value,
        "box": Box.OUTBOX.value,
    }
    col_liked = DB.activities.count_documents(q)

    return render_template(
        "admin.html",
        instances=list(DB.instances.find()),
        inbox_size=DB.activities.count_documents({"box": Box.INBOX.value}),
        outbox_size=DB.activities.count_documents({"box": Box.OUTBOX.value}),
        col_liked=col_liked,
        col_followers=DB.activities.count_documents(
            {
                "box": Box.INBOX.value,
                "type": ActivityType.FOLLOW.value,
                "meta.undo": False,
            }
        ),
        col_following=DB.activities.count_documents(
            {
                "box": Box.OUTBOX.value,
                "type": ActivityType.FOLLOW.value,
                "meta.undo": False,
            }
        ),
    )


@blueprint.route("/admin/lookup", methods=["GET", "POST"])
@login_required
def admin_lookup():
    data = None
    meta = None
    error = None
    submitted = None
    if request.method == "POST":
        csrf.protect()
        submitted = request.form.get("url")
        if submitted:
            try:
                data = lookup(submitted)
            except ap.Error:
                current_app.logger.warning(f"lookup not found for {submitted!r}")
                error = f"could not find {submitted}"
            except Exception as exc:
                current_app.logger.exception(f"lookup failed for {submitted!r}")
                error = str(exc) or exc.__class__.__name__
            else:
                if data.has_type(ActivityType.ANNOUNCE):
                    obj = data.get_object_sync()
                    meta = {
                        "object": obj.to_dict(),
                        "object_actor": obj.get_actor_sync().to_dict(),
                        "actor": data.get_actor_sync().to_dict(),
                    }

        current_app.logger.debug(data)
    return render_template("lookup.html", data=data, meta=meta, url=submitted, error=error)


def _fetch_remote_data(oid: str):
    """Fetches a remote object and wraps it as ad-hoc thread data.

    Used when the object is not stored locally (yet): the worker saves
    unknown reply targets asynchronously, and /admin/new can display
    notes that were only fetched on the fly.
    """
    try:
        remote_object = get_backend().fetch_iri_sync(oid)
    except (ActivityGoneError, ActivityNotFoundError, ActivityUnavailableError):
        abort(404)
    data = {"meta": {}, "activity": {"object": remote_object}}
    try:
        parsed = ap.parse_activity(data["activity"]["object"])
    except (BadActivityError, UnexpectedActivityTypeError):
        abort(404)
    return data, parsed


@blueprint.route("/admin/thread")
@login_required
def admin_thread():
    oid = request.args.get("oid")
    data = (
        DB.activities.find_one(
            {
                "$or": [
                    {"remote_id": oid},
                    {"activity.object.id": oid},
                ]
            }
        )
        if oid
        else None
    )
    if not data:
        if not oid:
            abort(404)
        # Not stored locally (yet): the worker saves unknown reply targets
        # asynchronously, and /admin/new can display notes that were only
        # fetched on the fly. Fetch the object so the thread still renders
        # instead of 404ing straight after posting a reply.
        data, _ = _fetch_remote_data(oid)
    if data["meta"].get("deleted", False):
        abort(410)
    thread = _build_thread(data)

    tpl = "note.html"
    if request.args.get("debug"):
        tpl = "note_debug.html"
    return render_template(tpl, thread=thread, note=data)


@blueprint.route("/admin/new", methods=["GET"])
@login_required
def admin_new():
    reply_id = None
    content = ""
    thread = []
    current_app.logger.debug(request.args)
    if request.args.get("reply"):
        data = DB.activities.find_one({"activity.object.id": request.args.get("reply")})
        if data:
            reply = ap.parse_activity(data["activity"])
        else:
            data, reply = _fetch_remote_data(request.args.get("reply"))

        reply_id = reply.id
        if reply.ACTIVITY_TYPE == ActivityType.CREATE:
            reply_id = reply.get_object_sync().id
        actor = reply.get_actor_sync()
        domain = urlparse(actor.id).netloc
        # FIXME(tsileo): if reply of reply, fetch all participants
        content = f"@{actor.preferredUsername}@{domain} "
        thread = _build_thread(data)

    return render_template("new.html", reply=reply_id, content=content, thread=thread)


@blueprint.route("/admin/notifications")
@login_required
def admin_notifications():
    # FIXME(tsileo): show unfollow (performed by the current actor) and liked???
    mentions_query = {
        "type": ActivityType.CREATE.value,
        "activity.object.tag.type": "Mention",
        "activity.object.tag.name": f"@{USERNAME}@{DOMAIN}",
        "meta.deleted": False,
    }
    replies_query = {
        "type": ActivityType.CREATE.value,
        "activity.object.inReplyTo": {"$regex": f"^{BASE_URL}"},
    }
    announced_query = {
        "type": ActivityType.ANNOUNCE.value,
        "activity.object": {"$regex": f"^{BASE_URL}"},
    }
    new_followers_query = {"type": ActivityType.FOLLOW.value}
    unfollow_query = {
        "type": ActivityType.UNDO.value,
        "activity.object.type": ActivityType.FOLLOW.value,
    }
    likes_query = {
        "type": ActivityType.LIKE.value,
        "activity.object": {"$regex": f"^{BASE_URL}"},
    }
    followed_query = {"type": ActivityType.ACCEPT.value}
    q = {
        "box": Box.INBOX.value,
        "$or": [
            mentions_query,
            announced_query,
            replies_query,
            new_followers_query,
            followed_query,
            unfollow_query,
            likes_query,
        ],
    }
    inbox_data, older_than, newer_than = paginated_query(DB.activities, q)

    return render_template(
        "stream.html",
        inbox_data=inbox_data,
        older_than=older_than,
        newer_than=newer_than,
        following_map=_following_map(),
    )


@blueprint.route("/admin/stream")
@login_required
def admin_stream():
    q = {"meta.stream": True, "meta.deleted": False}

    tpl = "stream.html"
    if request.args.get("debug"):
        tpl = "stream_debug.html"
        if request.args.get("debug_inbox"):
            q = {}

    inbox_data, older_than, newer_than = paginated_query(DB.activities, q, limit=requested_limit())

    return render_template(
        tpl, inbox_data=inbox_data, older_than=older_than, newer_than=newer_than, following_map=_following_map()
    )


@blueprint.route("/admin/logout")
@login_required
def admin_logout():
    session.clear()
    return redirect("/")


@blueprint.route("/login", methods=["POST", "GET"])
@noindex
def admin_login():
    from micronote.utils.webauthn import (
        clear_state,
        credential_options,
        get_server,
        load_state,
        save_state,
        stored_credentials,
        update_sign_count,
    )

    if session.get("logged_in") is True:
        return redirect(url_for(".admin_notifications"))

    credentials = stored_credentials()
    webauthn_enabled = bool(credentials)
    login_error = None
    if request.method == "POST":
        csrf.protect()
        pwd = request.form.get("pass")
        username = request.form.get("username")
        if not (pwd and username == USERNAME and verify_pass(pwd)):
            login_error = "Incorrect username or password."
        elif credentials:
            raw_assertion = request.form.get("assertion")
            state = load_state("login")
            if not raw_assertion or not state:
                login_error = "Security key assertion missing or session expired."
            else:
                try:
                    assertion = json.loads(raw_assertion)
                    credential = get_server().authenticate_complete(state, credentials, assertion)
                except (ValueError, json.JSONDecodeError, Exception) as exc:
                    current_app.logger.debug(f"webauthn failed: {exc}")
                    login_error = "Security key authentication failed."
                else:
                    if credential.sign_count != 0:
                        update_sign_count(credential.credential_id, credential.sign_count)
                finally:
                    clear_state("login")

        if not login_error:
            session.clear()
            session["logged_in"] = True
            session.permanent = True
            return redirect(safe_next_url(request.args.get("redirect"), url_for(".admin_notifications")))

    options = None
    if credentials:
        options, state = get_server().authenticate_begin(credentials)
        save_state("login", state)
        options = credential_options(options)

    return render_template("login.html", webauthn_enabled=webauthn_enabled, options=options, login_error=login_error)
