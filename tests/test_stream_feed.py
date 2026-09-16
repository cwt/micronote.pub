from unittest.mock import MagicMock, patch

from neosqlite.objectid import ObjectId

from micronote.activitypub import build_inbox_json_feed


def test_build_inbox_json_feed_no_network_calls():
    oid1 = ObjectId()
    oid2 = ObjectId()
    oid3 = ObjectId()

    mock_items = [
        # Item 1: actor cached in meta.actor
        {
            "_id": oid1,
            "activity": {
                "id": "https://remote.server/activities/1",
                "actor": "https://remote.server/users/alice",
                "object": {
                    "url": "https://remote.server/notes/1",
                    "content": "Hello from Alice",
                    "published": "2026-09-16T12:00:00Z",
                },
            },
            "meta": {
                "actor": {
                    "name": "Alice Wonderland",
                    "url": "https://remote.server/users/alice",
                    "icon": {"url": "https://remote.server/avatars/alice.png"},
                }
            },
        },
        # Item 2: actor not in meta, but in DB.actors
        {
            "_id": oid2,
            "activity": {
                "id": "https://remote.server/activities/2",
                "actor": "https://remote.server/users/bob",
                "object": {
                    "url": "https://remote.server/notes/2",
                    "content": "Hello from Bob",
                    "published": "2026-09-16T12:01:00Z",
                },
            },
            "meta": {},
        },
        # Item 3: actor completely missing (unresolvable)
        {
            "_id": oid3,
            "activity": {
                "id": "https://remote.server/activities/3",
                "actor": "https://remote.server/users/charlie",
                "object": {
                    "url": "https://remote.server/notes/3",
                    "content": "Hello from Charlie",
                    "published": "2026-09-16T12:02:00Z",
                },
            },
            "meta": {},
        },
    ]

    mock_activities = MagicMock()
    mock_activities.find.return_value.sort.return_value = mock_items

    mock_actors = MagicMock()
    mock_actors.find.return_value = [
        {
            "remote_id": "https://remote.server/users/bob",
            "data": {
                "name": "Bob Builder",
                "url": "https://remote.server/users/bob",
                "icon": {"url": "https://remote.server/avatars/bob.png"},
            },
        }
    ]

    mock_backend = MagicMock()

    with (
        patch("micronote.activitypub.DB.activities", mock_activities),
        patch("micronote.activitypub.DB.actors", mock_actors),
        patch("micronote.activitypub.ap.get_backend", return_value=mock_backend),
    ):
        feed = build_inbox_json_feed("/api/stream")

        # Zero network calls allowed
        mock_backend.fetch_iri_sync.assert_not_called()

        assert len(feed["items"]) == 3

        # Item 1 author from meta.actor
        assert feed["items"][0]["author"]["name"] == "Alice Wonderland"
        assert feed["items"][0]["author"]["avatar"] == "https://remote.server/avatars/alice.png"

        # Item 2 author from DB.actors
        assert feed["items"][1]["author"]["name"] == "Bob Builder"
        assert feed["items"][1]["author"]["avatar"] == "https://remote.server/avatars/bob.png"

        # Item 3 fallback author without crash
        assert feed["items"][2]["author"]["name"] == "https://remote.server/users/charlie"
        assert feed["items"][2]["author"]["avatar"] is None
