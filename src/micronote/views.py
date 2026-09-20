"""HTML views for the public microblog."""

import logging

from active_boxes.activitypub import ActivityType
from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from micronote import ap_serialize, config, repository
from micronote.boxes import Box
from micronote.config import me
from micronote.threads import build_thread
from micronote.utils.login import login_required
from micronote.web import activity_json, negotiate, page_cache

blueprint = Blueprint("views", __name__, template_folder="templates")

log = logging.getLogger(__name__)


@page_cache(type_="html")
def index_html():
    older_than = request.args.get("older_than")
    newer_than = request.args.get("newer_than")

    pinned = []
    # Only fetch the pinned notes if we're on the first page
    if not older_than and not newer_than:
        pinned = repository.pinned_notes()

    outbox_data, older_than, newer_than = repository.outbox_page(older_than, newer_than, limit=25 - len(pinned))

    return render_template(
        "index.html",
        outbox_data=outbox_data,
        older_than=older_than,
        newer_than=newer_than,
        pinned=pinned,
    )


def index_ap():
    return activity_json(**me())


blueprint.add_url_rule("/", endpoint="index", view_func=negotiate(html=index_html, activitypub=index_ap))


@blueprint.route("/with_replies")
@login_required
def with_replies():
    outbox_data, older_than, newer_than = repository.with_replies_page(
        request.args.get("older_than"), request.args.get("newer_than")
    )

    return render_template(
        "index.html",
        outbox_data=outbox_data,
        older_than=older_than,
        newer_than=newer_than,
    )


def note_by_id_html(note_id):
    data = repository.outbox_item(note_id, include_deleted=True)
    if not data:
        abort(404)
    if data["meta"].get("deleted", False):
        abort(410)
    thread = build_thread(data)
    log.info(f"thread={thread!r}")

    object_id = data["activity"]["object"]["id"]
    likes = repository.actors_for_object(object_id, ActivityType.LIKE)
    log.info(f"likes={likes!r}")
    shares = repository.actors_for_object(object_id, ActivityType.ANNOUNCE)
    log.info(f"shares={shares!r}")

    return render_template("note.html", likes=likes, shares=shares, thread=thread, note=data)


def note_by_id_ap(note_id):
    return redirect(url_for("ap.outbox_activity", item_id=note_id))


blueprint.add_url_rule(
    "/note/<note_id>",
    endpoint="note_by_id",
    view_func=negotiate(html=note_by_id_html, activitypub=note_by_id_ap),
)


def followers_html():
    raw_followers, older_than, newer_than = repository.followers_page(
        request.args.get("older_than"), request.args.get("newer_than")
    )
    followers = [doc["meta"]["actor"] for doc in raw_followers if "actor" in doc.get("meta", {})]
    return render_template(
        "followers.html",
        followers_data=followers,
        older_than=older_than,
        newer_than=newer_than,
    )


def followers_ap():
    q = {"box": Box.INBOX.value, "type": ActivityType.FOLLOW.value, "meta.undo": False}
    return activity_json(
        **ap_serialize.build_ordered_collection(
            repository.activities(),
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_actor_from_doc,
            col_name="followers",
        )
    )


blueprint.add_url_rule(
    "/followers",
    endpoint="followers",
    view_func=negotiate(html=followers_html, activitypub=followers_ap),
)


def following_html():
    if config.HIDE_FOLLOWING and not session.get("logged_in", False):
        abort(404)

    following, older_than, newer_than = repository.following_page(
        request.args.get("older_than"), request.args.get("newer_than")
    )
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


def following_ap():
    q = {"box": Box.OUTBOX.value, "type": ActivityType.FOLLOW.value, "meta.undo": False}
    return activity_json(
        **ap_serialize.build_ordered_collection(
            repository.activities(),
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_object_from_doc,
            col_name="following",
        )
    )


blueprint.add_url_rule(
    "/following",
    endpoint="following",
    view_func=negotiate(html=following_html, activitypub=following_ap),
)


def _require_tag(tag):
    """The tag must exist for both representations; 404 otherwise."""
    if not repository.tag_exists(tag):
        abort(404)


def tags_html(tag):
    _require_tag(tag)
    return render_template("tags.html", tag=tag, outbox_data=repository.tag_notes(tag))


def tags_ap(tag):
    _require_tag(tag)
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
            repository.activities(),
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_object_id_from_doc,
            col_name=f"tags/{tag}",
        )
    )


blueprint.add_url_rule(
    "/tags/<tag>",
    endpoint="tags",
    view_func=negotiate(html=tags_html, activitypub=tags_ap),
)


def liked_html():
    liked, older_than, newer_than = repository.liked_page(request.args.get("older_than"), request.args.get("newer_than"))

    return render_template("liked.html", liked=liked, older_than=older_than, newer_than=newer_than)


def liked_ap():
    q = {"meta.deleted": False, "meta.undo": False, "type": ActivityType.LIKE.value}
    return activity_json(
        **ap_serialize.build_ordered_collection(
            repository.activities(),
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_object_from_doc,
            col_name="liked",
        )
    )


blueprint.add_url_rule(
    "/liked",
    endpoint="liked",
    view_func=negotiate(html=liked_html, activitypub=liked_ap),
)
