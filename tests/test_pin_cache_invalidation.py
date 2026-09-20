from unittest.mock import MagicMock, patch

from micronote.app import app


def test_api_pin_invalidates_cache2():
    mock_note = MagicMock()
    mock_note.id = "https://example.com/note/1"

    mock_db = MagicMock()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with (
            patch("micronote.api._user_api_get_note", return_value=mock_note),
            patch("micronote.api.csrf.protect"),
            patch("micronote.api.DB", mock_db),
        ):
            resp = client.post(
                "/api/note/pin",
                data={"id": mock_note.id},
            )
            assert resp.status_code == 201
            mock_db.activities.update_one.assert_called_once()
            mock_db.cache2.delete_many.assert_called_once_with({})


def test_api_unpin_invalidates_cache2():
    mock_note = MagicMock()
    mock_note.id = "https://example.com/note/1"

    mock_db = MagicMock()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with (
            patch("micronote.api._user_api_get_note", return_value=mock_note),
            patch("micronote.api.csrf.protect"),
            patch("micronote.api.DB", mock_db),
        ):
            resp = client.post(
                "/api/note/unpin",
                data={"id": mock_note.id},
            )
            assert resp.status_code == 201
            mock_db.activities.update_one.assert_called_once()
            mock_db.cache2.delete_many.assert_called_once_with({})
