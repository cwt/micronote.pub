from unittest.mock import MagicMock, patch

import pytest
from active_boxes.errors import ActivityUnavailableError, NotAnActivityError

from micronote.utils.lookup import lookup
from micronote.utils.opengraph import fetch_og_metadata
from micronote.worker import fetch_og_metadata as worker_fetch_og_metadata


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


def test_worker_fetch_og_metadata_handles_unavailable_activity():
    mock_db = MagicMock()
    with (
        patch(
            "micronote.worker.ap.fetch_remote_activity_sync",
            side_effect=ActivityUnavailableError("activity unavailable 401"),
        ),
        patch("micronote.worker.DB", mock_db),
    ):
        # Must return cleanly without raising
        worker_fetch_og_metadata({"iri": "https://example.com/activity/401"})
        mock_db.activities.update_one.assert_not_called()
