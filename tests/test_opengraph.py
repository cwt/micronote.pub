from unittest.mock import MagicMock, patch

import pytest
from active_boxes.errors import ActivityUnavailableError, NotAnActivityError

from micronote.handlers import fetch_og_metadata as worker_fetch_og_metadata
from micronote.utils.lookup import lookup
from micronote.utils.opengraph import _youtube_channel, _youtube_video_id, fetch_og_metadata


def test_lookup_converts_non_json_activity_unavailable_to_not_an_activity():
    with (
        patch("micronote.utils.lookup.requests.get") as mock_get,
        patch("micronote.utils.lookup.ap.fetch_remote_activity_sync") as mock_fetch,
    ):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.text = "<html><body>Not JSON</body></html>"
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_get.return_value = mock_resp

        mock_fetch.side_effect = ActivityUnavailableError(
            'unable to fetch https://example.com/, unknown error: NotAnActivityError("is not JSON: 200")'
        )

        with pytest.raises(NotAnActivityError):
            lookup("https://example.com/")


def test_fetch_og_metadata_ignores_lookup_activity_unavailable_error():
    test_link = "https://x11cp.org/apps/mxascii/"
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta property="og:title" content="mxascii App">
        <meta property="og:url" content="https://x11cp.org/apps/mxascii/">
        <meta property="og:image" content="https://x11cp.org/img.png">
    </head>
    <body>Hello</body>
    </html>
    """

    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.text = html_content
    mock_resp.raise_for_status = MagicMock()

    with (
        patch(
            "micronote.utils.opengraph.lookup",
            side_effect=ActivityUnavailableError("unable to fetch, NotAnActivityError: is not JSON"),
        ),
        patch("micronote.utils.opengraph.check_url"),
        patch("micronote.utils.opengraph.requests.get", return_value=mock_resp),
    ):
        res = fetch_og_metadata("test-agent", [test_link])
        assert len(res) == 1
        assert res[0]["title"] == "mxascii App"
        assert res[0]["url"] == test_link
        assert res[0]["image"] == "https://x11cp.org/img.png"


def test_fetch_og_metadata_skips_ap_actors():
    actor_link = "https://mastodon.social/users/alice"
    mock_actor = MagicMock()
    mock_actor.has_type.return_value = True

    with (
        patch("micronote.utils.opengraph.lookup", return_value=mock_actor),
        patch("micronote.utils.opengraph.check_url"),
        patch("micronote.utils.opengraph.requests.get") as mock_get,
    ):
        res = fetch_og_metadata("test-agent", [actor_link])
        assert res == []
        mock_get.assert_not_called()


def test_fetch_og_metadata_handles_broken_link_without_crashing_others():
    bad_link = "https://broken.example/404"
    good_link = "https://good.example/page"

    good_resp = MagicMock()
    good_resp.headers = {"content-type": "text/html"}
    good_resp.text = '<html><head><meta property="og:title" content="Good Page"><meta property="og:url" content="https://good.example/page"></head></html>'
    good_resp.raise_for_status = MagicMock()

    def mock_requests_get(url, **kwargs):
        if url == bad_link:
            import requests

            mock_bad = MagicMock()
            mock_bad.status_code = 404
            raise requests.HTTPError("404 Not Found", response=mock_bad)
        return good_resp

    with (
        patch("micronote.utils.opengraph.lookup", side_effect=Exception("lookup failed")),
        patch("micronote.utils.opengraph.check_url"),
        patch("micronote.utils.opengraph.requests.get", side_effect=mock_requests_get),
    ):
        res = fetch_og_metadata("test-agent", [bad_link, good_link])
        assert len(res) == 1
        assert res[0]["title"] == "Good Page"


def test_youtube_video_id_variants():
    cases = {
        "https://youtu.be/Obpa9bzpzvQ?si=xyz": "Obpa9bzpzvQ",
        "https://youtu.be/Obpa9bzpzvQ": "Obpa9bzpzvQ",
        "https://www.youtube.com/watch?v=Obpa9bzpzvQ": "Obpa9bzpzvQ",
        "https://youtube.com/watch?si=x&v=Obpa9bzpzvQ&feature=youtu.be": "Obpa9bzpzvQ",
        "https://www.youtube.com/shorts/Obpa9bzpzvQ": "Obpa9bzpzvQ",
        "https://www.youtube.com/embed/Obpa9bzpzvQ": "Obpa9bzpzvQ",
        "https://m.youtube.com/watch?v=Obpa9bzpzvQ": "Obpa9bzpzvQ",
        "https://music.youtube.com/watch?v=Obpa9bzpzvQ": "Obpa9bzpzvQ",
        "https://www.youtube.com/watch": None,
        "https://www.youtube.com/playlist?list=PLabc": None,
        "https://example.com/Obpa9bzpzvQ": None,
        "https://youtu.be/xyz": None,
    }
    for url, expected in cases.items():
        assert _youtube_video_id(url) == expected, url


def test_youtube_channel_url_variants():
    cases = {
        "https://www.youtube.com/@babylon5": ("https://www.youtube.com/@babylon5", "@babylon5"),
        "https://youtube.com/@babylon5?tab=videos": ("https://www.youtube.com/@babylon5", "@babylon5"),
        "https://m.youtube.com/@babylon5": ("https://www.youtube.com/@babylon5", "@babylon5"),
        "https://www.youtube.com/channel/UCbYOzvigGQ4GsLfO9J2lY2g": (
            "https://www.youtube.com/channel/UCbYOzvigGQ4GsLfO9J2lY2g",
            "YouTube channel",
        ),
        "https://www.youtube.com/user/somebody": ("https://www.youtube.com/user/somebody", "somebody"),
        "https://www.youtube.com/c/BrandName": ("https://www.youtube.com/c/BrandName", "BrandName"),
        "https://www.youtube.com/watch?v=Obpa9bzpzvQ": None,
        "https://www.youtube.com/playlist?list=PL123": None,
        "https://youtu.be/Obpa9bzpzvQ": None,
        "https://example.com/@notyoutube": None,
    }
    for url, expected in cases.items():
        assert _youtube_channel(url) == expected, url


def test_fetch_og_metadata_youtube_channel_scrapes_page():
    link = "https://www.youtube.com/@babylon5"
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta property="og:title" content="Babylon 5">
        <meta property="og:url" content="https://www.youtube.com/channel/UCwhFvS02GwuTx32B3RVKe8A">
        <meta property="og:image" content="https://yt3.googleusercontent.com/F1HKaOra5ySW=s900-c-k">
        <meta property="og:description" content="A neutral station">
    </head>
    <body>Hello</body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.text = html_content
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("micronote.utils.opengraph.check_url"),
        patch("micronote.utils.opengraph.requests.get", return_value=mock_resp) as mock_get,
    ):
        res = fetch_og_metadata("test-agent", [link])

    # The channel page (not oEmbed, not the watch page) is what gets scraped.
    mock_get.assert_called_once_with(
        "https://www.youtube.com/@babylon5", headers={"User-Agent": "test-agent"}, timeout=15
    )
    assert len(res) == 1
    card = res[0]
    assert card["title"] == "Babylon 5"
    assert card["url"] == "https://www.youtube.com/channel/UCwhFvS02GwuTx32B3RVKe8A"
    assert card["image"] == "https://yt3.googleusercontent.com/F1HKaOra5ySW=s900-c-k"


def test_fetch_og_metadata_youtube_channel_fallback_when_page_unavailable():
    link = "https://www.youtube.com/@babylon5"
    with (
        patch("micronote.utils.opengraph.check_url"),
        patch("micronote.utils.opengraph.requests.get", side_effect=Exception("channel page unreachable")),
    ):
        res = fetch_og_metadata("test-agent", [link])

    # The deterministic text-only card is still produced.
    assert len(res) == 1
    card = res[0]
    assert card["url"] == "https://www.youtube.com/@babylon5"
    assert card["title"] == "@babylon5"
    assert card["description"] == "YouTube channel"
    assert card["site_name"] == "YouTube"
    assert "image" not in card


def test_fetch_og_metadata_youtube_uses_stable_endpoints():
    link = "https://youtu.be/Obpa9bzpzvQ?si=sSvAXkYwKNXUDt2q"
    mock_oembed = MagicMock()
    mock_oembed.json.return_value = {
        "title": "How to make a PVC DIY transverse flute - Tutorial",
        "author_name": "Nicolas Bras",
    }
    mock_oembed.raise_for_status = MagicMock()

    with (
        patch("micronote.utils.opengraph.check_url"),
        patch("micronote.utils.opengraph.requests.get", return_value=mock_oembed) as mock_get,
    ):
        res = fetch_og_metadata("test-agent", [link])

    assert len(res) == 1
    card = res[0]
    assert card["url"] == "https://www.youtube.com/watch?v=Obpa9bzpzvQ"
    assert card["image"] == "https://i.ytimg.com/vi/Obpa9bzpzvQ/hqdefault.jpg"
    assert card["title"] == "How to make a PVC DIY transverse flute - Tutorial"
    assert card["description"] == "Nicolas Bras"
    assert card["site_name"] == "YouTube"
    # Only the oEmbed endpoint is hit, never the consent-gated watch page.
    mock_get.assert_called_once_with(
        "https://www.youtube.com/oembed",
        params={"url": "https://www.youtube.com/watch?v=Obpa9bzpzvQ", "format": "json"},
        headers={"User-Agent": "test-agent"},
        timeout=15,
    )


def test_fetch_og_metadata_youtube_fallback_when_oembed_down():
    link = "https://www.youtube.com/watch?v=Obpa9bzpzvQ"
    with (
        patch("micronote.utils.opengraph.check_url"),
        patch("micronote.utils.opengraph.requests.get", side_effect=Exception("oembed unreachable")),
    ):
        res = fetch_og_metadata("test-agent", [link])

    # The thumbnail card is still produced without a title.
    assert len(res) == 1
    card = res[0]
    assert card["title"] == "YouTube video"
    assert card["description"] == ""
    assert card["image"] == "https://i.ytimg.com/vi/Obpa9bzpzvQ/hqdefault.jpg"


def test_worker_fetch_og_metadata_handles_unavailable_activity():
    mock_db = MagicMock()
    with (
        patch(
            "micronote.handlers.ap.fetch_remote_activity_sync",
            side_effect=ActivityUnavailableError("activity unavailable 401"),
        ),
        patch("micronote.handlers.DB", mock_db),
    ):
        # Must return cleanly without raising
        worker_fetch_og_metadata({"iri": "https://example.com/activity/401"})
        mock_db.activities.update_one.assert_not_called()
