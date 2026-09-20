"""Job handlers: the domain work the worker runs.

Queue mechanics (claiming, retries, the watch loop) live in `worker.py`;
this module only contains the handlers and their shared helpers.
"""

import json
import logging

import requests
from active_boxes import activitypub as ap
from active_boxes.activitypub import _to_list
from active_boxes.errors import (
    ActivityGoneError,
    ActivityNotFoundError,
    ActivityUnavailableError,
    BadActivityError,
    Error,
    NotAnActivityError,
)
from active_boxes.linked_data_sig import generate_signature
from requests.exceptions import HTTPError

from micronote import activitypub, cache, tasks
from micronote.config import BASE_URL, DB, ID, MEDIA_CACHE, key, user_agent
from micronote.instance import MY_PERSON, back
from micronote.jobs import enqueue_job
from micronote.utils import opengraph
from micronote.utils.delivery import sign_delivery_request
from micronote.utils.emoji import extract_custom_emojis
from micronote.utils.media import Kind

log = logging.getLogger(__name__)


def cache_object_media(obj_data: dict, actor_meta: dict | None = None) -> None:
    """Downloads and stores an object's emojis, image attachments, and the
    author's avatar/emojis. Best-effort: failures are logged, never raised."""
    for emoji_url in extract_custom_emojis(obj_data.get("tag", [])).values():
        try:
            MEDIA_CACHE.cache(emoji_url, Kind.CUSTOM_EMOJI)
        except Exception:
            log.exception(f"failed to cache object emoji {emoji_url}")

    for attachment in obj_data.get("attachment", []):
        if not isinstance(attachment, dict):
            continue
        url = attachment.get("url")
        if not url:
            continue
        media_type = attachment.get("mediaType") or ""
        if media_type.startswith("image/") or attachment.get("type") == ap.ActivityType.IMAGE.value:
            try:
                MEDIA_CACHE.cache(url, Kind.ATTACHMENT)
            except Exception:
                log.exception(f"failed to cache object attachment {attachment}")

    if not actor_meta:
        return
    for emoji_url in (actor_meta.get("emojis") or {}).values():
        try:
            MEDIA_CACHE.cache(emoji_url, Kind.CUSTOM_EMOJI)
        except Exception:
            log.exception(f"failed to cache actor emoji {emoji_url}")

    icon = actor_meta.get("icon")
    icon_url = icon.get("url") if isinstance(icon, dict) else (icon if isinstance(icon, str) else None)
    if icon_url:
        try:
            MEDIA_CACHE.cache(icon_url, Kind.ACTOR_ICON)
        except Exception:
            log.exception(f"failed to cache actor icon {icon_url}")


