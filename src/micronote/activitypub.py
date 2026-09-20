import json
import logging
import os
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from active_boxes import activitypub as ap
from active_boxes import strtobool
from active_boxes.activitypub import _to_list
from active_boxes.backend import Backend
from active_boxes.errors import (
    ActivityGoneError,
    ActivityNotFoundError,
    ActivityUnavailableError,
    Error,
    NotAnActivityError,
)
from active_boxes.http_client import get_http_client
from active_boxes.urlutils import URLLookupFailedError
from cachetools import LRUCache

from micronote.boxes import Box
from micronote.config import (
    BASE_URL,
    DB_NAME,
    EXTRA_INBOXES,
    ID,
    KEY,
    ME,
    USER_AGENT,
    create_db_client,
)
from micronote.utils.delivery import sign_fetch_request
from micronote.utils.emoji import extract_custom_emojis

logger = logging.getLogger(__name__)

ACTORS_CACHE: LRUCache[str, Any] = LRUCache(maxsize=256)
AUTHORIZED_FETCH = strtobool(os.getenv("MICRONOTE_AUTHORIZED_FETCH", "true"))


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


def actor_to_meta(actor: ap.BaseActivity, with_inbox: bool = False) -> dict[str, Any]:
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


def safe_object_actor_meta(obj: ap.BaseActivity | ap.BaseObject) -> dict[str, Any] | None:
    """Safely extracts actor metadata for an object, falling back to attributedTo if remote fetch fails."""
    actor_meta = None
    try:
        actor = obj.get_actor_sync()
        if actor:
            actor_meta = actor_to_meta(actor)
    except (ActivityGoneError, ActivityNotFoundError, NotAnActivityError):
        logger.warning(f"object actor for {obj!r} gone or not found")
    except (Error, Exception) as err:
        logger.warning(f"unable to fetch object actor for {obj!r}: {err}")

    if not actor_meta:
        attributed_to = getattr(obj, "attributedTo", None)
        if not attributed_to and hasattr(obj, "_data") and isinstance(obj._data, dict):
            attributed_to = obj._data.get("attributedTo") or obj._data.get("actor")

        if isinstance(attributed_to, list) and attributed_to:
            attributed_to = attributed_to[0]

        if isinstance(attributed_to, dict):
            attributed_to = attributed_to.get("id") or attributed_to.get("url")

        if attributed_to and isinstance(attributed_to, str):
            actor_meta = {
                "id": attributed_to,
                "url": attributed_to,
                "icon": None,
                "name": attributed_to,
                "preferredUsername": None,
                "emojis": {},
            }
    return actor_meta


