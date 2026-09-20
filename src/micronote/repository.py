"""Named data-access queries for routes, admin, and feeds.

The module is Flask-free: cursor values are passed in explicitly and a
bad cursor raises `ValueError`, which the app maps to HTTP 400. Worker
and scripts can import these queries without an application context.
"""

import logging
import re

from active_boxes.activitypub import ActivityType
from neosqlite.objectid import ObjectId

from micronote.boxes import Box
from micronote.config import BASE_URL, DB, DOMAIN, USERNAME
from micronote.instance import back

log = logging.getLogger(__name__)


def activities():
    """The activities collection, for collection serialization."""
    return DB.activities


def paginated_query(db, q, older_than=None, newer_than=None, limit=25, sort_key="_id"):
    """Cursor pagination over `_id`, returning `(items, older_than, newer_than)`."""

    def sort_key_as_str(doc):
        return str(doc[sort_key])

    # Copy: cursor keys are added below and must not leak into the caller.
    q = q.copy()

    next_older = next_newer = None
    query_sort = -1
    first_page = not older_than and not newer_than

    if older_than:
        try:
            q["_id"] = {"$lt": ObjectId(older_than)}
        except Exception as exc:
            raise ValueError("Invalid cursor") from exc
    elif newer_than:
        try:
            q["_id"] = {"$gt": ObjectId(newer_than)}
        except Exception as exc:
            raise ValueError("Invalid cursor") from exc
        query_sort = 1

    data = list(db.find(q, limit=limit + 1).sort(sort_key, query_sort))
    data_len = len(data)
    if not data:
        return [], None, None
    data = sorted(data[:limit], key=sort_key_as_str, reverse=True)

    if older_than:
        next_newer = str(data[0]["_id"])
        if data_len == limit + 1:
            next_older = str(data[-1]["_id"])
    elif newer_than:
        next_older = str(data[-1]["_id"])
        if data_len == limit + 1:
            next_newer = str(data[0]["_id"])
    elif first_page and data_len == limit + 1:
        next_older = str(data[-1]["_id"])

    return data, next_older, next_newer


def outbox_item(item_id, include_deleted=False):
    """An outbox activity by short id; deleted rows excluded by default."""
    q = {"box": Box.OUTBOX.value, "remote_id": back.activity_url(item_id)}
    if not include_deleted:
        q["meta.deleted"] = False
    return DB.activities.find_one(q)


def outbox_item_any(ref):
    """An outbox activity by short id or full activity IRI."""
    return DB.activities.find_one(
        {"box": Box.OUTBOX.value, "$or": [{"remote_id": back.activity_url(ref)}, {"remote_id": ref}]}
    )


def activity_by_object_id(oid):
    return DB.activities.find_one({"activity.object.id": oid})


def activity_by_ref(ref):
    return DB.activities.find_one({"$or": [{"remote_id": ref}, {"activity.object.id": ref}]})


def block_activity(actor):
    return DB.activities.find_one(
        {
            "box": Box.OUTBOX.value,
            "type": ActivityType.BLOCK.value,
            "activity.object": actor,
            "meta.undo": False,
        }
    )


def follow_activity(actor):
    return DB.activities.find_one(
        {
            "box": Box.OUTBOX.value,
            "type": ActivityType.FOLLOW.value,
            "meta.undo": False,
            "activity.object": actor,
        }
    )


def following_docs():
    return list(
        DB.activities.find(
            {
                "box": Box.OUTBOX.value,
                "type": ActivityType.FOLLOW.value,
                "meta.undo": False,
            }
        )
    )


def actors_for_object(object_id, activity_type):
    """Cached actor metadata of Like/Announce activities targeting the object."""
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


def pinned_notes():
    q = {
        "box": Box.OUTBOX.value,
        "type": ActivityType.CREATE.value,
        "meta.deleted": False,
        "meta.undo": False,
        "meta.pinned": True,
    }
    return list(DB.activities.find(q))


def outbox_page(older_than=None, newer_than=None, limit=25):
    q = {
        "box": Box.OUTBOX.value,
        "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
        "activity.object.inReplyTo": None,
        "meta.deleted": False,
        "meta.undo": False,
        "$or": [{"meta.pinned": False}, {"meta.pinned": {"$exists": False}}],
    }
    return paginated_query(DB.activities, q, older_than, newer_than, limit=limit)


