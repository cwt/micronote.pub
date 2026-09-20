"""Frozen URL map: refactors must not add, remove, or change routes.

Endpoint names are intentionally excluded from the snapshot: they change when
routes move into blueprints (improvement plan Phase 3), while the public URL
surface must stay identical.
"""

from micronote.app import app

EXPECTED_ROUTES = [
    ("/", ("GET",)),
    ("/.well-known/nodeinfo", ("GET",)),
    ("/.well-known/webfinger", ("GET",)),
    ("/admin", ("GET",)),
    ("/admin/logout", ("GET",)),
    ("/admin/lookup", ("GET", "POST")),
    ("/admin/new", ("GET",)),
    ("/admin/notifications", ("GET",)),
    ("/admin/stream", ("GET",)),
    ("/admin/thread", ("GET",)),
    ("/api/block", ("POST",)),
    ("/api/boost", ("POST",)),
    ("/api/debug", ("DELETE", "GET")),
    ("/api/follow", ("POST",)),
    ("/api/key", ("GET",)),
    ("/api/like", ("POST",)),
    ("/api/new_note", ("POST",)),
    ("/api/note/delete", ("POST",)),
    ("/api/note/pin", ("POST",)),
    ("/api/note/unpin", ("POST",)),
    ("/api/stream", ("GET",)),
    ("/api/undo", ("POST",)),
    ("/authorize_follow", ("GET", "POST")),
    ("/drop_cache", ("POST",)),
    ("/favicon.ico", ("GET",)),
    ("/featured", ("GET",)),
    ("/feed.atom", ("GET",)),
    ("/feed.json", ("GET",)),
    ("/feed.rss", ("GET",)),
    ("/followers", ("GET",)),
    ("/following", ("GET",)),
    ("/inbox", ("GET", "POST")),
    ("/indieauth", ("GET", "POST")),
    ("/indieauth/flow", ("POST",)),
    ("/liked", ("GET",)),
    ("/login", ("GET", "POST")),
    ("/manifest.json", ("GET",)),
    ("/media/<media_id>", ("GET",)),
    ("/nodeinfo", ("GET",)),
    ("/note/<note_id>", ("GET",)),
    ("/outbox", ("GET", "POST")),
    ("/outbox/<item_id>", ("GET",)),
    ("/outbox/<item_id>/activity", ("GET",)),
    ("/outbox/<item_id>/likes", ("GET",)),
    ("/outbox/<item_id>/replies", ("GET",)),
    ("/outbox/<item_id>/shares", ("GET",)),
    ("/remote_follow", ("GET", "POST")),
    ("/robots.txt", ("GET",)),
    ("/static/<path:filename>", ("GET",)),
    ("/tags/<tag>", ("GET",)),
    ("/token", ("GET", "POST")),
    ("/uploads/<oid>/<fname>", ("GET",)),
    ("/webauthn/register", ("GET", "POST")),
    ("/with_replies", ("GET",)),
]


def _route_pairs():
    return sorted((rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))) for rule in app.url_map.iter_rules())


def test_url_map_is_frozen():
    assert _route_pairs() == EXPECTED_ROUTES
