"""Shared HTTP helpers and delivery decorators for the route blueprints."""

from functools import wraps

from flask import Response, abort, current_app, request, session

from micronote import activitypub, cache, config
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


def negotiate(*, html, activitypub):
    """Route to the HTML or ActivityPub handler based on the Accept header."""

    @wraps(html)
    def view(**kwargs):
        if is_api_request():
            return activitypub(**kwargs)
        return html(**kwargs)

    return view


def activitypub_only(view_func):
    """Reject browser/HTML GET requests with HTTP 404; leave POSTs alone."""

    @wraps(view_func)
    def view(**kwargs):
        if request.method in ("GET", "HEAD") and not is_api_request():
            abort(404)
        return view_func(**kwargs)

    return view


def page_cache(type_="html", content_type=None):
    """Cache anonymous responses, keyed by path and pagination args.

    HTML handlers return their cached string directly; responses with an
    explicit `content_type` are rewrapped so headers survive the cache.
    """

    def decorator(view_func):
        @wraps(view_func)
        def view(**kwargs):
            if session.get("logged_in"):
                return view_func(**kwargs)

            arg = f"{request.args.get('older_than', '')}:{request.args.get('newer_than', '')}"
            cached = cache.get_page(request.path, type_, arg)
            if cached is not None:
                if content_type is not None:
                    return Response(cached, headers={"Content-Type": content_type})
                return cached

            resp = view_func(**kwargs)
            data = resp.get_data(as_text=True) if isinstance(resp, Response) else resp
            cache.set_page(request.path, data, type_, arg)
            return resp

        return view

    return decorator
