"""Draft-cavage HTTP Signature helper for outbound deliveries.

active-boxes sign_request reads header names case-sensitively from the
given dict, while real HTTP headers are case-insensitive. Signing a
plain dict with capitalized keys therefore signs empty values for
host/date/digest, which no strict verifier accepts. Signing the
prepared request's CaseInsensitiveDict keeps the signed bytes
identical to the sent bytes.

The (request-target) is always the origin-form path: full URLs there
are proxy-form and strict verifiers reject them.
"""

from urllib.parse import urlparse

from active_boxes.httpsig import sign_request_sync

CONTENT_TYPE = "application/activity+json"


def delivery_target(url: str) -> str:
    """Origin-form request target for signing (path plus query)."""
    parsed = urlparse(url)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return target


def sign_delivery_request(url: str, body: str | bytes, key, user_agent: str) -> dict[str, str]:
    """Returns header dict with a valid signature for POSTing body to url."""
    import requests

    netloc = urlparse(url).netloc
    prepared = requests.Request(
        "POST",
        url,
        data=body,
        headers={
            "Content-Type": CONTENT_TYPE,
            "Accept": CONTENT_TYPE,
            "User-Agent": user_agent,
            "Host": netloc,
        },
    ).prepare()
    # Pass host explicitly: with an origin-form target the signer would
    # otherwise derive a port-less Host and overwrite the correct one.
    signed = sign_request_sync("POST", delivery_target(url), prepared.headers, key, body, host=netloc)
    return dict(signed)


def sign_fetch_request(url: str, key, user_agent: str) -> dict[str, str]:
    """Returns header dict with a valid draft-cavage signature for GETting url (Authorized Fetch)."""
    import base64
    from datetime import UTC, datetime

    from active_boxes.httpsig import _sign_bytes_rsa
    from active_boxes.key import Ed25519Key

    netloc = urlparse(url).netloc
    target = delivery_target(url)
    date = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    accept = "application/activity+json, application/json"

    headers = {
        "Host": netloc,
        "Date": date,
        "Accept": accept,
        "User-Agent": user_agent,
    }

    sigheaders = "(request-target) host date accept"
    signed_string = f"(request-target): get {target}\nhost: {netloc}\ndate: {date}\naccept: {accept}"

    if isinstance(key, Ed25519Key):
        from active_boxes.httpsig import _sign_bytes_ed25519

        sig_bytes = _sign_bytes_ed25519(key.privkey, signed_string.encode("utf-8"))
        algorithm = "ed25519"
    else:
        sig_bytes = _sign_bytes_rsa(key.privkey, signed_string.encode("utf-8"))
        algorithm = "rsa-sha256"

    sig = base64.b64encode(sig_bytes).decode("utf-8")
    headers["Signature"] = f'keyId="{key.key_id()}",algorithm="{algorithm}",headers="{sigheaders}",signature="{sig}"'
    return headers