def process_new_activity(job) -> None:
    """Process an activity received in the inbox"""
    iri = job["iri"]
    try:
        activity = ap.fetch_remote_activity_sync(iri)
        log.info(f"activity={activity!r}")

        # Is the activity expected?
        # following = ap.get_backend().following()
        should_forward = False
        should_delete = False

        tag_stream = False
        if activity.has_type(ap.ActivityType.ANNOUNCE):
            try:
                activity.get_object_sync()
                tag_stream = True
            except (NotAnActivityError, BadActivityError):
                log.exception(f"failed to get announce object for {activity!r}")
                # Most likely on OStatus notice
                tag_stream = False
                should_delete = True
            except (ActivityGoneError, ActivityNotFoundError):
                # The announced activity is deleted/gone, drop it
                should_delete = True
            except ActivityUnavailableError as err:
                log.warning(f"announce object for {activity!r} unavailable ({err})")
                tag_stream = False

        elif activity.has_type(ap.ActivityType.CREATE):
            note = activity.get_object_sync()
            # The note joins the stream if it starts a thread or continues a local one.
            if not note.inReplyTo or note.inReplyTo.startswith(ID):
                tag_stream = True

            if note.inReplyTo:
                try:
                    # Fetch for effect: OStatus notices raise NotAnActivityError
                    # (dropped below); gone targets raise through to the outer
                    # handler, which drops the activity without flagging it.
                    ap.fetch_remote_activity_sync(note.inReplyTo)
                except NotAnActivityError:
                    # Most likely a reply to an OStatus notice; don't keep it.
                    should_delete = True
                except Error as err:
                    log.warning(f"inReplyTo {note.inReplyTo} not fetchable: {err}")

            # Forward a reply to our followers only when the author explicitly
            # addressed our followers collection AND the reply continues a local
            # thread (ActivityPub inbox-forwarding criteria; avoids amplifying
            # mentions that were never addressed to our followers).
            if note.inReplyTo and note.inReplyTo.startswith(ID):
                local_followers = ID + "/followers"
                for field in ["to", "cc"]:
                    if local_followers in _to_list(activity._data.get(field, [])):
                        should_forward = True
                        break

        elif activity.has_type(ap.ActivityType.DELETE):
            note = DB.activities.find_one({"activity.object.id": activity.get_object_sync().id})
            if note and note["meta"].get("forwarded", False):
                # If the activity was originally forwarded, forward the delete too
                should_forward = True

        elif activity.has_type(ap.ActivityType.LIKE):
            if not activity.get_object_id().startswith(BASE_URL):
                # We only want to keep a like if it's a like for a local activity
                # (Pleroma relay the likes it received, we don't want to store them)
                should_delete = True

        if should_forward:
            log.info(f"will forward {activity!r} to followers")
            enqueue_job("forward_activity", iri=activity.id)

        if should_delete:
            log.info(f"will soft delete {activity!r}")

        log.info(f"{iri} tag_stream={tag_stream}")
        DB.activities.update_one(
            {"remote_id": activity.id},
            {
                "$set": {
                    "meta.stream": tag_stream,
                    "meta.forwarded": should_forward,
                    "meta.deleted": should_delete,
                }
            },
        )

        log.info(f"new activity {iri} processed")
        if not should_delete and not activity.has_type(ap.ActivityType.DELETE):
            enqueue_job("cache_actor", iri=iri)
    except (ActivityGoneError, ActivityNotFoundError):
        log.exception(f"dropping activity {iri}, skip processing")
    except Exception:
        log.exception(f"failed to process new activity {iri}")
        raise


def fetch_og_metadata(job) -> None:
    iri = job["iri"]
    try:
        activity = ap.fetch_remote_activity_sync(iri)
        log.info(f"activity={activity!r}")
        if activity.has_type(ap.ActivityType.CREATE):
            note = activity.get_object_sync()
            links = opengraph.links_from_note(note.to_dict())
            og_metadata = opengraph.fetch_og_metadata(user_agent(), links)
            for og in og_metadata:
                if not og.get("image"):
                    continue
                try:
                    MEDIA_CACHE.cache_og_image(og["image"])
                except Exception:
                    log.warning(f"failed to cache OG image {og.get('image')}")

            log.debug(f"OG metadata {og_metadata!r}")
            DB.activities.update_one({"remote_id": iri}, {"$set": {"meta.og_metadata": og_metadata}})

        log.info(f"OG metadata fetched for {iri}")
    except (ActivityGoneError, ActivityNotFoundError):
        log.exception(f"dropping activity {iri}, skip OG metedata")
    except ActivityUnavailableError as err:
        log.warning(f"remote activity {iri} unavailable ({err}), skipping fetch_og_metadata without retry")
    except requests.exceptions.HTTPError as http_err:
        if http_err.response is not None and 400 <= http_err.response.status_code < 500:
            log.exception("bad request, no retry")
            return
        log.exception("failed to fetch OG metadata")
        raise
    except Exception:
        log.exception(f"failed to fetch OG metadata for {iri}")
        raise