def with_replies_page(older_than=None, newer_than=None, limit=25):
    q = {
        "box": Box.OUTBOX.value,
        "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
        "meta.deleted": False,
        "meta.undo": False,
    }
    return paginated_query(DB.activities, q, older_than, newer_than, limit=limit)


def followers_page(older_than=None, newer_than=None, limit=25):
    q = {"box": Box.INBOX.value, "type": ActivityType.FOLLOW.value, "meta.undo": False}
    return paginated_query(DB.activities, q, older_than, newer_than, limit=limit)


def following_page(older_than=None, newer_than=None, limit=25):
    q = {"box": Box.OUTBOX.value, "type": ActivityType.FOLLOW.value, "meta.undo": False}
    return paginated_query(DB.activities, q, older_than, newer_than, limit=limit)


def liked_page(older_than=None, newer_than=None, limit=25):
    q = {
        "box": Box.OUTBOX.value,
        "type": ActivityType.LIKE.value,
        "meta.deleted": False,
        "meta.undo": False,
    }
    return paginated_query(DB.activities, q, older_than, newer_than, limit=limit)


def notifications_page(older_than=None, newer_than=None, limit=25):
    mentions_query = {
        "type": ActivityType.CREATE.value,
        "activity.object.tag.type": "Mention",
        "activity.object.tag.name": f"@{USERNAME}@{DOMAIN}",
        "meta.deleted": False,
    }
    escaped_base = re.escape(BASE_URL)
    replies_query = {
        "type": ActivityType.CREATE.value,
        "activity.object.inReplyTo": {"$regex": f"^{escaped_base}"},
    }
    announced_query = {
        "type": ActivityType.ANNOUNCE.value,
        "activity.object": {"$regex": f"^{escaped_base}"},
    }
    new_followers_query = {"type": ActivityType.FOLLOW.value}
    unfollow_query = {
        "type": ActivityType.UNDO.value,
        "activity.object.type": ActivityType.FOLLOW.value,
    }
    likes_query = {
        "type": ActivityType.LIKE.value,
        "activity.object": {"$regex": f"^{escaped_base}"},
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
    return paginated_query(DB.activities, q, older_than, newer_than, limit=limit)


def stream_page(older_than=None, newer_than=None, limit=25, include_all=False):
    q = {} if include_all else {"meta.stream": True, "meta.deleted": False}
    return paginated_query(DB.activities, q, older_than, newer_than, limit=limit)


def tag_exists(tag):
    return bool(
        DB.activities.count_documents(
            {
                "box": Box.OUTBOX.value,
                "activity.object.tag.type": "Hashtag",
                "activity.object.tag.name": f"#{tag}",
            }
        )
    )


def tag_notes(tag):
    return DB.activities.find(
        {
            "box": Box.OUTBOX.value,
            "type": ActivityType.CREATE.value,
            "meta.deleted": False,
            "activity.object.tag.type": "Hashtag",
            "activity.object.tag.name": f"#{tag}",
        }
    )


def recent_outbox_notes(limit=10):
    return DB.activities.find({"box": Box.OUTBOX.value, "type": "Create", "meta.deleted": False}, limit=limit).sort(
        "_id", -1
    )


def outbox_docs():
    return DB.activities.find({"box": Box.OUTBOX.value})


def collection_sizes():
    return {
        "inbox_size": DB.activities.count_documents({"box": Box.INBOX.value}),
        "outbox_size": DB.activities.count_documents({"box": Box.OUTBOX.value}),
    }


def local_posts_count():
    q = {
        "box": Box.OUTBOX.value,
        "meta.deleted": False,
        "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
    }
    return DB.activities.count_documents(q)


def counts() -> dict[str, int]:
    """The public counters rendered into the header and admin dashboard."""
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
        "followers_count": DB.activities.count_documents(followers_q),
        "following_count": DB.activities.count_documents(following_q),
        "notes_count": notes_count,
        "liked_count": liked_count,
        "with_replies_count": with_replies_count,
    }
