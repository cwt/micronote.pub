from unittest.mock import MagicMock, patch

from neosqlite.objectid import ObjectId

from micronote.app import app


def test_admin_stream_announce_renders_without_network_call():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    item_id = ObjectId()
    mock_item = {
        "_id": item_id,
        "type": "Announce",
        "activity": {
            "id": "https://remote.server/activities/announce-1",
            "type": "Announce",
            "actor": "https://remote.server/users/booster",
            "object": "https://original.server/notes/1",
            "published": "2026-09-17T08:00:00Z",
        },
        "meta": {
            "stream": True,
            "deleted": False,
            "actor": {
                "id": "https://remote.server/users/booster",
                "name": "Bob Booster",
                "preferredUsername": "booster",
                "url": "https://remote.server/users/booster",
            },
            "object": {
                "id": "https://original.server/notes/1",
                "type": "Note",
                "attributedTo": "https://original.server/users/author",
                "content": "<p>Original Boosted Post</p>",
                "published": "2026-09-17T07:00:00Z",
                "url": "https://original.server/notes/1",
            },
            "object_actor": {
                "id": "https://original.server/users/author",
                "name": "Alice Author",
                "preferredUsername": "author",
                "url": "https://original.server/users/author",
            },
        },
    }

    mock_backend = MagicMock()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with (
            patch("micronote.admin.paginated_query", return_value=([mock_item], None, None)),
            patch("micronote.admin._following_map", return_value={}),
            patch("micronote.filters.get_backend", return_value=mock_backend),
        ):
            resp = client.get("/admin/stream")
            assert resp.status_code == 200

            # Assert zero synchronous HTTP calls made during render
            mock_backend.fetch_iri_sync.assert_not_called()

            html = resp.data.decode("utf-8")
            assert "Bob Booster" in html
            assert "Alice Author" in html
            assert "Original Boosted Post" in html
