from unittest.mock import MagicMock, patch

from micronote.app import _COUNTS_CACHE, app


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
    _COUNTS_CACHE["counts"] = {"test": 1}

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        with (
            patch("micronote.app.DEBUG_MODE", True),
            patch("micronote.app.csrf.protect"),
            patch("micronote.app.DB", mock_db),
        ):
            resp = client.post("/drop_cache")
            assert resp.status_code == 200
            assert resp.text == "Done"
            mock_db.actors.drop.assert_called_once()
            mock_db.cache2.delete_many.assert_called_once_with({})
            assert "counts" not in _COUNTS_CACHE
