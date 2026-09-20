"""HTML views for the public microblog."""

import logging
from typing import Any

from active_boxes.activitypub import ActivityType
from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from micronote import ap_serialize, cache, config
from micronote.boxes import Box
from micronote.config import DB, ME
from micronote.instance import back
from micronote.utils.login import login_required
from micronote.utils.query import paginated_query
from micronote.utils.thread import build_thread
from micronote.web import activity_json, is_api_request

blueprint = Blueprint("views", __name__, template_folder="templates")

log = logging.getLogger(__name__)


@blueprint.route("/")
def index():
    if is_api_request():
        return activity_json(**ME)
    cache_arg = f"{request.args.get('older_than', '')}:{request.args.get('newer_than', '')}"
    logged_in = session.get("logged_in")
    if not logged_in:
        cached = cache.get_page(request.path, "html", cache_arg)
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
    if not logged_in:
        cache.set_page(request.path, resp, "html", cache_arg)
    return resp


@blueprint.route("/with_replies")
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
            log.exception(f"invalid doc: {doc!r}")
    return actors


@blueprint.route("/note/<note_id>")
def note_by_id(note_id):
    if is_api_request():
        return redirect(url_for("ap.outbox_activity", item_id=note_id))

    data = DB.activities.find_one({"box": Box.OUTBOX.value, "remote_id": back.activity_url(note_id)})
    if not data:
        abort(404)
    if data["meta"].get("deleted", False):
        abort(410)
    thread = build_thread(data)
    log.info(f"thread={thread!r}")

    likes = _collect_actors(data, ActivityType.LIKE)
    log.info(f"likes={likes!r}")
    shares = _collect_actors(data, ActivityType.ANNOUNCE)
    log.info(f"shares={shares!r}")

    return render_template("note.html", likes=likes, shares=shares, thread=thread, note=data)


@blueprint.route("/followers")
def followers():
    q = {"box": Box.INBOX.value, "type": ActivityType.FOLLOW.value, "meta.undo": False}

    if is_api_request():
        return activity_json(
            **ap_serialize.build_ordered_collection(
                DB.activities,
                q=q,
                cursor=request.args.get("cursor"),
                map_func=ap_serialize.activity_actor_from_doc,
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


@blueprint.route("/following")
def following():
    q = {"box": Box.OUTBOX.value, "type": ActivityType.FOLLOW.value, "meta.undo": False}

    if is_api_request():
        return activity_json(
            **ap_serialize.build_ordered_collection(
                DB.activities,
                q=q,
                cursor=request.args.get("cursor"),
                map_func=ap_serialize.activity_object_from_doc,
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


@blueprint.route("/tags/<tag>")
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
    return activity_json(
        **ap_serialize.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_object_id_from_doc,
            col_name=f"tags/{tag}",
        )
    )


@blueprint.route("/liked")
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
    return activity_json(
        **ap_serialize.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_object_from_doc,
            col_name="liked",
        )
    )
