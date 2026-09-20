"""Feed endpoints (RSS/Atom/JSON) and their builders."""

from typing import Any

import flask
from feedgen.feed import FeedGenerator
from flask import Response, abort
from html2text import html2text
from neosqlite.objectid import ObjectId

from micronote import activitypub
from micronote.boxes import Box
from micronote.config import DB, ID, ME, USERNAME

blueprint = flask.Blueprint("feeds", __name__, template_folder="templates")


def gen_feed():
    fg = FeedGenerator()
    fg.id(f"{ID}")
    fg.title(f"{USERNAME} notes")
    fg.author({"name": USERNAME, "email": "t@a4.io"})
    fg.link(href=ID, rel="alternate")
    fg.description(f"{USERNAME} notes")
    fg.logo(ME.get("icon", {}).get("url"))
    fg.language("en")
    for item in DB.activities.find({"box": Box.OUTBOX.value, "type": "Create", "meta.deleted": False}, limit=10).sort(
        "_id", -1
    ):
        fe = fg.add_entry()
        fe.id(item["activity"]["object"].get("url"))
        fe.link(href=item["activity"]["object"].get("url"))
        fe.title(item["activity"]["object"]["content"])
        fe.description(item["activity"]["object"]["content"])
    return fg


def _feed_item(item: dict[str, Any], author: dict[str, Any] | None = None) -> dict[str, Any]:
    """One JSON Feed entry; `author` is only set for inbox activities."""
    note = item["activity"]["object"]
    entry = {
        "id": item["activity"]["id"],
        "url": note.get("url"),
        "content_html": note["content"],
        "content_text": html2text(note["content"]),
        "date_published": note.get("published"),
    }
    if author is not None:
        entry["author"] = author
    return entry


def build_json_feed(path: str) -> dict[str, Any]:
    """JSON Feed (https://jsonfeed.org/) document."""
    items = DB.activities.find({"box": Box.OUTBOX.value, "type": "Create", "meta.deleted": False}, limit=10).sort(
        "_id", -1
    )
    data = [_feed_item(item) for item in items]
    return {
        "version": "https://jsonfeed.org/version/1",
        "user_comment": (
            f"This is a micronote.pub feed. You can add this to your feed reader using the following URL: {ID}{path}"
        ),
        "title": USERNAME,
        "home_page_url": ID,
        "feed_url": f"{ID}{path}",
        "author": {
            "name": USERNAME,
            "url": ID,
            "avatar": ME.get("icon", {}).get("url"),
        },
        "items": data,
    }


def build_inbox_json_feed(path: str, request_cursor: str | None = None) -> dict[str, Any]:
    """Build a JSON feed from the inbox activities."""
    q: dict[str, Any] = {
        "type": "Create",
        "meta.deleted": False,
        "box": Box.INBOX.value,
    }
    if request_cursor:
        try:
            q["_id"] = {"$lt": ObjectId(request_cursor)}
        except Exception:
            abort(400)

    items = list(DB.activities.find(q, limit=50).sort("_id", -1))

    missing_iris = {
        item.get("activity", {}).get("actor")
        for item in items
        if not item.get("meta", {}).get("actor") and item.get("activity", {}).get("actor")
    }
    cached_actors: dict[str, dict[str, Any]] = {}
    if missing_iris:
        for actor_doc in DB.actors.find({"remote_id": {"$in": list(missing_iris)}}):
            remote_id = actor_doc.get("remote_id")
            if remote_id and actor_doc.get("data"):
                cached_actors[remote_id] = actor_doc["data"]

    data = []
    for item in items:
        activity = item.get("activity", {})
        actor_iri = activity.get("actor")
        meta_actor = item.get("meta", {}).get("actor")
        if not meta_actor and actor_iri:
            meta_actor = cached_actors.get(actor_iri, {})

        if not isinstance(meta_actor, dict):
            meta_actor = {}

        name = meta_actor.get("name") or meta_actor.get("preferredUsername") or actor_iri or ""
        url = meta_actor.get("url") or actor_iri or ""
        icon = meta_actor.get("icon")
        avatar = icon.get("url") if isinstance(icon, dict) else None

        author_info = {
            "name": name,
            "url": url,
            "avatar": avatar,
        }
        data.append(_feed_item(item, author=author_info))
    cursor = str(items[-1]["_id"]) if items else None
    resp = {
        "version": "https://jsonfeed.org/version/1",
        "title": f"{USERNAME}'s stream",
        "home_page_url": ID,
        "feed_url": f"{ID}{path}",
        "items": data,
    }
    if cursor and len(data) == 50:
        resp["next_url"] = f"{ID}{path}?cursor={cursor}"

    return resp


@blueprint.route("/feed.json")
def json_feed():
    return Response(
        response=activitypub.json_dumps(build_json_feed("/feed.json")),
        headers={"Content-Type": "application/json"},
    )


@blueprint.route("/feed.atom")
def atom_feed():
    return Response(
        response=gen_feed().atom_str(),
        headers={"Content-Type": "application/atom+xml"},
    )


@blueprint.route("/feed.rss")
def rss_feed():
    return Response(
        response=gen_feed().rss_str(),
        headers={"Content-Type": "application/rss+xml"},
    )
