import logging

from active_boxes import activitypub as ap

from micronote import cache
from micronote.boxes import Box
from micronote.instance import MY_PERSON, back
from micronote.jobs import enqueue_job

log = logging.getLogger(__name__)


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


def post_to_outbox(activity: ap.BaseActivity) -> str:
    if activity.has_type(ap.CREATE_TYPES):
        activity = activity.build_create()

    # Assign create a random ID
    obj_id = back.random_object_id()
    activity.set_id(back.activity_url(obj_id), obj_id)

    back.save(Box.OUTBOX, activity)
    cache.clear()
    enqueue_job("cache_actor", iri=activity.id)
    enqueue_job("finish_post_to_outbox", iri=activity.id)
    return activity.id
