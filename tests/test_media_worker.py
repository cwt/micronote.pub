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
