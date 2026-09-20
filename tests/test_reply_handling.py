from unittest.mock import MagicMock, patch

from active_boxes.errors import ActivityUnavailableError

from micronote.activitypub import MicroblogPubBackend
from micronote.config import me


def test_handle_replies_catches_activity_unavailable_error():
    mock_db = MagicMock()
    mock_create = MagicMock()
    mock_create.id = "https://example.com/activities/create-1"
    mock_note = MagicMock()
    mock_note.inReplyTo = "https://infosec.exchange/users/lcamtuf/statuses/117277593517319641"
    mock_create.get_object_sync.return_value = mock_note

    # Simulate local note not found in DB
    mock_db.activities.find_one_and_update.return_value = None

    backend = MicroblogPubBackend()
    backend.DB = mock_db
    mock_person = MagicMock()
    mock_person.id = me()["id"]

    # When remote server returns 401 Unauthorized, active_boxes raises ActivityUnavailableError
    err = ActivityUnavailableError("unable to fetch, unknown error: 401 Unauthorized")

    with patch("micronote.activitypub.ap.fetch_remote_activity_sync", side_effect=err):
        # Must not raise ActivityUnavailableError or crash
        backend._handle_replies(mock_person, mock_create)

    # Verify thread_root_parent was still recorded
    mock_db.activities.update_one.assert_called_once_with(
        {"remote_id": mock_create.id},
        {"$set": {"meta.thread_root_parent": mock_note.inReplyTo}},
    )


def test_handle_replies_stops_traversal_on_ancestor_error():
    mock_db = MagicMock()
    mock_create = MagicMock()
    mock_create.id = "https://example.com/activities/create-2"
    mock_note = MagicMock()
    mock_note.inReplyTo = "https://remote.server/notes/1"
    mock_create.get_object_sync.return_value = mock_note

    mock_db.activities.find_one_and_update.return_value = None
    mock_db.activities.count_documents.return_value = 0

    parent_reply = MagicMock()
    parent_reply.id = "https://remote.server/activities/reply-1"
    parent_reply.inReplyTo = "https://remote.server/notes/private-root"

    # First fetch succeeds, second fetch raises ActivityUnavailableError
    side_effects = [
        parent_reply,
        ActivityUnavailableError("remote host 401"),
    ]

    backend = MicroblogPubBackend()
    backend.DB = mock_db
    mock_person = MagicMock()
    mock_person.id = me()["id"]

    with (
        patch.object(backend, "save") as mock_save,
        patch("micronote.activitypub.ap.fetch_remote_activity_sync", side_effect=side_effects),
    ):
        backend._handle_replies(mock_person, mock_create)

        # Parent was saved to REPLIES
        mock_save.assert_called_once()

    # thread_root_parent set to the unreachable root ancestor
    mock_db.activities.update_one.assert_called_once_with(
        {"remote_id": mock_create.id},
        {"$set": {"meta.thread_root_parent": "https://remote.server/notes/private-root"}},
    )
