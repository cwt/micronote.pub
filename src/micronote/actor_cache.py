"""Remote actor resolution and caching for template rendering.

Template filters delegate here, so rendering keeps a single place that
may fetch an actor over the network and upsert it into the local cache.
"""

import logging

from active_boxes.activitypub import get_backend
from active_boxes.errors import ActivityGoneError, ActivityNotFoundError

from micronote.config import DB

log = logging.getLogger(__name__)


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

    log.debug(f"GET_ACTOR {url}")
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
