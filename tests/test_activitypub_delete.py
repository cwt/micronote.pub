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
