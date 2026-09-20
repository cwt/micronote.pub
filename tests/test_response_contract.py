"""Content-negotiation contract for dual and ActivityPub-only routes.

Refactors (improvement plan Phase 4) must preserve which delivery mechanism
answers each Accept header, including the 404 rejection of browser requests
on ActivityPub-only endpoints.
"""

from unittest.mock import patch

from micronote.app import app

HTML_ACCEPT = "text/html,application/xhtml+xml"
AP_ACCEPT = "application/activity+json"
AP_MIMETYPES = {"application/activity+json", "application/json"}


def _logged_in_client():
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client


def test_dual_routes_serve_html_and_activitypub():
    with (
        patch("micronote.cache.get_page", return_value=None),
        patch("micronote.cache.set_page"),
        patch("micronote.views.paginated_query", return_value=([], None, None)),
    ):
        with app.test_client() as client:
            for path in ("/", "/followers", "/liked"):
                assert client.get(path, headers={"Accept": HTML_ACCEPT}).status_code == 200
                ap_resp = client.get(path, headers={"Accept": AP_ACCEPT})
                assert ap_resp.status_code == 200
                assert ap_resp.mimetype in AP_MIMETYPES

            with _logged_in_client() as admin:
                assert admin.get("/following", headers={"Accept": HTML_ACCEPT}).status_code == 200
            assert client.get("/following", headers={"Accept": AP_ACCEPT}).status_code == 200

            # Notes: HTML looks the note up (404 when missing), ActivityPub
            # redirects to the outbox activity endpoint.
            assert client.get("/note/missing-note", headers={"Accept": HTML_ACCEPT}).status_code == 404
            redirect = client.get("/note/missing-note", headers={"Accept": AP_ACCEPT})
            assert redirect.status_code == 302
            assert redirect.headers["Location"].endswith("/outbox/missing-note/activity")

            # Tags: the existence check runs before negotiation, so both
            # representations agree on 404 for an unknown tag.
            for accept in (HTML_ACCEPT, AP_ACCEPT):
                assert client.get("/tags/no-such-tag-xyz", headers={"Accept": accept}).status_code == 404


def test_activitypub_only_routes_reject_browsers():
    with app.test_client() as client:
        for path in ("/outbox", "/featured", "/inbox", "/outbox/abc/replies"):
            assert client.get(path, headers={"Accept": HTML_ACCEPT}).status_code == 404


def test_activitypub_only_routes_serve_activitypub():
    with app.test_client() as client:
        assert client.get("/outbox", headers={"Accept": AP_ACCEPT}).status_code == 200
        assert client.get("/featured", headers={"Accept": AP_ACCEPT}).status_code == 200

        # The inbox collection requires the API key or a logged-in session.
        assert client.get("/inbox", headers={"Accept": AP_ACCEPT}).status_code == 404
        with _logged_in_client() as admin:
            assert admin.get("/inbox", headers={"Accept": AP_ACCEPT}).status_code == 200
