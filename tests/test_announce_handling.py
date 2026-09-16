from unittest.mock import MagicMock, patch

from active_boxes.activitypub import ActivityType
from active_boxes.errors import ActivityUnavailableError

from micronote import activitypub
from micronote.tasks import MY_PERSON
from micronote.worker import finish_post_to_inbox, run_job


def test_inbox_announce_handles_401_actor():
    mock_announce = MagicMock()
    mock_announce.id = "https://mas.to/users/veer66/statuses/117279205452392879/activity"

    mock_note = MagicMock()
    mock_note.id = "https://mastodon.social/users/titipat/statuses/117279089759684770"
    mock_note.attributedTo = "https://mastodon.social/users/titipat"
    mock_note.to_dict.return_value = {
        "id": mock_note.id,
        "type": "Note",
        "attributedTo": mock_note.attributedTo,
        "content": "A status update",
    }
    # Simulate remote Mastodon server returning 401 Unauthorized for the actor profile
    mock_note.get_actor_sync.side_effect = ActivityUnavailableError(
        "unable to fetch https://mastodon.social/users/titipat, unknown error: 401, message='Unauthorized'"
    )
    mock_announce.get_object_sync.return_value = mock_note

    backend = activitypub.MicroblogPubBackend()
    backend.DB = MagicMock()

    # Must execute cleanly without raising ActivityUnavailableError
    backend.inbox_announce(MY_PERSON, mock_announce)

    # Verify activities collection was updated with fallback actor metadata
    backend.DB.activities.update_one.assert_any_call(
        {"remote_id": mock_announce.id},
        {
            "$set": {
                "meta.object": mock_note.to_dict(embed=True),
                "meta.object_actor": {
                    "id": "https://mastodon.social/users/titipat",
                    "url": "https://mastodon.social/users/titipat",
                    "icon": None,
                    "name": "https://mastodon.social/users/titipat",
                    "preferredUsername": None,
                    "emojis": {},
                },
            }
        },
    )
    backend.DB.activities.update_one.assert_any_call(
        {"activity.object.id": mock_note.id},
        {"$inc": {"meta.count_boost": 1}},
    )


def test_inbox_announce_handles_unavailable_object():
    mock_announce = MagicMock()
    mock_announce.id = "https://example.com/announce/1"
    mock_announce._data = {"object": "https://example.com/notes/404"}
    mock_announce.get_object_sync.side_effect = ActivityUnavailableError("object unavailable")

    backend = activitypub.MicroblogPubBackend()
    backend.DB = MagicMock()

    # Must return without raising
    backend.inbox_announce(MY_PERSON, mock_announce)
    backend.DB.activities.update_one.assert_not_called()


def test_finish_post_to_inbox_announce_with_401_actor():
    mock_announce = MagicMock()
    mock_announce.id = "https://mas.to/users/veer66/statuses/117279205452392879/activity"

    def mock_has_type(activity_type):
        return activity_type == ActivityType.ANNOUNCE

    mock_announce.has_type.side_effect = mock_has_type

    mock_note = MagicMock()
    mock_note.id = "https://mastodon.social/users/titipat/statuses/117279089759684770"
    mock_note.attributedTo = "https://mastodon.social/users/titipat"
    mock_note.to_dict.return_value = {"id": mock_note.id}
    mock_note.get_actor_sync.side_effect = ActivityUnavailableError("401 Unauthorized")
    mock_announce.get_object_sync.return_value = mock_note

    mock_backend = MagicMock()

    with (
        patch("micronote.worker.ap.fetch_remote_activity_sync", return_value=mock_announce),
        patch("micronote.worker.back", mock_backend),
        patch("micronote.worker.tasks.invalidate_cache"),
    ):
        # Must execute cleanly without exception
        finish_post_to_inbox({"iri": mock_announce.id})

    mock_backend.inbox_announce.assert_called_once_with(MY_PERSON, mock_announce)


def test_finish_post_to_inbox_handles_remote_unavailable():
    mock_db = MagicMock()
    with (
        patch(
            "micronote.worker.ap.fetch_remote_activity_sync",
            side_effect=ActivityUnavailableError("remote 401"),
        ),
        patch("micronote.worker.DB", mock_db),
    ):
        # Must return cleanly without raising
        finish_post_to_inbox({"iri": "https://example.com/activity/unavailable"})


def test_run_job_fails_permanently_after_max_retries():
    job_doc = {
        "_id": "job123",
        "type": "finish_post_to_inbox",
        "attempts": 3,
    }

    mock_db = MagicMock()

    def failing_handler(job):
        raise RuntimeError("boom")

    with (
        patch.dict("micronote.worker.JOB_HANDLERS", {"finish_post_to_inbox": failing_handler}),
        patch("micronote.worker.DB", mock_db),
        patch("micronote.worker.MAX_RETRIES", 3),
        patch("micronote.worker.REMOVE_FAILED_JOBS", False),
    ):
        run_job(job_doc)

    mock_db.jobs.update_one.assert_called_once()
    call_args = mock_db.jobs.update_one.call_args
    assert call_args[0][0] == {"_id": "job123"}
    set_fields = call_args[0][1]["$set"]
    assert set_fields["status"] == "failed"
    assert set_fields["attempts"] == 4


def test_run_job_removes_failed_job_when_configured():
    job_doc = {
        "_id": "job456",
        "type": "finish_post_to_inbox",
        "attempts": 3,
    }

    mock_db = MagicMock()

    def failing_handler(job):
        raise RuntimeError("boom")

    with (
        patch.dict("micronote.worker.JOB_HANDLERS", {"finish_post_to_inbox": failing_handler}),
        patch("micronote.worker.DB", mock_db),
        patch("micronote.worker.MAX_RETRIES", 3),
        patch("micronote.worker.REMOVE_FAILED_JOBS", True),
    ):
        run_job(job_doc)

    mock_db.jobs.delete_one.assert_called_once_with({"_id": "job456"})
