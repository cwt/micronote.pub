"""Shared HTTP helpers for the app factory and the route blueprints."""

from flask import Response, current_app, request

from micronote import activitypub, config
from micronote.config import HEADERS


def activity_json(**data):
    if "@context" not in data:
        data["@context"] = config.DEFAULT_CTX
    return Response(
        response=activitypub.json_dumps(data),
        headers={"Content-Type": "application/json" if current_app.debug else "application/activity+json"},
    )


def is_api_request() -> bool:
    h = request.headers.get("Accept")
    if h is None:
        return False
    media_type = h.split(",")[0]
    return media_type in HEADERS or media_type == "application/json"


def wants_html() -> bool:
    """True when the client sent a browser-like Accept header."""
    return "text/html" in request.headers.get("Accept", "")
