"""Unit tests for ActivityPub delete cascading and metadata integrity."""

from unittest.mock import MagicMock

from active_boxes import activitypub as ap

from micronote.activitypub import MicroblogPubBackend


def test_outbox_delete_sets_meta_extra():
    from micronote.config import me

    backend = MicroblogPubBackend()
    backend.DB = MagicMock()
    orig = ap.get_backend()
    ap.use_backend(backend)
    try:
        person = ap.Person(**me())
        note = ap.Note(id=f"{me()['id']}/note/1", content="hello", attributedTo=me()["id"])
        delete = ap.Delete(actor=me()["id"], object=note.to_dict())

        backend._handle_replies_delete = MagicMock()
        backend.outbox_delete(person, delete)

        # Verify that update_many was called with meta.extra (not meta.exta)
        calls = backend.DB.activities.update_many.call_args_list
        assert len(calls) == 1
        _filter, update = calls[0].args
        assert _filter == {"meta.object.id": note.id}
        assert update["$set"]["meta.extra"] == "object deleted"
        assert "meta.exta" not in update["$set"]
    finally:
        ap.use_backend(orig)


def test_delete_handlers_invoke_get_object_sync_once():
    from micronote.config import me

    backend = MicroblogPubBackend()
    backend.DB = MagicMock()
    backend._handle_replies_delete = MagicMock()
    orig = ap.get_backend()
    ap.use_backend(backend)
    try:
        person = ap.Person(**me())
        note = ap.Note(id=f"{me()['id']}/note/1", content="hello", attributedTo=me()["id"])

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
    finally:
        ap.use_backend(orig)
