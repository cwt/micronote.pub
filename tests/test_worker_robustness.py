import asyncio
import threading

from active_boxes import activitypub as ap
from active_boxes.errors import ActivityUnavailableError

from micronote.config import DB, create_db_connection
from micronote.instance import back


def test_no_ttl_sweeper_thread_leak():
    """create_db_connection should not spawn background daemon sweeper threads."""
    conn = create_db_connection()
    assert conn is not None
    threads_after = [t.name for t in threading.enumerate()]
    sweeper_threads = [name for name in threads_after if "ttl-sweeper" in name]
    assert len(sweeper_threads) == 0, f"Leaked sweeper threads: {sweeper_threads}"


def test_sync_cleanup_hook():
    """Nested _run_sync calls must clean up thread-local DB connections upon thread exit."""
    from active_boxes._sync import _run_sync

    async def nested_db_access():
        # Access DB in the nested thread
        conn = create_db_connection()
        assert conn is not None
        return True

    async def outer():
        # Running _run_sync inside an existing loop forces the ThreadPoolExecutor branch
        return _run_sync(nested_db_access())

    threads_before = threading.active_count()
    result = asyncio.run(outer())
    assert result is True
    # The ephemeral thread in the ThreadPoolExecutor should have completed and cleaned up
    threads_after = threading.active_count()
    assert threads_after == threads_before


def test_fetch_iri_uses_cached_actor_in_db():
    """_fetch_iri should return an actor from DB.actors if present, avoiding network calls."""
    actor_id = "https://example.com/users/test_cached_actor"
    actor_data = {
        "id": actor_id,
        "type": "Person",
        "preferredUsername": "testuser",
        "name": "Test User",
    }
    DB.actors.update_one(
        {"remote_id": actor_id},
        {"$set": {"remote_id": actor_id, "data": actor_data}},
        upsert=True,
    )
    try:
        data = back._fetch_iri(actor_id)
        assert data is not None
        assert data["id"] == actor_id
        assert data["type"] == "Person"
    finally:
        DB.actors.delete_one({"remote_id": actor_id})


def test_validate_actor_preserves_unavailable_error(monkeypatch):
    """BaseActivity._validate_actor should preserve ActivityUnavailableError instead of masking it."""
    import pytest

    def mock_fetch_iri_sync(iri, **kwargs):
        raise ActivityUnavailableError(f"unable to fetch {iri}, timeout")

    monkeypatch.setattr(back, "fetch_iri_sync", mock_fetch_iri_sync)

    with pytest.raises(ActivityUnavailableError) as exc_info:
        ap.Create(
            id="https://example.com/activity/1",
            actor="https://example.com/users/unavailable_actor",
            object={"type": "Note", "id": "https://example.com/note/1", "content": "hello"},
        )
    assert "timeout" in str(exc_info.value)
