from unittest.mock import MagicMock, patch

from micronote.utils.media import MediaCache


def test_cache_actor_icon_handles_corrupted_image():
    mock_factory = MagicMock()
    media_cache = MediaCache(mock_factory, "test-user-agent")

    with (
        patch.object(media_cache, "get_file", return_value=None),
        patch("micronote.utils.media.load", side_effect=ValueError("Corrupted image stream")),
    ):
        # Must return None safely without raising ValueError
        result = media_cache.cache_actor_icon("https://example.com/bad-avatar.png")
        assert result is None


def test_cache_actor_icon_handles_http_error():
    mock_factory = MagicMock()
    media_cache = MediaCache(mock_factory, "test-user-agent")

    with (
        patch.object(media_cache, "get_file", return_value=None),
        patch("micronote.utils.media.load", side_effect=OSError("Network unreachable")),
    ):
        result = media_cache.cache_actor_icon("https://example.com/unreachable-avatar.png")
        assert result is None


def test_cache_actor_icon_skips_if_already_cached():
    mock_factory = MagicMock()
    media_cache = MediaCache(mock_factory, "test-user-agent")

    with (
        patch.object(media_cache, "get_file", return_value=MagicMock()) as mock_get_file,
        patch("micronote.utils.media.load") as mock_load,
    ):
        media_cache.cache_actor_icon("https://example.com/existing-avatar.png")
        mock_get_file.assert_called_once()
        mock_load.assert_not_called()


def test_cache_custom_emoji_skips_if_already_cached():
    mock_factory = MagicMock()
    media_cache = MediaCache(mock_factory, "test-user-agent")

    with (
        patch.object(media_cache, "get_file", return_value=MagicMock()) as mock_get_file,
        patch("micronote.utils.media.load") as mock_load,
    ):
        media_cache.cache_custom_emoji("https://example.com/existing-emoji.png")
        mock_get_file.assert_called_once()
        mock_load.assert_not_called()


def test_cache_custom_emoji_stores_webp():
    mock_factory = MagicMock()
    media_cache = MediaCache(mock_factory, "test-user-agent")

    mock_img = MagicMock()
    mock_img.width = 64
    mock_img.height = 64

    with (
        patch.object(media_cache, "get_file", return_value=None),
        patch("micronote.utils.media.load", return_value=mock_img),
        patch("micronote.utils.media._encode_image", return_value=b"fake_webp"),
        patch.object(media_cache, "_store") as mock_store,
    ):
        media_cache.cache_custom_emoji("https://example.com/new-emoji.png")
        mock_store.assert_called_once()
        call_args = mock_store.call_args
        assert call_args[0][0] == b"fake_webp"
        assert call_args[0][1] == "https://example.com/new-emoji.png"
        assert call_args[0][4] == "custom_emoji"


def test_cache_custom_emoji_handles_error():
    mock_factory = MagicMock()
    media_cache = MediaCache(mock_factory, "test-user-agent")

    with (
        patch.object(media_cache, "get_file", return_value=None),
        patch("micronote.utils.media.load", side_effect=Exception("Failed to download")),
    ):
        result = media_cache.cache_custom_emoji("https://example.com/bad-emoji.png")
        assert result is None


def test_cache_media_item_calls_media_cache():
    from micronote.utils.media import Kind
    from micronote.worker import cache_media_item

    mock_media_cache = MagicMock()
    with patch("micronote.worker.MEDIA_CACHE", mock_media_cache):
        cache_media_item(
            {
                "iri": "https://example.com/photo.jpg",
                "payload": {"kind": "attachment"},
            }
        )
        mock_media_cache.cache.assert_called_once_with("https://example.com/photo.jpg", Kind.ATTACHMENT)


def test_cache_media_item_handles_4xx_without_retry():
    import requests

    from micronote.worker import cache_media_item

    mock_response = MagicMock()
    mock_response.status_code = 404
    http_error = requests.HTTPError(response=mock_response)

    with patch("micronote.worker.MEDIA_CACHE.cache", side_effect=http_error):
        # Should catch and return without re-raising 4xx
        cache_media_item(
            {
                "iri": "https://example.com/notfound.png",
                "payload": {"kind": "actor_icon"},
            }
        )


def test_cache_media_item_raises_5xx_for_retry():
    import pytest
    import requests

    from micronote.worker import cache_media_item

    mock_response = MagicMock()
    mock_response.status_code = 503
    http_error = requests.HTTPError(response=mock_response)

    with patch("micronote.worker.MEDIA_CACHE.cache", side_effect=http_error):
        with pytest.raises(requests.HTTPError):
            cache_media_item(
                {
                    "iri": "https://example.com/servererror.png",
                    "payload": {"kind": "attachment"},
                }
            )


def test_cache_media_item_ignores_empty_url():
    from micronote.worker import cache_media_item

    with patch("micronote.worker.MEDIA_CACHE.cache") as mock_cache:
        cache_media_item({"iri": None, "payload": {}})
        mock_cache.assert_not_called()


def test_serve_grid_file_mimetype_fallback_and_nosniff():
    from micronote.app import app, serve_grid_file

    mock_grid = MagicMock()
    mock_grid.read.return_value = b"binary payload"
    mock_grid.upload_date = "2026-09-20T00:00:00Z"
    mock_grid.md5 = "testmd5"
    mock_grid.metadata = None  # None or missing content_type

    with app.app_context():
        resp = serve_grid_file(mock_grid)
        assert resp.mimetype == "application/octet-stream"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert "text/html" not in resp.headers.get("Content-Type", "")


def test_save_upload_unknown_extension_stores_octet_stream():
    from io import BytesIO

    mock_factory = MagicMock()
    media_cache = MediaCache(mock_factory, "test-user-agent")

    with patch.object(media_cache, "_store", return_value="fake_oid") as mock_store:
        stored = media_cache.save_upload(BytesIO(b"data"), "unknown.xyz123", (1000, 1000))
        assert stored.mimetype == "application/octet-stream"
        mock_store.assert_called_once()
        _, _, _, stored_mtype, _ = mock_store.call_args[0]
        assert stored_mtype == "application/octet-stream"
