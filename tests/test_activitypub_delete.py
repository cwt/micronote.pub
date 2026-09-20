"""Unit tests for ActivityPub delete cascading and metadata integrity."""

from unittest.mock import MagicMock

from active_boxes import activitypub as ap

from micronote.activitypub import MicroblogPubBackend


def test_outbox_delete_sets_meta_extra():
    from micronote.config import ME

    backend = MicroblogPubBackend()
    backend.DB = MagicMock()
    ap.use_backend(backend)

    person = ap.Person(**ME)
    note = ap.Note(id=f"{ME['id']}/note/1", content="hello", attributedTo=ME["id"])
    delete = ap.Delete(actor=ME["id"], object=note.to_dict())

    backend._handle_replies_delete = MagicMock()
    backend.outbox_delete(person, delete)

    # Verify that update_many was called with meta.extra (not meta.exta)
    calls = backend.DB.activities.update_many.call_args_list
    assert len(calls) == 1
    _filter, update = calls[0].args
    assert _filter == {"meta.object.id": note.id}
    assert update["$set"]["meta.extra"] == "object deleted"
    assert "meta.exta" not in update["$set"]


def test_delete_handlers_invoke_get_object_sync_once():
    from micronote.config import ME

    backend = MicroblogPubBackend()
    backend.DB = MagicMock()
    backend._handle_replies_delete = MagicMock()
    ap.use_backend(backend)

    person = ap.Person(**ME)
    note = ap.Note(id=f"{ME['id']}/note/1", content="hello", attributedTo=ME["id"])

    # Outbox delete
    mock_outbox_delete = MagicMock()
    mock_outbox_delete.get_object_sync.return_value = note
    backend.outbox_delete(person, mock_outbox_delete)
    assert mock_outbox_delete.get_object_sync.call_count == 1

    # Inbox delete
    mock_inbox_delete = MagicMock()
    mock_inbox_delete.get_object_sync.return_value = note
    backend.inbox_delete(person, mock_inbox_delete)
    assert mock_inbox_delete.get_object_sync.call_count == 1


def test_backend_post_to_outbox_delegates_to_tasks():
    from unittest.mock import patch

    backend = MicroblogPubBackend()
    activity = MagicMock()

    with patch("micronote.tasks.post_to_outbox", return_value="https://example.com/act/1") as mock_tasks_post:
        ret = backend.post_to_outbox(activity)

    mock_tasks_post.assert_called_once_with(activity)
    assert ret == "https://example.com/act/1"
