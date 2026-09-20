from unittest.mock import MagicMock, patch

from micronote import tasks
from micronote.app import app


def test_api_pin_invalidates_cache2():
    mock_note = MagicMock()
    mock_note.id = "https://example.com/note/1"

    mock_db = MagicMock()
    mock_cache_db = MagicMock()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with (
            patch("micronote.api._user_api_get_note", return_value=mock_note),
            patch("micronote.api.csrf.protect"),
            patch("micronote.api.DB", mock_db),
            patch("micronote.cache.DB", mock_cache_db),
        ):
            resp = client.post(
                "/api/note/pin",
                data={"id": mock_note.id},
            )
            assert resp.status_code == 201
            mock_db.activities.update_one.assert_called_once()
            mock_cache_db.cache2.delete_many.assert_called_once_with({})


def test_api_unpin_invalidates_cache2():
    mock_note = MagicMock()
    mock_note.id = "https://example.com/note/1"

    mock_db = MagicMock()
    mock_cache_db = MagicMock()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with (
            patch("micronote.api._user_api_get_note", return_value=mock_note),
            patch("micronote.api.csrf.protect"),
            patch("micronote.api.DB", mock_db),
            patch("micronote.cache.DB", mock_cache_db),
        ):
            resp = client.post(
                "/api/note/unpin",
                data={"id": mock_note.id},
            )
            assert resp.status_code == 201
            mock_db.activities.update_one.assert_called_once()
            mock_cache_db.cache2.delete_many.assert_called_once_with({})


def test_anonymous_homepage_is_cached():
    with app.test_client() as client:
        with (
            patch("micronote.app.paginated_query", return_value=([], None, None)),
            patch("micronote.cache.set_page") as mock_set_page,
        ):
            assert client.get("/", headers={"Accept": "text/html"}).status_code == 200
            mock_set_page.assert_called_once()

            with patch("micronote.cache.get_page", return_value="cached page"):
                resp = client.get("/", headers={"Accept": "text/html"})
                assert resp.data == b"cached page"


def test_post_to_outbox_clears_cache():
    activity = MagicMock()
    activity.has_type.return_value = False
    mock_back = MagicMock()
    mock_back.random_object_id.return_value = "abc123"
    mock_back.activity_url.return_value = "https://example.com/outbox/abc123"
    mock_cache_db = MagicMock()

    with (
        patch("micronote.tasks.back", mock_back),
        patch("micronote.cache.DB", mock_cache_db),
        patch("micronote.tasks.enqueue_job"),
    ):
        tasks.post_to_outbox(activity)

    mock_cache_db.cache2.delete_many.assert_called_once_with({})