def ensure_it_is_me(f):
    """Method decorator used to track the events fired during tests."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        if args[1].id != ME["id"]:
            raise Error("unexpected actor")
        return f(*args, **kwargs)

    return wrapper


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

    async def _fetch_remote_iri(self, iri: str, **kwargs) -> ap.ObjectType:
        """Fetch remote IRI, signing request with HTTP Signatures for Authorized Fetch."""
        if AUTHORIZED_FETCH and KEY and getattr(KEY, "privkey", None):
            try:
                headers = sign_fetch_request(iri, KEY, self.user_agent())
                try:
                    await self.check_url(iri)
                except URLLookupFailedError as url_err:
                    raise ActivityUnavailableError(f"unable to fetch {iri}, url lookup failed") from url_err

                client = await get_http_client()
                kwargs_copy = dict(kwargs)
                kwargs_copy.setdefault("debug", self.debug_mode())
                return await client.get_json(iri, headers=headers, **kwargs_copy)
            except (ActivityNotFoundError, ActivityGoneError):
                raise
            except ActivityUnavailableError as err:
                logger.debug(f"signed fetch failed for {iri}: {err}, trying unsigned fallback")
            except Exception as err:
                logger.debug(f"signed fetch exception for {iri}: {err}, trying unsigned fallback")

        return await super().fetch_iri(iri, **kwargs)

    async def fetch_iri(self, iri: str, **kwargs) -> ap.ObjectType:
        logger.info(f"fetch_iri {iri!r}")
        if iri == ME["id"]:
            return ME

        if iri in ACTORS_CACHE:
            logger.info(f"{iri} found in cache")
            return ACTORS_CACHE[iri]

        data = self._fetch_iri(iri)
        if data is None:
            # Fetch the URL via HTTP
            logger.info(f"dereference {iri} via HTTP")
            return await self._fetch_remote_iri(iri, **kwargs)

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

    @ensure_it_is_me
    def undo_new_follower(self, as_actor: ap.Person, follow: ap.Follow) -> None:
        self.DB.activities.update_one({"remote_id": follow.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def undo_new_following(self, as_actor: ap.Person, follow: ap.Follow) -> None:
        self.DB.activities.update_one({"remote_id": follow.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def inbox_like(self, as_actor: ap.Person, like: ap.Like) -> None:
        try:
            obj = like.get_object_sync()
        except (Error, Exception) as err:
            logger.warning(f"failed to fetch object for inbox like {like.id}: {err}")
            return
        # Update the meta counter if the object is published by the server
        self.DB.activities.update_one(
            {"box": Box.OUTBOX.value, "activity.object.id": obj.id},
            {"$inc": {"meta.count_like": 1}},
        )

    @ensure_it_is_me
    def inbox_undo_like(self, as_actor: ap.Person, like: ap.Like) -> None:
        try:
            obj = like.get_object_sync()
        except (Error, Exception) as err:
            logger.warning(f"failed to fetch object for inbox undo like {like.id}: {err}")
            obj = None
        if obj:
            # Update the meta counter if the object is published by the server
            self.DB.activities.update_one(
                {"box": Box.OUTBOX.value, "activity.object.id": obj.id},
                {"$inc": {"meta.count_like": -1}},
            )
        self.DB.activities.update_one({"remote_id": like.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def outbox_like(self, as_actor: ap.Person, like: ap.Like) -> None:
        try:
            obj = like.get_object_sync()
        except (Error, Exception) as err:
            logger.warning(f"failed to fetch object for outbox like {like.id}: {err}")
            return
        self.DB.activities.update_one(
            {"activity.object.id": obj.id},
            {"$inc": {"meta.count_like": 1}, "$set": {"meta.liked": like.id}},
        )

    @ensure_it_is_me
    def outbox_undo_like(self, as_actor: ap.Person, like: ap.Like) -> None:
        try:
            obj = like.get_object_sync()
        except (Error, Exception) as err:
            logger.warning(f"failed to fetch object for outbox undo like {like.id}: {err}")
            obj = None
        if obj:
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
        except (ActivityGoneError, ActivityNotFoundError, NotAnActivityError):
            logger.warning(
                f"received an Announce referencing missing/gone object ({announce._data.get('object')}), dropping message"
            )
            return
        except (ActivityUnavailableError, Error, Exception) as err:
            logger.warning(f"failed to fetch object for Announce {announce.id}: {err}, dropping message")
            return

        actor_meta = safe_object_actor_meta(obj)
        update_payload: dict[str, Any] = {
            "meta.object": obj.to_dict(embed=True),
        }
        if actor_meta:
            update_payload["meta.object_actor"] = actor_meta

        self.DB.activities.update_one(
            {"remote_id": announce.id},
            {"$set": update_payload},
        )
        self.DB.activities.update_one({"activity.object.id": obj.id}, {"$inc": {"meta.count_boost": 1}})

    @ensure_it_is_me
    def inbox_undo_announce(self, as_actor: ap.Person, announce: ap.Announce) -> None:
        try:
            obj = announce.get_object_sync()
        except (Error, Exception) as err:
            logger.warning(f"failed to fetch object for inbox undo announce {announce.id}: {err}")
            obj = None
        if obj:
            # Update the meta counter if the object is published by the server
            self.DB.activities.update_one({"activity.object.id": obj.id}, {"$inc": {"meta.count_boost": -1}})
        self.DB.activities.update_one({"remote_id": announce.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def outbox_announce(self, as_actor: ap.Person, announce: ap.Announce) -> None:
        try:
            obj = announce.get_object_sync()
        except (Error, Exception) as err:
            logger.warning(f"failed to fetch object for outbox Announce {announce.id}: {err}")
            return

        actor_meta = safe_object_actor_meta(obj)
        update_payload: dict[str, Any] = {
            "meta.object": obj.to_dict(embed=True),
        }
        if actor_meta:
            update_payload["meta.object_actor"] = actor_meta

        self.DB.activities.update_one(
            {"remote_id": announce.id},
            {"$set": update_payload},
        )

        self.DB.activities.update_one({"activity.object.id": obj.id}, {"$set": {"meta.boosted": announce.id}})

    @ensure_it_is_me
    def outbox_undo_announce(self, as_actor: ap.Person, announce: ap.Announce) -> None:
        try:
            obj = announce.get_object_sync()
        except (Error, Exception) as err:
            logger.warning(f"failed to fetch object for outbox undo announce {announce.id}: {err}")
            obj = None
        if obj:
            self.DB.activities.update_one({"activity.object.id": obj.id}, {"$set": {"meta.boosted": False}})
        self.DB.activities.update_one({"remote_id": announce.id}, {"$set": {"meta.undo": True}})

    @ensure_it_is_me
    def inbox_delete(self, as_actor: ap.Person, delete: ap.Delete) -> None:
        obj = delete.get_object_sync()
        logger.debug(f"delete object={obj!r}")
        self.DB.activities.update_one({"activity.object.id": obj.id}, {"$set": {"meta.deleted": True}})

        logger.info(f"inbox_delete handle_replies obj={obj!r}")
        in_reply_to = obj.inReplyTo
        if obj.ACTIVITY_TYPE != ap.ActivityType.NOTE:
            create_doc = self.DB.activities.find_one(
                {
                    "activity.object.id": obj.id,
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
        obj = delete.get_object_sync()
        self.DB.activities.update_one(
            {"activity.object.id": obj.id},
            {"$set": {"meta.deleted": True}},
        )
        if obj.ACTIVITY_TYPE != ap.ActivityType.NOTE:
            create_doc = self.DB.activities.find_one(
                {
                    "activity.object.id": obj.id,
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
