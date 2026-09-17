from unittest.mock import MagicMock, patch

from active_boxes.errors import ActivityUnavailableError

from micronote.worker import cache_actor, cache_object


def test_cache_object_handles_401_actor_unauthorized():
    mock_activity = MagicMock()
    mock_activity.id = "https://mas.to/users/veer66/statuses/117279205452392879/activity"

    mock_note = MagicMock()
    mock_note.attributedTo = "https://mastodon.social/users/titipat"
    mock_note.to_dict.return_value = {
        "id": "https://mastodon.social/users/titipat/statuses/117279089759684770",
        "type": "Note",
        "content": "Hello",
        "attributedTo": "https://mastodon.social/users/titipat",
    }
    # Simulate remote server returning 401 Unauthorized for the actor profile
    mock_note.get_actor_sync.side_effect = ActivityUnavailableError(
        "unable to fetch https://mastodon.social/users/titipat, unknown error: 401, message='Unauthorized'"
    )
    mock_activity.get_object_sync.return_value = mock_note

    mock_db = MagicMock()

    with (
        patch("micronote.worker.ap.fetch_remote_activity_sync", return_value=mock_activity),
        patch("micronote.worker.DB", mock_db),
    ):
        # Must not raise an exception
        cache_object({"iri": mock_activity.id})

    # Verify DB update was called with object and fallback object_actor
    mock_db.activities.update_one.assert_called_once()
    call_args = mock_db.activities.update_one.call_args
    assert call_args[0][0] == {"remote_id": mock_activity.id}
    set_payload = call_args[0][1]["$set"]
    assert "meta.object" in set_payload
    assert set_payload["meta.object_actor"]["id"] == "https://mastodon.social/users/titipat"


def test_cache_object_handles_unavailable_object():
    mock_activity = MagicMock()
    mock_activity.id = "https://example.com/announce/1"
    mock_activity.get_object_sync.side_effect = ActivityUnavailableError("object 401")

    mock_db = MagicMock()

    with (
        patch("micronote.worker.ap.fetch_remote_activity_sync", return_value=mock_activity),
        patch("micronote.worker.DB", mock_db),
    ):
        # Must return cleanly without raising
        cache_object({"iri": mock_activity.id})

    # DB activities should not be updated with corrupt data
    mock_db.activities.update_one.assert_not_called()


def test_cache_actor_handles_401_actor_unauthorized():
    mock_activity = MagicMock()
    mock_activity.id = "https://example.com/activity/1"
    mock_activity.actor = "https://mastodon.social/users/privateuser"
    mock_activity.has_type.return_value = False
    mock_activity.get_actor_sync.side_effect = ActivityUnavailableError("actor 401")

    mock_db = MagicMock()

    with (
        patch("micronote.worker.ap.fetch_remote_activity_sync", return_value=mock_activity),
        patch("micronote.worker.DB", mock_db),
    ):
        cache_actor({"iri": mock_activity.id, "also_cache_attachments": False})

    mock_db.activities.update_one.assert_called_once()
    call_args = mock_db.activities.update_one.call_args
    set_payload = call_args[0][1]["$set"]
    assert set_payload["meta.actor"]["id"] == "https://mastodon.social/users/privateuser"


def test_cache_object_caches_attachments_and_actor_icon():
    mock_activity = MagicMock()
    mock_activity.id = "https://example.com/announce/2"

    mock_note = MagicMock()
    mock_note.to_dict.return_value = {
        "id": "https://example.com/notes/2",
        "type": "Note",
        "attachment": [
            {"type": "Image", "url": "https://example.com/img1.png", "mediaType": "image/png"},
            {"type": "Document", "url": "https://example.com/doc.pdf", "mediaType": "application/pdf"},
        ],
        "tag": [
            {"type": "Emoji", "name": ":blob:", "icon": {"url": "https://example.com/blob.png"}},
        ],
    }
    mock_note._data = mock_note.to_dict.return_value

    mock_actor = MagicMock()
    mock_actor.id = "https://example.com/users/alice"
    mock_actor.url = "https://example.com/users/alice"
    mock_actor.icon = {"type": "Image", "url": "https://example.com/alice.png"}
    mock_actor.name = "Alice :sparkles:"
    mock_actor.preferredUsername = "alice"
    mock_actor._data = {
        "tag": [{"type": "Emoji", "name": ":sparkles:", "icon": {"url": "https://example.com/sparkles.png"}}],
    }
    mock_note.get_actor_sync.return_value = mock_actor
    mock_activity.get_object_sync.return_value = mock_note

    mock_db = MagicMock()
    mock_media_cache = MagicMock()

    with (
        patch("micronote.worker.ap.fetch_remote_activity_sync", return_value=mock_activity),
        patch("micronote.worker.DB", mock_db),
        patch("micronote.worker.MEDIA_CACHE", mock_media_cache),
    ):
        cache_object({"iri": mock_activity.id})

    # Verify attachment, actor icon, and emojis were cached
    from micronote.utils.media import Kind

    mock_media_cache.cache.assert_any_call("https://example.com/img1.png", Kind.ATTACHMENT)
    mock_media_cache.cache.assert_any_call("https://example.com/alice.png", Kind.ACTOR_ICON)
    mock_media_cache.cache.assert_any_call("https://example.com/blob.png", Kind.CUSTOM_EMOJI)
    mock_media_cache.cache.assert_any_call("https://example.com/sparkles.png", Kind.CUSTOM_EMOJI)
