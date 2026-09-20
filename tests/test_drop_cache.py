from unittest.mock import MagicMock, patch

from micronote import stats
from micronote.app import app


def test_drop_cache_rejects_get():
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.get("/drop_cache")
        assert resp.status_code == 405


def test_drop_cache_rejected_when_debug_mode_off():
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        with patch("micronote.app.DEBUG_MODE", False):
            resp = client.post("/drop_cache")
            assert resp.status_code == 403
            assert resp.get_json() == {"message": "DEBUG_MODE is off"}


def test_drop_cache_succeeds_when_debug_mode_on():
    mock_db = MagicMock()
    mock_cache_db = MagicMock()

    # Seed the counts memo
    mock_db.activities.count_documents.return_value = 1
    with patch("micronote.stats.DB", mock_db):
        stats.counts()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        with (
            patch("micronote.app.DEBUG_MODE", True),
            patch("micronote.app.csrf.protect"),
            patch("micronote.app.DB", mock_db),
            patch("micronote.cache.DB", mock_cache_db),
        ):
            resp = client.post("/drop_cache")
            assert resp.status_code == 200
            assert resp.text == "Done"
            mock_db.actors.drop.assert_called_once()
            mock_cache_db.cache2.delete_many.assert_called_once_with({})

    # cache.clear() dropped the counts memo: the next call re-queries the DB
    mock_db.activities.count_documents.reset_mock()
    with patch("micronote.stats.DB", mock_db):
        stats.counts()
    assert mock_db.activities.count_documents.call_count == 5
