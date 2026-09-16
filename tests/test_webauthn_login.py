"""Unit tests for WebAuthn login error handling and resilience."""

from unittest.mock import MagicMock, patch

from micronote.app import app
from micronote.config import USERNAME


def test_admin_login_webauthn_missing_assertion():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    fake_credential = object()
    mock_server = MagicMock()
    mock_server.authenticate_begin.return_value = ({}, "mock_state")
    with patch("micronote.admin.csrf.protect"):
        with patch("micronote.admin.verify_pass", return_value=True):
            with patch("micronote.utils.webauthn.stored_credentials", return_value=[fake_credential]):
                with patch("micronote.utils.webauthn.load_state", return_value={"mock": "state"}):
                    with patch("micronote.utils.webauthn.get_server", return_value=mock_server):
                        # Submit password but no assertion field
                        resp = client.post(
                            "/login",
                            data={"username": USERNAME, "pass": "secret"},
                        )
                        assert resp.status_code == 200
                        assert b"Security key assertion missing or session expired." in resp.data


def test_admin_login_webauthn_invalid_json_assertion():
    app.config["TESTING"] = True
    client = app.test_client()

    fake_credential = object()
    mock_server = MagicMock()
    mock_server.authenticate_begin.return_value = ({}, "mock_state")
    with patch("micronote.admin.csrf.protect"):
        with patch("micronote.admin.verify_pass", return_value=True):
            with patch("micronote.utils.webauthn.stored_credentials", return_value=[fake_credential]):
                with patch("micronote.utils.webauthn.load_state", return_value={"mock": "state"}):
                    with patch("micronote.utils.webauthn.get_server", return_value=mock_server):
                        resp = client.post(
                            "/login",
                            data={"username": USERNAME, "pass": "secret", "assertion": "not-a-json"},
                        )
                        assert resp.status_code == 200
                        assert b"Security key authentication failed." in resp.data
