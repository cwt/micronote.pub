"""Background worker: drains the jobs queue via NeoSQLite watch().

Single process (run one, not per web worker). Enqueued by tasks.py,
claimed with find_one_and_update, retried with exponential backoff.
"""

import json
import logging
import os
import random
import time
from datetime import UTC, datetime, timedelta

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

from micronote import activitypub, tasks
from micronote.config import BASE_URL, DB, ID, KEY, MEDIA_CACHE, USER_AGENT, create_db_connection
from micronote.tasks import (
    MAX_RETRIES,
    MY_PERSON,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    back,
    enqueue_job,
    log,
)
from micronote.utils import opengraph, strtobool
from micronote.utils.delivery import sign_delivery_request
from micronote.utils.media import Kind

RESUME_TOKEN_ID = "jobs_watch"
SWEEP_INTERVAL_SECONDS = 60
REMOVE_FAILED_JOBS = strtobool(os.getenv("MICRONOTE_REMOVE_FAILED_JOBS", "false"))


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
                except (Error, Exception) as err:
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
            og_metadata = opengraph.fetch_og_metadata(USER_AGENT, links)
            for og in og_metadata:
                if not og.get("image"):
                    continue
                MEDIA_CACHE.cache_og_image(og["image"])

            log.debug(f"OG metadata {og_metadata!r}")
            DB.activities.update_one({"remote_id": iri}, {"$set": {"meta.og_metadata": og_metadata}})

        log.info(f"OG metadata fetched for {iri}")
    except (ActivityGoneError, ActivityNotFoundError):
        log.exception(f"dropping activity {iri}, skip OG metedata")
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

        actor_meta = activitypub._safe_object_actor_meta(obj)

        update_payload = {
            "meta.object": obj.to_dict(embed=True),
        }
        if actor_meta:
            update_payload["meta.object_actor"] = actor_meta

        DB.activities.update_one(
            {"remote_id": activity.id},
            {"$set": update_payload},
        )
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
        except (Error, Exception) as err:
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
                            {"$set": {"meta.object": activitypub._actor_to_meta(follow_target)}},
                        )
                except (Error, Exception) as err:
                    log.warning(f"unable to cache follow target for {iri}: {err}")

        # Cache the actor info (or fallback to basic dict if remote profile returned 401/error)
        actor_meta = None
        if actor:
            actor_meta = activitypub._actor_to_meta(actor, cache_actor_with_inbox)
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
        # Generates thumbnails for the actor's icon and the attachments if any

        actor = activity.get_actor_sync()

        # Update the cached actor
        DB.actors.update_one(
            {"remote_id": iri},
            {"$set": {"remote_id": iri, "data": actor.to_dict(embed=True)}},
            upsert=True,
        )

        icon = actor.icon
        icon_url = icon.get("url") if isinstance(icon, dict) else None
        if icon_url:
            try:
                MEDIA_CACHE.cache(icon_url, Kind.ACTOR_ICON)
            except Exception:
                log.exception(f"failed to cache actor icon {icon_url}")

        if activity.has_type(ap.ActivityType.CREATE):
            for attachment in activity.get_object_sync()._data.get("attachment", []):
                if not isinstance(attachment, dict):
                    continue
                url = attachment.get("url")
                if not url:
                    log.warning(f"skipping attachment without url in {iri}")
                    continue
                media_type = attachment.get("mediaType") or ""
                if media_type.startswith("image/") or attachment.get("type") == ap.ActivityType.IMAGE.value:
                    try:
                        MEDIA_CACHE.cache(url, Kind.ATTACHMENT)
                    except ValueError:
                        log.exception(f"failed to cache {attachment}")

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
            tasks.invalidate_cache(activity)
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

        DB.cache2.delete_many({})

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
                generate_signature(signed_payload, KEY)
            except Exception:
                # Linked-data signing resolves the w3id identity context,
                # which no longer dereferences; HTTP Signatures below still
                # authenticate the delivery, so proceed without it.
                log.exception("LD signature failed, delivering with HTTP signature only")

        body = activitypub.json_dumps(signed_payload)
        headers = sign_delivery_request(to, body, KEY, USER_AGENT)

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
}


