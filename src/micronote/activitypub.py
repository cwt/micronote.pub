import json
import logging
import os
from datetime import UTC, datetime
from enum import StrEnum
from functools import wraps
from typing import Any

from active_boxes import activitypub as ap
from active_boxes import strtobool
from active_boxes.activitypub import _to_list
from active_boxes.backend import Backend
from active_boxes.errors import (
    ActivityGoneError,
    Error,
    NotAnActivityError,
)
from cachetools import LRUCache
from feedgen.feed import FeedGenerator
from flask import abort
from html2text import html2text
from neosqlite.objectid import ObjectId

from micronote.config import BASE_URL, DB, DB_NAME, EXTRA_INBOXES, ID, ME, USER_AGENT, USERNAME, create_db_client
from micronote.utils.emoji import extract_custom_emojis

logger = logging.getLogger(__name__)

ACTORS_CACHE: LRUCache[str, Any] = LRUCache(maxsize=256)


def _json_default(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(data) -> str:
    """Serializes activity data, normalizing datetimes to ISO strings.

    Needed because the document store may return timestamps as datetime
    objects (like PyMongo/BSON does) instead of strings.
    """
    return json.dumps(data, default=_json_default)


def _actor_to_meta(actor: ap.BaseActivity, with_inbox: bool = False) -> dict[str, Any]:
    meta = {
        "id": actor.id,
        "url": actor.url,
        "icon": actor.icon,
        "name": actor.name,
        "preferredUsername": actor.preferredUsername,
        # Custom emojis (Mastodon-style Emoji tags) for rendering the
        # display name; stored at ingest so pages need no extra lookups.
        "emojis": extract_custom_emojis((actor._data or {}).get("tag", [])),
    }
    if with_inbox:
        meta |= {
            "inbox": actor.inbox,
            "sharedInbox": actor._data.get("endpoints", {}).get("sharedInbox"),
        }
    logger.debug(f"meta={meta}")

    return meta


def _remove_id(doc: ap.ObjectType) -> ap.ObjectType:
    """Helper for removing MongoDB's `_id` field."""
    doc = doc.copy()
    doc.pop("_id", None)
    return doc


def ensure_it_is_me(f):
    """Method decorator used to track the events fired during tests."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        if args[1].id != ME["id"]:
            raise Error("unexpected actor")
        return f(*args, **kwargs)

    return wrapper


class Box(StrEnum):
    INBOX = "inbox"
    OUTBOX = "outbox"
    REPLIES = "replies"


class MicroblogPubBackend(Backend):
    """Implements a Little Boxes backend, backed by NeoSQLite."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.DB = create_db_client(DB_NAME)

    def debug_mode(self) -> bool:
        return strtobool(os.getenv("MICRONOTE_DEBUG", "false"))

    def user_agent(self) -> str:
        """Setup a custom user agent."""
        return USER_AGENT

    def extra_inboxes(self) -> list[str]:
        return EXTRA_INBOXES

    def base_url(self) -> str:
        """Base URL config."""
        return BASE_URL

    def activity_url(self, obj_id):
        """URL for activity link."""
        return f"{BASE_URL}/outbox/{obj_id}"

    def note_url(self, obj_id):
        """URL for activity link."""
        return f"{BASE_URL}/note/{obj_id}"

    def save(self, box: Box, activity: ap.BaseActivity) -> None:
        """Custom helper for saving an activity to the DB."""
        self.DB.activities.insert_one(
            {
                "box": box.value,
                "activity": activity.to_dict(),
                "type": _to_list(activity.type),
                "remote_id": activity.id,
                "meta": {"undo": False, "deleted": False},
            }
        )

    def followers(self) -> list[str]:
        q = {
            "box": Box.INBOX.value,
            "type": ap.ActivityType.FOLLOW.value,
            "meta.undo": False,
        }
        return [doc["activity"]["actor"] for doc in self.DB.activities.find(q)]

    def followers_as_recipients(self) -> list[str]:
        q = {
            "box": Box.INBOX.value,
            "type": ap.ActivityType.FOLLOW.value,
            "meta.undo": False,
        }
        recipients = {
            doc["meta"]["actor"]["sharedInbox"] or doc["meta"]["actor"]["inbox"] for doc in self.DB.activities.find(q)
        }

        return list(recipients)

    def following(self) -> list[str]:
        q = {
            "box": Box.OUTBOX.value,
            "type": ap.ActivityType.FOLLOW.value,
            "meta.undo": False,
        }
        return [doc["activity"]["object"] for doc in self.DB.activities.find(q)]

    def parse_collection(self, payload: dict[str, Any] | None = None, url: str | None = None) -> list[str]:
        """Resolve/fetch a `Collection`/`OrderedCollection`."""
        # Resolve internal collections via MongoDB directly
        if url == f"{ID}/followers":
            return self.followers()
        if url == f"{ID}/following":
            return self.following()

        return super().parse_collection(payload, url)

    @ensure_it_is_me
    def outbox_is_blocked(self, as_actor: ap.Person, actor_id: str) -> bool:
        return bool(
            self.DB.activities.find_one(
                {
                    "box": Box.OUTBOX.value,
                    "type": ap.ActivityType.BLOCK.value,
                    "activity.object": actor_id,
                    "meta.undo": False,
                }
            )
        )

    def _fetch_iri(self, iri: str) -> ap.ObjectType | None:
        if iri == ME["id"]:
            return ME

        if iri == f"{ID}/followers":
            return {
                "type": ap.ActivityType.ORDERED_COLLECTION.value,
                "id": iri,
                "orderedItems": self.followers(),
            }
        if iri == f"{ID}/following":
            return {
                "type": ap.ActivityType.ORDERED_COLLECTION.value,
                "id": iri,
                "orderedItems": self.following(),
            }

        # Check if the activity is owned by this server
        if iri.startswith(BASE_URL):
            is_a_note = False
            if iri.endswith("/activity"):
                iri = iri.removesuffix("/activity")
                is_a_note = True
            data = self.DB.activities.find_one({"box": Box.OUTBOX.value, "remote_id": iri})
            if data and data["meta"]["deleted"]:
                raise ActivityGoneError(f"{iri} is gone")
            if data and is_a_note:
                return data["activity"]["object"]
            if data:
                return data["activity"]
        else:
            # Check if the activity is stored in the inbox
            data = self.DB.activities.find_one({"remote_id": iri})
            if data:
                if data["meta"]["deleted"]:
                    raise ActivityGoneError(f"{iri} is gone")
                return data["activity"]

        return None

    async def fetch_iri(self, iri: str, **kwargs) -> ap.ObjectType:
        if iri == ME["id"]:
            return ME

        if iri in ACTORS_CACHE:
            logger.info(f"{iri} found in cache")
            return ACTORS_CACHE[iri]

        # data = self.DB.actors.find_one({"remote_id": iri})
        # if data:
        #    if ap._has_type(data["type"], ap.ACTOR_TYPES):
        #        logger.info(f"{iri} found in DB cache")
        #        ACTORS_CACHE[iri] = data["data"]
        #    return data["data"]

        data = self._fetch_iri(iri)
        if data is None:
            # Fetch the URL via HTTP
            logger.info(f"dereference {iri} via HTTP")
            return await super().fetch_iri(iri, **kwargs)

        logger.debug(f"_fetch_iri({iri!r}) == {data!r}")
        if ap._has_type(data["type"], ap.ACTOR_TYPES):
            logger.debug(f"caching actor {iri}")
            # Cache the actor
            self.DB.actors.update_one(
                {"remote_id": iri},
                {"$set": {"remote_id": iri, "data": data}},
                upsert=True,
            )
            ACTORS_CACHE[iri] = data

        return data

    @ensure_it_is_me
    def inbox_check_duplicate(self, as_actor: ap.Person, iri: str) -> bool:
        return bool(self.DB.activities.find_one({"box": Box.INBOX.value, "remote_id": iri}))

    @ensure_it_is_me
    def inbox_has_active_follower(self, as_actor: ap.Person, actor_id: str) -> bool:
        """True when actor_id already has a non-undone Follow.

        A follower relationship is keyed by actor, not by activity: a new
        Follow id from an already-following actor is a no-op. Undone
        follows are excluded, so re-follow after Undo still goes through.
        """
        return bool(
            self.DB.activities.find_one(
                {
                    "box": Box.INBOX.value,
                    "type": ap.ActivityType.FOLLOW.value,
                    "activity.actor": actor_id,
                    "meta.undo": False,
                }
            )
        )

    def set_post_to_remote_inbox(self, cb):
        self.post_to_remote_inbox_cb = cb

    @ensure_it_is_me
    def undo_new_follower(self, as_actor: ap.Person, follow: ap.Follow) -> None:
        self.DB.activities.update_one({"remote_id": follow.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def undo_new_following(self, as_actor: ap.Person, follow: ap.Follow) -> None:
        self.DB.activities.update_one({"remote_id": follow.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def inbox_like(self, as_actor: ap.Person, like: ap.Like) -> None:
        obj = like.get_object_sync()
        # Update the meta counter if the object is published by the server
        self.DB.activities.update_one(
            {"box": Box.OUTBOX.value, "activity.object.id": obj.id},
            {"$inc": {"meta.count_like": 1}},
        )

    @ensure_it_is_me
    def inbox_undo_like(self, as_actor: ap.Person, like: ap.Like) -> None:
        obj = like.get_object_sync()
        # Update the meta counter if the object is published by the server
        self.DB.activities.update_one(
            {"box": Box.OUTBOX.value, "activity.object.id": obj.id},
            {"$inc": {"meta.count_like": -1}},
        )
        self.DB.activities.update_one({"remote_id": like.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def outbox_like(self, as_actor: ap.Person, like: ap.Like) -> None:
        obj = like.get_object_sync()
        self.DB.activities.update_one(
            {"activity.object.id": obj.id},
            {"$inc": {"meta.count_like": 1}, "$set": {"meta.liked": like.id}},
        )

    @ensure_it_is_me
    def outbox_undo_like(self, as_actor: ap.Person, like: ap.Like) -> None:
        obj = like.get_object_sync()
        self.DB.activities.update_one(
            {"activity.object.id": obj.id},
            {"$inc": {"meta.count_like": -1}, "$set": {"meta.liked": False}},
        )
        self.DB.activities.update_one({"remote_id": like.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def inbox_announce(self, as_actor: ap.Person, announce: ap.Announce) -> None:
        # TODO(tsileo): actually drop it without storing it and better logging, also move the check somewhere else
        # or remove it?
        try:
            obj = announce.get_object_sync()
        except NotAnActivityError:
            logger.exception(
                f"received an Annouce referencing an OStatus notice ({announce._data['object']}), dropping the message"
            )
            return

        self.DB.activities.update_one(
            {"remote_id": announce.id},
            {
                "$set": {
                    "meta.object": obj.to_dict(embed=True),
                    "meta.object_actor": _actor_to_meta(obj.get_actor_sync()),
                }
            },
        )
        self.DB.activities.update_one({"activity.object.id": obj.id}, {"$inc": {"meta.count_boost": 1}})

    @ensure_it_is_me
    def inbox_undo_announce(self, as_actor: ap.Person, announce: ap.Announce) -> None:
        obj = announce.get_object_sync()
        # Update the meta counter if the object is published by the server
        self.DB.activities.update_one({"activity.object.id": obj.id}, {"$inc": {"meta.count_boost": -1}})
        self.DB.activities.update_one({"remote_id": announce.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def outbox_announce(self, as_actor: ap.Person, announce: ap.Announce) -> None:
        obj = announce.get_object_sync()
        self.DB.activities.update_one(
            {"remote_id": announce.id},
            {
                "$set": {
                    "meta.object": obj.to_dict(embed=True),
                    "meta.object_actor": _actor_to_meta(obj.get_actor_sync()),
                }
            },
        )

        self.DB.activities.update_one({"activity.object.id": obj.id}, {"$set": {"meta.boosted": announce.id}})

    @ensure_it_is_me
    def outbox_undo_announce(self, as_actor: ap.Person, announce: ap.Announce) -> None:
        obj = announce.get_object_sync()
        self.DB.activities.update_one({"activity.object.id": obj.id}, {"$set": {"meta.boosted": False}})
        self.DB.activities.update_one({"remote_id": announce.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def inbox_delete(self, as_actor: ap.Person, delete: ap.Delete) -> None:
        obj = delete.get_object_sync()
        logger.debug(f"delete object={obj!r}")
        self.DB.activities.update_one({"activity.object.id": obj.id}, {"$set": {"meta.deleted": True}})

        logger.info(f"inbox_delete handle_replies obj={obj!r}")
        in_reply_to = obj.inReplyTo
        if delete.get_object_sync().ACTIVITY_TYPE != ap.ActivityType.NOTE:
            create_doc = self.DB.activities.find_one(
                {
                    "activity.object.id": delete.get_object_sync().id,
                    "type": ap.ActivityType.CREATE.value,
                }
            )
            if not create_doc:
                return
            in_reply_to = create_doc["activity"]["object"].get("inReplyTo")

        # Fake a Undo so any related Like/Announce doesn't appear on the web UI
        self.DB.activities.update_many(
            {"meta.object.id": obj.id},
            {"$set": {"meta.undo": True, "meta.extra": "object deleted"}},
        )
        if in_reply_to:
            self._handle_replies_delete(as_actor, in_reply_to)

    @ensure_it_is_me
    def outbox_delete(self, as_actor: ap.Person, delete: ap.Delete) -> None:
        self.DB.activities.update_one(
            {"activity.object.id": delete.get_object_sync().id},
            {"$set": {"meta.deleted": True}},
        )
        obj = delete.get_object_sync()
        if delete.get_object_sync().ACTIVITY_TYPE != ap.ActivityType.NOTE:
            create_doc = self.DB.activities.find_one(
                {
                    "activity.object.id": delete.get_object_sync().id,
                    "type": ap.ActivityType.CREATE.value,
                }
            )
            if not create_doc:
                return
            obj = ap.parse_activity(create_doc["activity"]).get_object_sync()

        self.DB.activities.update_many(
            {"meta.object.id": obj.id},
            {"$set": {"meta.undo": True, "meta.extra": "object deleted"}},
        )

        self._handle_replies_delete(as_actor, obj.inReplyTo)

    @ensure_it_is_me
    def inbox_update(self, as_actor: ap.Person, update: ap.Update) -> None:
        obj = update.get_object_sync()
        if obj.ACTIVITY_TYPE == ap.ActivityType.NOTE:
            self.DB.activities.update_one(
                {"activity.object.id": obj.id},
                {"$set": {"activity.object": obj.to_dict()}},
            )
        # FIXME(tsileo): handle update actor amd inbox_update_note/inbox_update_actor

    @ensure_it_is_me
    def outbox_update(self, as_actor: ap.Person, _update: ap.Update) -> None:
        obj = _update._data["object"]

        update_prefix = "activity.object."
        update_set: dict[str, Any] = {
            f"{update_prefix}updated": (datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        }
        update_unset: dict[str, Any] = {}
        for k, v in obj.items():
            if k in ("id", "type"):
                continue
            if v is None:
                update_unset[f"{update_prefix}{k}"] = ""
            else:
                update_set[f"{update_prefix}{k}"] = v

        update: dict[str, Any] = {"$set": update_set}
        if update_unset:
            update["$unset"] = update_unset

        logger.info(f"updating note from outbox {obj!r} {update}")
        self.DB.activities.update_one({"activity.object.id": obj["id"]}, update)
        # FIXME(tsileo): should send an Update (but not a partial one, to all the note's recipients
        # (create a new Update with the result of the update, and send it without saving it?)

    @ensure_it_is_me
    def outbox_create(self, as_actor: ap.Person, create: ap.Create) -> None:
        self._handle_replies(as_actor, create)

    @ensure_it_is_me
    def inbox_create(self, as_actor: ap.Person, create: ap.Create) -> None:
        self._handle_replies(as_actor, create)

    @ensure_it_is_me
    def _handle_replies_delete(self, as_actor: ap.Person, in_reply_to: str | None) -> None:
        if not in_reply_to:
            return

        self.DB.activities.update_one(
            {"activity.object.id": in_reply_to},
            {"$inc": {"meta.count_reply": -1, "meta.count_direct_reply": -1}},
        )

    @ensure_it_is_me
    def _handle_replies(self, as_actor: ap.Person, create: ap.Create) -> None:
        """Go up to the root reply, store unknown replies in the `threads` DB and set the "meta.thread_root_parent"
        key to make it easy to query a whole thread."""
        in_reply_to = create.get_object_sync().inReplyTo
        if not in_reply_to:
            return

        new_threads = []
        root_reply = in_reply_to
        reply = None
        try:
            reply = ap.fetch_remote_activity_sync(root_reply)
        except (Error, Exception) as err:
            logger.info(f"reply target {root_reply} not fetchable ({err}), skipping thread walk")

        creply = self.DB.activities.find_one_and_update(
            {"activity.object.id": in_reply_to},
            {"$inc": {"meta.count_reply": 1, "meta.count_direct_reply": 1}},
        )
        if not creply and reply is not None:
            # It means the activity is not in the inbox, and not in the outbox, we want to save it
            self.save(Box.REPLIES, reply)
            new_threads.append(reply.id)

        seen = {root_reply}
        while reply is not None:
            in_reply_to = getattr(reply, "inReplyTo", None)
            if not in_reply_to or in_reply_to in seen:
                break
            seen.add(in_reply_to)
            root_reply = in_reply_to
            try:
                reply = ap.fetch_remote_activity_sync(root_reply)
            except (Error, Exception) as err:
                logger.info(f"reply target {root_reply} not fetchable ({err}), stopping thread walk")
                break
            q = {"activity.object.id": root_reply}
            if not self.DB.activities.count_documents(q):
                self.save(Box.REPLIES, reply)
                new_threads.append(reply.id)

        self.DB.activities.update_one({"remote_id": create.id}, {"$set": {"meta.thread_root_parent": root_reply}})
        if new_threads:
            self.DB.activities.update_many(
                {"box": Box.REPLIES.value, "remote_id": {"$in": new_threads}},
                {"$set": {"meta.thread_root_parent": root_reply}},
            )

    def post_to_outbox(self, activity: ap.BaseActivity) -> None:
        if activity.has_type(ap.CREATE_TYPES):
            activity = activity.build_create()

        self.save(Box.OUTBOX, activity)

        # Assign create a random ID
        obj_id = self.random_object_id()
        activity.set_id(self.activity_url(obj_id), obj_id)

        recipients = activity.recipients()
        logger.info(f"recipients={recipients}")
        activity = ap.clean_activity(activity.to_dict())

        payload = json_dumps(activity)
        for recp in recipients:
            logger.debug(f"posting to {recp}")
            self.post_to_remote_inbox(self.get_actor_sync(), payload, recp)


def gen_feed():
    fg = FeedGenerator()
    fg.id(f"{ID}")
    fg.title(f"{USERNAME} notes")
    fg.author({"name": USERNAME, "email": "t@a4.io"})
    fg.link(href=ID, rel="alternate")
    fg.description(f"{USERNAME} notes")
    fg.logo(ME.get("icon", {}).get("url"))
    fg.language("en")
    for item in DB.activities.find({"box": Box.OUTBOX.value, "type": "Create", "meta.deleted": False}, limit=10).sort(
        "_id", -1
    ):
        fe = fg.add_entry()
        fe.id(item["activity"]["object"].get("url"))
        fe.link(href=item["activity"]["object"].get("url"))
        fe.title(item["activity"]["object"]["content"])
        fe.description(item["activity"]["object"]["content"])
    return fg


def _feed_item(item: dict[str, Any], author: dict[str, Any] | None = None) -> dict[str, Any]:
    """One JSON Feed entry; `author` is only set for inbox activities."""
    note = item["activity"]["object"]
    entry = {
        "id": item["activity"]["id"],
        "url": note.get("url"),
        "content_html": note["content"],
        "content_text": html2text(note["content"]),
        "date_published": note.get("published"),
    }
    if author is not None:
        entry["author"] = author
    return entry


def json_feed(path: str) -> dict[str, Any]:
    """JSON Feed (https://jsonfeed.org/) document."""
    items = DB.activities.find({"box": Box.OUTBOX.value, "type": "Create", "meta.deleted": False}, limit=10).sort(
        "_id", -1
    )
    data = [_feed_item(item) for item in items]
    return {
        "version": "https://jsonfeed.org/version/1",
        "user_comment": (
            f"This is a micronote.pub feed. You can add this to your feed reader using the following URL: {ID}{path}"
        ),
        "title": USERNAME,
        "home_page_url": ID,
        "feed_url": f"{ID}{path}",
        "author": {
            "name": USERNAME,
            "url": ID,
            "avatar": ME.get("icon", {}).get("url"),
        },
        "items": data,
    }


def build_inbox_json_feed(path: str, request_cursor: str | None = None) -> dict[str, Any]:
    """Build a JSON feed from the inbox activities."""
    q: dict[str, Any] = {
        "type": "Create",
        "meta.deleted": False,
        "box": Box.INBOX.value,
    }
    if request_cursor:
        try:
            q["_id"] = {"$lt": ObjectId(request_cursor)}
        except Exception:
            abort(400)

    items = list(DB.activities.find(q, limit=50).sort("_id", -1))

    missing_iris = {
        item.get("activity", {}).get("actor")
        for item in items
        if not item.get("meta", {}).get("actor") and item.get("activity", {}).get("actor")
    }
    cached_actors: dict[str, dict[str, Any]] = {}
    if missing_iris:
        for actor_doc in DB.actors.find({"remote_id": {"$in": list(missing_iris)}}):
            remote_id = actor_doc.get("remote_id")
            if remote_id and actor_doc.get("data"):
                cached_actors[remote_id] = actor_doc["data"]

    data = []
    for item in items:
        activity = item.get("activity", {})
        actor_iri = activity.get("actor")
        meta_actor = item.get("meta", {}).get("actor")
        if not meta_actor and actor_iri:
            meta_actor = cached_actors.get(actor_iri, {})

        if not isinstance(meta_actor, dict):
            meta_actor = {}

        name = meta_actor.get("name") or meta_actor.get("preferredUsername") or actor_iri or ""
        url = meta_actor.get("url") or actor_iri or ""
        icon = meta_actor.get("icon")
        avatar = icon.get("url") if isinstance(icon, dict) else None

        author_info = {
            "name": name,
            "url": url,
            "avatar": avatar,
        }
        data.append(_feed_item(item, author=author_info))
    cursor = str(items[-1]["_id"]) if items else None
    resp = {
        "version": "https://jsonfeed.org/version/1",
        "title": f"{USERNAME}'s stream",
        "home_page_url": ID,
        "feed_url": f"{ID}{path}",
        "items": data,
    }
    if cursor and len(data) == 50:
        resp["next_url"] = f"{ID}{path}?cursor={cursor}"

    return resp


def embed_collection(total_items, first_page_id):
    """Helper creating a root OrderedCollection with a link to the first page."""
    return {
        "type": ap.ActivityType.ORDERED_COLLECTION.value,
        "totalItems": total_items,
        "first": f"{first_page_id}?page=first",
        "id": first_page_id,
    }


def simple_build_ordered_collection(col_name, data):
    return {
        "@context": ap.COLLECTION_CTX,
        "id": f"{BASE_URL}/{col_name}",
        "totalItems": len(data),
        "type": ap.ActivityType.ORDERED_COLLECTION.value,
        "orderedItems": data,
    }


def build_ordered_collection(col, q=None, cursor=None, map_func=None, limit=50, col_name=None, first_page=False):
    """Helper for building an OrderedCollection from a MongoDB query (with pagination support)."""
    col_name = col_name or col.name
    collection_id = f"{BASE_URL}/{col_name}"
    base_q = q.copy() if q is not None else {}
    total_items = col.count_documents(base_q)

    query_with_cursor = base_q.copy()
    if cursor:
        try:
            query_with_cursor["_id"] = {"$lt": ObjectId(cursor)}
        except Exception:
            abort(400)
    data = list(col.find(query_with_cursor, limit=limit).sort("_id", -1))

    if not data:
        # Returns an empty page if there's a cursor
        if cursor:
            return {
                "@context": ap.COLLECTION_CTX,
                "type": ap.ActivityType.ORDERED_COLLECTION_PAGE.value,
                "id": f"{collection_id}?cursor={cursor}",
                "partOf": collection_id,
                "totalItems": total_items,
                "orderedItems": [],
            }
        return {
            "@context": ap.COLLECTION_CTX,
            "id": collection_id,
            "totalItems": total_items,
            "type": ap.ActivityType.ORDERED_COLLECTION.value,
            "orderedItems": [],
        }

    start_cursor = str(data[0]["_id"])
    next_page_cursor = str(data[-1]["_id"])

    data = [_remove_id(doc) for doc in data]
    if map_func:
        data = [map_func(doc) for doc in data]

    page = {
        "id": f"{collection_id}?cursor={start_cursor}",
        "orderedItems": data,
        "partOf": collection_id,
        "totalItems": total_items,
        "type": ap.ActivityType.ORDERED_COLLECTION_PAGE.value,
    }
    if len(data) == limit:
        page["next"] = f"{collection_id}?cursor={next_page_cursor}"

    # No cursor, this is the first page and we return an OrderedCollection
    if not cursor:
        if first_page:
            return page
        return {
            "@context": ap.COLLECTION_CTX,
            "id": collection_id,
            "totalItems": total_items,
            "type": ap.ActivityType.ORDERED_COLLECTION.value,
            "first": page,
        }

    # If there's a cursor, then we return an OrderedCollectionPage
    # XXX(tsileo): implements prev with prev=<first item cursor>?
    return {"@context": ap.COLLECTION_CTX, **page}
