"""Page cache: reads, writes, and invalidation policy.

Flask-free: callers pass the request path and cache type explicitly, and
auth awareness lives in the route layer (`@page_cache` in Phase 4).

`clear()` is the single invalidation entry point: it drops the page cache
and the counts memo together so the two can never disagree.
"""

import logging
from datetime import UTC, datetime

from active_boxes import activitypub as ap

from micronote import stats
from micronote.config import BASE_URL, DB, ID

log = logging.getLogger(__name__)

CACHING = True


def get_page(path: str, type_: str = "html", arg: str | None = None):
    if not CACHING:
        return None
    cached = DB.cache2.find_one({"path": path, "type": type_, "arg": arg})
    if cached:
        log.info("from cache")
        return cached["response_data"]
    return None


def set_page(path: str, data, type_: str = "html", arg: str | None = None) -> None:
    if not CACHING:
        return
    DB.cache2.update_one(
        {"path": path, "type": type_, "arg": arg},
        {"$set": {"response_data": data, "date": datetime.now(UTC)}},
        upsert=True,
    )


def clear() -> None:
    """Drops the page cache and the counts memo together."""
    DB.cache2.delete_many({})
    stats.clear_counts()


def invalidate_for_activity(activity) -> None:
    """Invalidates cached pages affected by a processed activity."""
    if activity.has_type([ap.ActivityType.UNDO, ap.ActivityType.DELETE, ap.ActivityType.UPDATE]):
        clear()
    elif activity.has_type(ap.ActivityType.FOLLOW):
        # A new follower changes the followers badge rendered into the
        # cached homepage (header.html), so the page cache must go.
        # (Duplicates never reach here: post_to_inbox drops them.)
        clear()
    elif activity.has_type([ap.ActivityType.LIKE, ap.ActivityType.ANNOUNCE]):
        if activity.get_object_sync().id.startswith(BASE_URL):
            clear()
    elif activity.has_type(ap.ActivityType.CREATE):
        note = activity.get_object_sync()
        if not note.inReplyTo or note.inReplyTo.startswith(ID):
            clear()
