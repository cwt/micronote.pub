import hashlib
from unittest.mock import MagicMock, patch

from micronote.app import app
from micronote.filters import emojize, get_actor, get_custom_emoji_url, permalink_id


def test_permalink_id_deterministic():
    uri = "https://example.com/activities/12345"
    expected = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:12]
    assert permalink_id(uri) == expected
    assert permalink_id(uri) == permalink_id(uri)


def test_permalink_id_empty_and_none():
    assert permalink_id(None) == ""
    assert permalink_id("") == ""


def test_permalink_id_non_string():
    expected = hashlib.sha256(b"98765").hexdigest()[:12]
    assert permalink_id(98765) == expected


def test_permalink_id_format():
    result = permalink_id("sample-item-id")
    assert len(result) == 12
    # Verify it is hex characters only (safe for DOM element IDs)
    int(result, 16)


def test_get_actor_empty():
    assert get_actor(None) is None
    assert get_actor("") is None
    assert get_actor([]) is None


def test_get_actor_dict_passthrough():
    actor_data = {
        "id": "https://remote.example/users/alice",
        "type": "Person",
        "name": "Alice",
    }
    assert get_actor(actor_data) == actor_data

    actor_with_uname = {
        "id": "https://remote.example/users/alice",
        "preferredUsername": "alice",
    }
    assert get_actor(actor_with_uname) == actor_with_uname


def test_get_actor_from_db_cache():
    mock_db = MagicMock()
    mock_db.actors.find_one.return_value = {
        "remote_id": "https://remote.example/users/alice",
        "data": {"name": "Alice Cached"},
    }
    with (
        patch("micronote.filters.DB", mock_db),
        patch("micronote.filters.get_backend") as mock_backend,
    ):
        res = get_actor("https://remote.example/users/alice")
        assert res == {"name": "Alice Cached"}
        mock_backend.assert_not_called()


def test_get_actor_network_fallback_and_cache():
    mock_db = MagicMock()
    mock_db.actors.find_one.return_value = None
    mock_backend = MagicMock()
    mock_backend.fetch_iri_sync.return_value = {
        "id": "https://remote.example/users/bob",
        "name": "Bob",
    }
    with (
        app.app_context(),
        patch("micronote.filters.DB", mock_db),
        patch("micronote.filters.get_backend", return_value=mock_backend),
    ):
        res = get_actor("https://remote.example/users/bob")
        assert res == {"id": "https://remote.example/users/bob", "name": "Bob"}
        mock_backend.fetch_iri_sync.assert_called_once_with("https://remote.example/users/bob")
        mock_db.actors.update_one.assert_called_once_with(
            {"remote_id": "https://remote.example/users/bob"},
            {
                "$set": {
                    "remote_id": "https://remote.example/users/bob",
                    "data": {"id": "https://remote.example/users/bob", "name": "Bob"},
                }
            },
            upsert=True,
        )


def test_get_custom_emoji_url_cached():
    mock_file = MagicMock()
    mock_file._id = "mock_emoji_id"
    with patch("micronote.filters.MEDIA_CACHE.get_file", return_value=mock_file):
        url = get_custom_emoji_url("https://remote.example/emoji.png")
        assert url == "/media/mock_emoji_id"


def test_get_custom_emoji_url_uncached():
    with app.app_context(), patch("micronote.filters.MEDIA_CACHE.get_file", return_value=None):
        url = get_custom_emoji_url("https://remote.example/uncached_emoji.png")
        assert url == "https://remote.example/uncached_emoji.png"


def test_emojize_with_obj_tags():
    obj = {
        "tag": [
            {
                "type": "Emoji",
                "name": ":my_cat:",
                "icon": {"url": "https://remote.example/cat.png"},
            }
        ]
    }
    html = "<p>Look at :my_cat: and :snake:</p>"
    res = emojize(html, obj)
    assert (
        '<img class="custom-emoji" src="https://remote.example/cat.png" alt=":my_cat:" title=":my_cat:" loading="lazy">'
        in res
    )
    assert "🐍" in res


def test_emojize_with_actor_emojis():
    actor = {"emojis": {"pepe": "https://remote.example/pepe.png"}}
    html = "<p>Hello :pepe:</p>"
    res = emojize(html, None, actor)
    assert (
        '<img class="custom-emoji" src="https://remote.example/pepe.png" alt=":pepe:" title=":pepe:" loading="lazy">'
        in res
    )


def test_get_file_url_enqueues_background_caching_on_miss():
    from micronote.filters import _PENDING_CACHE_JOBS, _get_file_url
    from micronote.utils.media import Kind

    test_url = "https://remote.example/attachment-unique-123.png"
    _PENDING_CACHE_JOBS.clear()

    with (
        app.app_context(),
        patch("micronote.filters.MEDIA_CACHE.get_file", return_value=None),
        patch("micronote.tasks.enqueue_job") as mock_enqueue,
    ):
        result = _get_file_url(test_url, 720, Kind.ATTACHMENT)
        assert result == test_url
        mock_enqueue.assert_called_once_with(
            "cache_media_item",
            iri=test_url,
            payload={"kind": "attachment"},
        )


def test_enqueue_media_cache_deduplication():
    from micronote.filters import _PENDING_CACHE_JOBS, _enqueue_media_cache
    from micronote.utils.media import Kind

    test_url = "https://remote.example/avatar-dedup.png"
    _PENDING_CACHE_JOBS.clear()

    with (
        app.app_context(),
        patch("micronote.tasks.enqueue_job") as mock_enqueue,
    ):
        _enqueue_media_cache(test_url, Kind.ACTOR_ICON)
        _enqueue_media_cache(test_url, Kind.ACTOR_ICON)
        # Should only have been enqueued once due to TTLCache deduplication
        assert mock_enqueue.call_count == 1


def test_enqueue_media_cache_skips_invalid_urls():
    from micronote.filters import _enqueue_media_cache
    from micronote.utils.media import Kind

    with patch("micronote.tasks.enqueue_job") as mock_enqueue:
        _enqueue_media_cache("", Kind.ATTACHMENT)
        _enqueue_media_cache(None, Kind.ATTACHMENT)  # type: ignore[arg-type]
        _enqueue_media_cache("/static/img.png", Kind.ATTACHMENT)
        mock_enqueue.assert_not_called()


def test_gridfs_cache_is_bounded_ttl_cache():
    from cachetools import TTLCache

    from micronote.filters import _GRIDFS_CACHE

    assert isinstance(_GRIDFS_CACHE, TTLCache)
    assert _GRIDFS_CACHE.maxsize == 4096
    assert _GRIDFS_CACHE.ttl == 3600