def cache_object(job) -> None:
    iri = job["iri"]
    try:
        activity = ap.fetch_remote_activity_sync(iri)
        log.info(f"activity={activity!r}")

        try:
            obj = activity.get_object_sync()
        except (ActivityGoneError, ActivityNotFoundError, NotAnActivityError):
            DB.activities.update_one({"remote_id": iri}, {"$set": {"meta.deleted": True}})
            log.exception(f"flagging activity {iri} as deleted, object gone/not found")
            return
        except ActivityUnavailableError as err:
            log.warning(f"remote object for {iri} unavailable ({err}), skipping object cache")
            return

        actor_meta = activitypub.safe_object_actor_meta(obj)

        update_payload = {
            "meta.object": obj.to_dict(embed=True),
        }
        if actor_meta:
            update_payload["meta.object_actor"] = actor_meta

        DB.activities.update_one(
            {"remote_id": activity.id},
            {"$set": update_payload},
        )

        obj_data = getattr(obj, "_data", None) or obj.to_dict()
        cache_object_media(obj_data, actor_meta)
    except (ActivityGoneError, ActivityNotFoundError, NotAnActivityError):
        DB.activities.update_one({"remote_id": iri}, {"$set": {"meta.deleted": True}})
        log.exception(f"flagging activity {iri} as deleted, no object caching")
    except ActivityUnavailableError as err:
        log.warning(f"remote activity {iri} unavailable ({err}), skipping cache_object without retry")
    except Exception:
        log.exception(f"failed to cache object for {iri}")
        raise


def cache_actor(job) -> None:
    iri = job["iri"]
    also_cache_attachments = job.get("also_cache_attachments", True)
    try:
        activity = ap.fetch_remote_activity_sync(iri)
        log.info(f"activity={activity!r}")

        if activity.has_type(ap.ActivityType.CREATE):
            enqueue_job("fetch_og_metadata", iri=iri)

        if activity.has_type([ap.ActivityType.LIKE, ap.ActivityType.ANNOUNCE]):
            enqueue_job("cache_object", iri=iri)

        actor = None
        try:
            actor = activity.get_actor_sync()
        except (ActivityGoneError, ActivityNotFoundError):
            DB.activities.update_one({"remote_id": iri}, {"$set": {"meta.deleted": True}})
            log.exception(f"flagging activity {iri} as deleted, no actor caching")
            return
        except Error as err:
            log.warning(f"unable to fetch actor for {iri}: {err}")

        cache_actor_with_inbox = False
        if activity.has_type(ap.ActivityType.FOLLOW):
            if actor and actor.id != ID:
                # It's a Follow from the Inbox
                cache_actor_with_inbox = True
            else:
                # It's a new following, cache the "object" (which is the actor we follow)
                try:
                    follow_target = activity.get_object_sync()
                    if follow_target:
                        DB.activities.update_one(
                            {"remote_id": iri},
                            {"$set": {"meta.object": activitypub.actor_to_meta(follow_target)}},
                        )
                except Error as err:
                    log.warning(f"unable to cache follow target for {iri}: {err}")

        # Cache the actor info (or fallback to basic dict if remote profile returned 401/error)
        actor_meta = None
        if actor:
            actor_meta = activitypub.actor_to_meta(actor, cache_actor_with_inbox)
        else:
            actor_id = getattr(activity, "actor", None)
            if actor_id:
                actor_meta = {
                    "id": actor_id,
                    "url": actor_id,
                    "icon": None,
                    "name": actor_id,
                    "preferredUsername": None,
                    "emojis": {},
                }

        if actor_meta:
            DB.activities.update_one(
                {"remote_id": iri},
                {"$set": {"meta.actor": actor_meta}},
            )
            for emoji_url in (actor_meta.get("emojis") or {}).values():
                try:
                    MEDIA_CACHE.cache(emoji_url, Kind.CUSTOM_EMOJI)
                except Exception:
                    log.exception(f"failed to cache actor emoji {emoji_url}")

        log.info(f"actor cached for {iri}")
        if also_cache_attachments and activity.has_type(ap.ActivityType.CREATE):
            enqueue_job("cache_attachments", iri=iri)

    except (ActivityGoneError, ActivityNotFoundError):
        DB.activities.update_one({"remote_id": iri}, {"$set": {"meta.deleted": True}})
        log.exception(f"flagging activity {iri} as deleted, no actor caching")
    except ActivityUnavailableError as err:
        log.warning(f"remote activity {iri} unavailable ({err}), skipping cache_actor without retry")
    except Exception:
        log.exception(f"failed to cache actor for {iri}")
        raise


