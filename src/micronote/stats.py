"""Read-model counters with a short TTL memo.

The memo is process-local; `cache.clear()` drops it whenever writers
invalidate the page cache, so the two can never disagree.
"""

from active_boxes.activitypub import ActivityType
from cachetools import TTLCache

from micronote.boxes import Box
from micronote.config import DB

_COUNTS_CACHE: TTLCache[str, dict[str, int]] = TTLCache(maxsize=1, ttl=30)


def counts() -> dict[str, int]:
    cached = _COUNTS_CACHE.get("counts")
    if cached is not None:
        return cached

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

    result = {
        "followers_count": DB.activities.count_documents(followers_q),
        "following_count": DB.activities.count_documents(following_q),
        "notes_count": notes_count,
        "liked_count": liked_count,
        "with_replies_count": with_replies_count,
    }
    _COUNTS_CACHE["counts"] = result
    return result


def clear_counts() -> None:
    _COUNTS_CACHE.clear()
