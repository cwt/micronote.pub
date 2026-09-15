import logging
import os
from datetime import UTC, datetime

from active_boxes import activitypub as ap

from micronote import activitypub
from micronote.activitypub import Box
from micronote.config import BASE_URL, DB, ID, ME
from micronote.utils import strtobool

log = logging.getLogger(__name__)

back = activitypub.MicroblogPubBackend()
ap.use_backend(back)


MY_PERSON = ap.Person(**ME)

MAX_RETRIES = 9

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_FAILED = "failed"

# Local testing without a worker process: drain the job queue inline
# after every enqueue. Production runs worker.py instead.
TASK_EAGER = strtobool(os.getenv("MICRONOTE_TASK_EAGER", "false"))


def enqueue_job(job_type, iri=None, payload=None, to=None, also_cache_attachments=True):
    """Stores a background job; worker.py picks it up via watch()."""
    job = {
        "type": job_type,
        "iri": iri,
        "payload": payload,
        "to": to,
        "also_cache_attachments": also_cache_attachments,
        "status": STATUS_PENDING,
        "attempts": 0,
        "next_run": datetime.now(UTC),
        "error": None,
    }
    DB.jobs.insert_one(job)
    log.info(f"enqueued {job_type} iri={iri}")
    if TASK_EAGER:
        from micronote.worker import drain_jobs

        drain_jobs()
    return job


def post_to_inbox(activity: ap.BaseActivity) -> None:
    # Check for Block activity
    actor = activity.get_actor_sync()
    if back.outbox_is_blocked(MY_PERSON, actor.id):
        log.info(f"actor {actor!r} is blocked, dropping the received activity {activity!r}")
        return

    if back.inbox_check_duplicate(MY_PERSON, activity.id):
        # The activity is already in the inbox
        log.info(f"received duplicate activity {activity!r}, dropping it")
        return

    if activity.has_type(ap.ActivityType.FOLLOW) and back.inbox_has_active_follower(MY_PERSON, actor.id):
        log.info(f"actor {actor.id} already follows, dropping duplicate Follow {activity!r}")
        return

    back.save(Box.INBOX, activity)
    enqueue_job("process_new_activity", iri=activity.id)

    log.info(f"spawning task for {activity!r}")
    enqueue_job("finish_post_to_inbox", iri=activity.id)


def invalidate_cache(activity) -> None:
    if activity.has_type([ap.ActivityType.UNDO, ap.ActivityType.DELETE, ap.ActivityType.UPDATE]):
        DB.cache2.delete_many({})
    elif activity.has_type([ap.ActivityType.LIKE, ap.ActivityType.ANNOUNCE]):
        if activity.get_object_sync().id.startswith(BASE_URL):
            DB.cache2.delete_many({})
    elif activity.has_type(ap.ActivityType.CREATE):
        note = activity.get_object_sync()
        if not note.inReplyTo or note.inReplyTo.startswith(ID):
            DB.cache2.delete_many({})


def post_to_outbox(activity: ap.BaseActivity) -> str:
    if activity.has_type(ap.CREATE_TYPES):
        activity = activity.build_create()

    # Assign create a random ID
    obj_id = back.random_object_id()
    activity.set_id(back.activity_url(obj_id), obj_id)

    back.save(Box.OUTBOX, activity)
    enqueue_job("cache_actor", iri=activity.id)
    enqueue_job("finish_post_to_outbox", iri=activity.id)
    return activity.id