def cache_attachments(job) -> None:
    iri = job["iri"]
    try:
        activity = ap.fetch_remote_activity_sync(iri)
        log.info(f"activity={activity!r}")
        # Generates thumbnails for the actor's icon, attachments, and emojis if any

        actor = activity.get_actor_sync()

        # Update the cached actor
        DB.actors.update_one(
            {"remote_id": iri},
            {"$set": {"remote_id": iri, "data": actor.to_dict(embed=True)}},
            upsert=True,
        )

        note_data: dict = {}
        if activity.has_type(ap.ActivityType.CREATE):
            note_obj = activity.get_object_sync()
            note_data = getattr(note_obj, "_data", None) or note_obj.to_dict()

        cache_object_media(note_data, activitypub.actor_to_meta(actor))

        log.info(f"attachments cached for {iri}")

    except (ActivityGoneError, ActivityNotFoundError, NotAnActivityError):
        log.exception(f"dropping activity {iri}, no attachment caching")
    except ActivityUnavailableError as err:
        log.warning(f"remote activity {iri} unavailable ({err}), skipping cache_attachments without retry")
    except Exception:
        log.exception(f"failed to cache attachments for {iri}")
        raise


def finish_post_to_inbox(job) -> None:
    iri = job["iri"]
    try:
        activity = ap.fetch_remote_activity_sync(iri)
        log.info(f"activity={activity!r}")

        if activity.has_type(ap.ActivityType.DELETE):
            back.inbox_delete(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.UPDATE):
            back.inbox_update(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.CREATE):
            back.inbox_create(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.ANNOUNCE):
            back.inbox_announce(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.LIKE):
            back.inbox_like(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.FOLLOW):
            # Reply to a Follow with an Accept
            accept = ap.Accept(actor=ID, object=activity.to_dict(embed=True))
            tasks.post_to_outbox(accept)
        elif activity.has_type(ap.ActivityType.UNDO):
            obj = activity.get_object_sync()
            if obj.has_type(ap.ActivityType.LIKE):
                back.inbox_undo_like(MY_PERSON, obj)
            elif obj.has_type(ap.ActivityType.ANNOUNCE):
                back.inbox_undo_announce(MY_PERSON, obj)
            elif obj.has_type(ap.ActivityType.FOLLOW):
                back.undo_new_follower(MY_PERSON, obj)
        try:
            cache.invalidate_for_activity(activity)
        except Exception:
            log.exception("failed to invalidate cache")
    except (ActivityGoneError, ActivityNotFoundError, NotAnActivityError):
        log.exception("no retry")
    except ActivityUnavailableError as err:
        log.warning(f"remote activity {iri} unavailable ({err}), skipping finish_post_to_inbox without retry")
    except Exception:
        log.exception(f"failed to finish post to inbox for {iri}")
        raise


def finish_post_to_outbox(job) -> None:
    iri = job["iri"]
    try:
        activity = ap.fetch_remote_activity_sync(iri)
        log.info(f"activity={activity!r}")

        recipients = activity.recipients()

        if activity.has_type(ap.ActivityType.DELETE):
            back.outbox_delete(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.UPDATE):
            back.outbox_update(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.CREATE):
            back.outbox_create(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.ANNOUNCE):
            back.outbox_announce(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.LIKE):
            back.outbox_like(MY_PERSON, activity)
        elif activity.has_type(ap.ActivityType.UNDO):
            obj = activity.get_object_sync()
            if obj.has_type(ap.ActivityType.LIKE):
                back.outbox_undo_like(MY_PERSON, obj)
            elif obj.has_type(ap.ActivityType.ANNOUNCE):
                back.outbox_undo_announce(MY_PERSON, obj)
            elif obj.has_type(ap.ActivityType.FOLLOW):
                back.undo_new_following(MY_PERSON, obj)

        log.info(f"recipients={recipients}")
        activity = ap.clean_activity(activity.to_dict())

        cache.clear()

        payload = activitypub.json_dumps(activity)
        for recp in recipients:
            log.debug(f"posting to {recp}")
            enqueue_job("post_to_remote_inbox", payload=payload, to=recp)
    except (ActivityGoneError, ActivityNotFoundError):
        log.exception("no retry")
    except ActivityUnavailableError as err:
        log.warning(f"remote activity {iri} unavailable ({err}), skipping finish_post_to_outbox without retry")
    except Exception:
        log.exception(f"failed to post to remote inbox for {iri}")
        raise


