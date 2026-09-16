import asyncio
import base64
from unittest.mock import AsyncMock, patch

from active_boxes.errors import ActivityUnavailableError
from active_boxes.httpsig import _verify_bytes_rsa

from micronote import activitypub
from micronote.config import KEY, USER_AGENT
from micronote.utils.delivery import delivery_target, sign_fetch_request


def test_sign_fetch_request_structure_and_signature():
    url = "https://bsd.network/users/thomasadam"
    headers = sign_fetch_request(url, KEY, USER_AGENT)

    assert headers["Host"] == "bsd.network"
    assert headers["User-Agent"] == USER_AGENT
    assert "Accept" in headers
    assert "Date" in headers
    assert "Signature" in headers

    sig_header = headers["Signature"]
    assert f'keyId="{KEY.key_id()}"' in sig_header
    assert 'algorithm="rsa-sha256"' in sig_header
    assert 'headers="(request-target) host date accept"' in sig_header

    # Extract signature part and verify cryptographically against KEY.pubkey
    parts = dict(part.split("=", 1) for part in sig_header.split(","))
    sig_b64 = parts["signature"].strip('"')
    sig_bytes = base64.b64decode(sig_b64)

    target = delivery_target(url)
    expected_signed_string = (
        f"(request-target): get {target}\nhost: {headers['Host']}\ndate: {headers['Date']}\naccept: {headers['Accept']}"
    )

    assert _verify_bytes_rsa(KEY.privkey.public_key(), expected_signed_string.encode("utf-8"), sig_bytes)


def test_fetch_remote_iri_signed_success():
    backend = activitypub.MicroblogPubBackend()
    url = "https://bsd.network/users/thomasadam"
    expected_response = {
        "id": url,
        "type": "Person",
        "name": "Thomas Adam",
    }

    mock_client = AsyncMock()
    mock_client.get_json.return_value = expected_response

    async def run_test():
        with (
            patch("micronote.activitypub.get_http_client", return_value=mock_client),
            patch.object(backend, "check_url", new=AsyncMock()),
            patch("micronote.activitypub.AUTHORIZED_FETCH", True),
        ):
            res = await backend._fetch_remote_iri(url)
            assert res == expected_response
            mock_client.get_json.assert_awaited_once()
            call_kwargs = mock_client.get_json.call_args[1]
            assert "headers" in call_kwargs
            assert "Signature" in call_kwargs["headers"]

    asyncio.run(run_test())


def test_fetch_remote_iri_falls_back_to_unsigned():
    backend = activitypub.MicroblogPubBackend()
    url = "https://example.com/notes/123"
    expected_response = {
        "id": url,
        "type": "Note",
        "content": "Public note",
    }

    mock_client = AsyncMock()
    # Signed fetch fails (e.g. 401 on remote server rejecting signed localhost)
    mock_client.get_json.side_effect = ActivityUnavailableError("signed fetch 401")

    async def run_test():
        with (
            patch("micronote.activitypub.get_http_client", return_value=mock_client),
            patch.object(backend, "check_url", new=AsyncMock()),
            patch("micronote.activitypub.AUTHORIZED_FETCH", True),
            patch(
                "active_boxes.backend.Backend.fetch_iri",
                new=AsyncMock(return_value=expected_response),
            ) as mock_super_fetch,
        ):
            res = await backend._fetch_remote_iri(url)
            assert res == expected_response
            mock_super_fetch.assert_awaited_once_with(url)

    asyncio.run(run_test())
