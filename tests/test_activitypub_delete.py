"""Unit tests for ActivityPub delete cascading and metadata integrity."""

from unittest.mock import MagicMock

from active_boxes import activitypub as ap

import micronote.instance  # noqa: F401 - ensures ActivityPub backend is initialized
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


def test_inbox_actor_delete_when_remote_gone(monkeypatch):
    from active_boxes.errors import ActivityGoneError

    from micronote.app import app
    from micronote.config import DB

    def mock_verify_false(*args, **kwargs):
        return False

    def mock_check_duplicate_false(*args, **kwargs):
        return False

    def mock_fetch_iri_gone(iri, **kwargs):
        raise ActivityGoneError(f"{iri} is gone")

    inserted = []

    def mock_insert_one(doc):
        inserted.append(doc)

    monkeypatch.setattr("micronote.ap_routes.verify_request_sync", mock_verify_false)
    monkeypatch.setattr(ap.get_backend(), "fetch_iri_sync", mock_fetch_iri_gone)
    monkeypatch.setattr(ap.get_backend(), "inbox_check_duplicate", mock_check_duplicate_false)
    monkeypatch.setattr(DB.activities, "insert_one", mock_insert_one)

    with app.test_client() as client:
        payload = {
            "type": "Delete",
            "id": "https://mastodon.social/ap/users/123#delete",
            "actor": "https://mastodon.social/ap/users/123",
            "object": "https://mastodon.social/ap/users/123",
        }
        res = client.post(
            "/inbox",
            json=payload,
            headers={"Content-Type": "application/activity+json", "Accept": "application/activity+json"},
        )
        assert res.status_code == 201
        assert len(inserted) == 1
        assert inserted[0]["remote_id"] == payload["id"]


def test_inbox_actor_delete_when_remote_unavailable_with_gone(monkeypatch):
    from active_boxes.errors import ActivityUnavailableError

    from micronote.app import app
    from micronote.config import DB

    def mock_verify_false(*args, **kwargs):
        return False

    def mock_check_duplicate_false(*args, **kwargs):
        return False

    def mock_fetch_iri_wrapped_gone(iri, **kwargs):
        raise ActivityUnavailableError(
            f"unable to fetch {iri}, unknown error: ActivityGoneError('{iri} is gone', payload=None, status_code=410)"
        )

    inserted = []

    def mock_insert_one(doc):
        inserted.append(doc)

    monkeypatch.setattr("micronote.ap_routes.verify_request_sync", mock_verify_false)
    monkeypatch.setattr(ap.get_backend(), "fetch_iri_sync", mock_fetch_iri_wrapped_gone)
    monkeypatch.setattr(ap.get_backend(), "inbox_check_duplicate", mock_check_duplicate_false)
    monkeypatch.setattr(DB.activities, "insert_one", mock_insert_one)

    with app.test_client() as client:
        payload = {
            "type": "Delete",
            "id": "https://mastodon.social/ap/users/456#delete",
            "actor": "https://mastodon.social/ap/users/456",
            "object": "https://mastodon.social/ap/users/456",
        }
        res = client.post(
            "/inbox",
            json=payload,
            headers={"Content-Type": "application/activity+json", "Accept": "application/activity+json"},
        )
        assert res.status_code == 201
        assert len(inserted) == 1
        assert inserted[0]["remote_id"] == payload["id"]


def test_patch_http_client_unwraps_gone():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import pytest
    from active_boxes.errors import ActivityGoneError
    from active_boxes.http_client import AsyncHTTPClient

    client = AsyncHTTPClient()
    mock_resp = AsyncMock()
    mock_resp.status = 410
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    mock_session.close = AsyncMock()
    client._get_session = AsyncMock(return_value=mock_session)

    async def run_fetch():
        await client.get_json("https://example.com/actor")

    with pytest.raises(ActivityGoneError):
        asyncio.run(run_fetch())


def test_patch_http_client_unwraps_not_found():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import pytest
    from active_boxes.errors import ActivityNotFoundError
    from active_boxes.http_client import AsyncHTTPClient

    client = AsyncHTTPClient()
    mock_resp = AsyncMock()
    mock_resp.status = 404
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    mock_session.close = AsyncMock()
    client._get_session = AsyncMock(return_value=mock_session)

    async def run_fetch():
        await client.get_json("https://example.com/actor")

    with pytest.raises(ActivityNotFoundError):
        asyncio.run(run_fetch())
