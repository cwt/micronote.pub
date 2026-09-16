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