def retry_delay(attempts) -> int:
    return int(random.uniform(2, 4) ** attempts)


def run_job(doc: dict) -> None:
    try:
        JOB_HANDLERS[doc["type"]](doc)
    except Exception as err:
        attempts = doc.get("attempts", 0) + 1
        if attempts > MAX_RETRIES:
            if REMOVE_FAILED_JOBS:
                log.exception(f"job {doc['_id']} failed permanently after {attempts} attempts, removing job")
                DB.jobs.delete_one({"_id": doc["_id"]})
            else:
                log.exception(f"job {doc['_id']} failed permanently after {attempts} attempts")
                DB.jobs.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"status": STATUS_FAILED, "attempts": attempts, "error": repr(err)}},
                )
        else:
            next_run = datetime.now(UTC) + timedelta(seconds=retry_delay(attempts))
            DB.jobs.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": STATUS_PENDING,
                        "attempts": attempts,
                        "next_run": next_run,
                        "error": repr(err),
                    }
                },
            )
    else:
        DB.jobs.delete_one({"_id": doc["_id"]})


def drain_jobs(limit: int = 100, max_passes: int = 100) -> int:
    """Claims and runs due jobs. Repeats until a pass claims nothing, so
    jobs chained mid-drain are picked up by the next pass."""
    processed = 0
    for _ in range(max_passes):
        claimed_any = False
        now = datetime.now(UTC)
        due = DB.jobs.find({"status": STATUS_PENDING, "next_run": {"$lte": now}}, limit=limit).sort("next_run", 1)
        for doc in due:
            claimed = DB.jobs.find_one_and_update(
                {"_id": doc["_id"], "status": STATUS_PENDING},
                {"$set": {"status": STATUS_PROCESSING}},
            )
            if not claimed:
                continue
            claimed_any = True
            run_job(claimed)
            processed += 1
        if not claimed_any:
            break
    return processed


def load_resume_token() -> str | None:
    state = DB.worker_state.find_one({"_id": RESUME_TOKEN_ID})
    return state["token"] if state else None


def save_resume_token(token: str) -> None:
    DB.worker_state.update_one({"_id": RESUME_TOKEN_ID}, {"$set": {"token": token}}, upsert=True)


def ensure_jobs_table() -> None:
    # watch() needs the table (and its triggers) to exist; inserts create
    # it on demand, so a sentinel round-trip suffices on fresh databases.
    DB.jobs.insert_one({"type": "_init", "status": STATUS_PROCESSING})
    DB.jobs.delete_many({"type": "_init"})


def run() -> None:
    log.info("worker starting, draining backlog")
    ensure_jobs_table()
    last_sweep = 0.0

    def swept_drain(limit: int) -> int:
        nonlocal last_sweep
        processed = drain_jobs(limit=limit)
        now = time.monotonic()
        if now - last_sweep >= SWEEP_INTERVAL_SECONDS:
            create_db_connection().sweep_ttl_once()
            last_sweep = now
        return processed

    swept_drain(limit=1000)
    resume = load_resume_token()
    while True:
        try:
            stream_kwargs = {
                "pipeline": [{"$match": {"fullDocument.status": STATUS_PENDING}}],
                "full_document": "updateLookup",
            }
            if resume is not None:
                stream_kwargs["resume_after"] = resume
            with DB.jobs.watch(**stream_kwargs) as stream:
                # NOTE: the stream blocks on idle (no None ticks in
                # NeoSQLite 1.16.1), so correctness never depends on
                # wakeups: every event triggers a drain-until-clean pass,
                # which also catches jobs chained by earlier passes.
                for change in stream:
                    if change is None:
                        continue
                    resume = change["_id"]
                    save_resume_token(resume)
                    full_document = change.get("fullDocument") or {}
                    if full_document.get("status") != STATUS_PENDING:
                        continue
                    swept_drain(limit=100)
        except ValueError:
            log.exception("bad resume token, restarting from now")
            resume = None
        except Exception:
            log.exception("watch failed, retrying in 5s")
            time.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        run()
    except KeyboardInterrupt:
        log.info("worker stopped")


if __name__ == "__main__":
    main()
