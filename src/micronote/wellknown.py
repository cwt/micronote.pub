"""Well-known and instance metadata endpoints."""

import json
import mimetypes

from active_boxes.activitypub import ActivityType
from flask import Blueprint, Response, abort, current_app, request
from flask import jsonify as flask_jsonify

from micronote import activitypub
from micronote.boxes import Box
from micronote.config import (
    BASE_URL,
    CDN_URL,
    DB,
    DOMAIN,
    ICON_URL,
    ID,
    KEY,
    NAME,
    SUMMARY,
    THEME_COLOR,
    USERNAME,
    VERSION,
)
from micronote.web import page_cache

blueprint = Blueprint("wellknown", __name__, template_folder="templates")

NODEINFO_CONTENT_TYPE = "application/json; profile=http://nodeinfo.diaspora.software/ns/schema/2.0#"


ROBOTS_TXT = """User-agent: *
Disallow: /login
Disallow: /admin/
Disallow: /static/
Disallow: /media/
Disallow: /uploads/"""


@blueprint.route("/robots.txt")
def robots_txt():
    return Response(response=ROBOTS_TXT, headers={"Content-Type": "text/plain"})


@blueprint.route("/nodeinfo")
@page_cache(type_="api", content_type=NODEINFO_CONTENT_TYPE)
def nodeinfo():
    q = {
        "box": Box.OUTBOX.value,
        "meta.deleted": False,  # TODO(tsileo): retrieve deleted and expose tombstone
        "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
    }

    response = json.dumps(
        {
            "version": "2.0",
            "software": {
                "name": "micronote.pub",
                "version": f"micronote.pub {VERSION}",
            },
            "protocols": ["activitypub"],
            "services": {"inbound": [], "outbound": []},
            "openRegistrations": False,
            "usage": {"users": {"total": 1}, "localPosts": DB.activities.count_documents(q)},
            "metadata": {
                "sourceCode": "https://github.com/cwt/micronote.pub",
                "nodeName": f"@{USERNAME}@{DOMAIN}",
            },
        }
    )

    return Response(
        headers={"Content-Type": NODEINFO_CONTENT_TYPE},
        response=response,
    )


@blueprint.route("/manifest.json")
def pwa_manifest():
    """Web app manifest, branded per instance (replaces the old symlinked static manifests)."""
    return Response(
        headers={
            "Content-Type": "application/manifest+json",
            "Cache-Control": "public,max-age=86400",
        },
        response=activitypub.json_dumps(
            {
                "name": f"{NAME}'s micronote.pub",
                "short_name": USERNAME,
                "description": SUMMARY,
                "id": "/",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "background_color": "#eee",
                "theme_color": THEME_COLOR,
                "icons": [
                    {
                        "src": f"{CDN_URL}/static/pwa/icon-192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                    },
                    {
                        "src": f"{CDN_URL}/static/pwa/icon-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": f"{CDN_URL}/static/pwa/icon-maskable-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "maskable",
                    },
                ],
            }
        ),
    )


@blueprint.route("/.well-known/nodeinfo")
def wellknown_nodeinfo():
    return flask_jsonify(
        links=[
            {
                "rel": "http://nodeinfo.diaspora.software/ns/schema/2.0",
                "href": f"{ID}/nodeinfo",
            }
        ]
    )


@blueprint.route("/.well-known/webfinger")
def wellknown_webfinger():
    """Enable WebFinger support, required for Mastodon interopability."""
    # TODO(tsileo): move this to little-boxes?
    resource = request.args.get("resource")
    if resource not in [f"acct:{USERNAME}@{DOMAIN}", ID]:
        abort(404)

    out = {
        "subject": f"acct:{USERNAME}@{DOMAIN}",
        "aliases": [ID],
        "links": [
            {
                "rel": "http://webfinger.net/rel/profile-page",
                "type": "text/html",
                "href": BASE_URL,
            },
            {"rel": "self", "type": "application/activity+json", "href": ID},
            {
                "rel": "http://ostatus.org/schema/1.0/subscribe",
                "template": f"{BASE_URL}/authorize_follow?profile={{uri}}",
            },
            {"rel": "magic-public-key", "href": KEY.to_magic_key()},
            {
                "href": ICON_URL,
                "rel": "http://webfinger.net/rel/avatar",
                "type": mimetypes.guess_type(ICON_URL)[0],
            },
        ],
    }

    return Response(
        response=json.dumps(out),
        headers={
            "Content-Type": "application/jrd+json; charset=utf-8" if not current_app.debug else "application/json"
        },
    )