def forward_activity(job) -> None:
    iri = job["iri"]
    try:
        activity = ap.fetch_remote_activity_sync(iri)
        recipients = back.followers_as_recipients()
        log.debug(f"Forwarding {activity!r} to {recipients}")
        activity = ap.clean_activity(activity.to_dict())
        payload = activitypub.json_dumps(activity)
        for recp in recipients:
            log.debug(f"forwarding {activity!r} to {recp}")
            enqueue_job("post_to_remote_inbox", payload=payload, to=recp)
    except Exception:
        log.exception(f"failed to forward activity {iri}")
        raise


def post_to_remote_inbox(job) -> None:
    payload = job["payload"]
    to = job["to"]
    try:
        log.info("payload=%s", payload)
        log.info("generating sig")
        signed_payload = json.loads(payload)

        # Don't overwrite the signature if we're forwarding an activity
        if "signature" not in signed_payload:
            try:
                generate_signature(signed_payload, key())
            except Exception:
                # Linked-data signing resolves the w3id identity context,
                # which no longer dereferences; HTTP Signatures below still
                # authenticate the delivery, so proceed without it.
                log.exception("LD signature failed, delivering with HTTP signature only")

        body = activitypub.json_dumps(signed_payload)
        headers = sign_delivery_request(to, body, key(), user_agent())

        log.info("to=%s", to)
        resp = requests.post(to, data=body, headers=headers, timeout=15)
        log.info("resp=%s", resp)
        log.info("resp_body=%s", resp.text)
        resp.raise_for_status()
    except HTTPError as err:
        log.exception("request failed")
        if err.response is not None and 400 <= err.response.status_code < 500:
            log.info("client error, no retry")
            return
        raise


def cache_media_item(job: dict) -> None:
    url = job.get("iri") or (job.get("payload") or {}).get("url")
    kind_str = (job.get("payload") or {}).get("kind")
    if not url:
        log.warning(f"invalid cache_media_item job without url: {job}")
        return

    try:
        kind = Kind(kind_str) if kind_str else Kind.ATTACHMENT
    except ValueError:
        kind = Kind.ATTACHMENT

    try:
        log.info(f"on-demand caching media {kind.value}: {url}")
        MEDIA_CACHE.cache(url, kind)
    except requests.exceptions.HTTPError as http_err:
        if http_err.response is not None and 400 <= http_err.response.status_code < 500:
            log.warning(f"client error {http_err.response.status_code} fetching media {url}, no retry")
            return
        log.exception(f"failed to cache media {url}")
        raise
    except Exception:
        log.exception(f"failed to cache media {url}")
        raise


def cache_all_custom_emojis() -> int:
    """Scans activities and cached actors to download and store all remote custom emojis in GridFS."""
    count = 0
    seen_urls: set[str] = set()

    for doc in DB.activities.find():
        for target in [
            doc.get("activity", {}).get("object"),
            doc.get("meta", {}).get("object"),
            doc.get("meta", {}).get("actor"),
            doc.get("meta", {}).get("object_actor"),
        ]:
            if isinstance(target, dict):
                for url in extract_custom_emojis(target.get("tag", [])).values():
                    seen_urls.add(url)
                if isinstance(target.get("emojis"), dict):
                    for url in target["emojis"].values():
                        seen_urls.add(url)

    for actor in DB.actors.find():
        data = actor.get("data", {})
        if isinstance(data, dict):
            for url in extract_custom_emojis(data.get("tag", [])).values():
                seen_urls.add(url)

    for url in seen_urls:
        if not MEDIA_CACHE.get_file(url, None, Kind.CUSTOM_EMOJI):
            try:
                MEDIA_CACHE.cache(url, Kind.CUSTOM_EMOJI)
                count += 1
            except Exception as exc:
                log.warning(f"failed to cache custom emoji {url}: {exc}")

    return count


JOB_HANDLERS = {
    "process_new_activity": process_new_activity,
    "fetch_og_metadata": fetch_og_metadata,
    "cache_object": cache_object,
    "cache_actor": cache_actor,
    "cache_attachments": cache_attachments,
    "finish_post_to_inbox": finish_post_to_inbox,
    "finish_post_to_outbox": finish_post_to_outbox,
    "forward_activity": forward_activity,
    "post_to_remote_inbox": post_to_remote_inbox,
    "cache_media_item": cache_media_item,
}
