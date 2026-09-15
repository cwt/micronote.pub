#!/usr/bin/env python3
"""Render every HTML page with seeded fixture data for validation.

Writes the pages as standalone files so they can be checked with an
HTML5 validator (see ``make lint-web``). The SQLite database lives in
a throwaway temp directory, so the developer's ``data/`` is never
touched, and the active config (``config/me.yml`` or
``MICRONOTE_CONFIG_DIR``) is used as-is.
"""

import argparse
import atexit
import logging
import os
import shutil
import sys
import tempfile
from typing import Any

DATA_DIR = tempfile.mkdtemp(prefix="micronote-html-")
os.environ["MICRONOTE_DATA_DIR"] = DATA_DIR
atexit.register(shutil.rmtree, DATA_DIR, ignore_errors=True)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from flask import render_template  # noqa: E402

from micronote.app import app  # noqa: E402
from micronote.config import BASE_URL, DB  # noqa: E402

# Fixture pages intentionally miss media cache entries and remote
# actors; the resulting log noise would bury the render summary.
logging.disable(logging.CRITICAL)

NOW = "2026-09-15T00:00:00Z"

ACTOR: dict[str, Any] = {
    "id": "https://remote.example/users/bob",
    "url": "https://remote.example/@bob",
    "name": "Bob Remote",
    "preferredUsername": "bob",
    "icon": {"url": "https://remote.example/avatar.png"},
    "type": "Person",
}
ACTOR_META: dict[str, Any] = {
    **ACTOR,
    "inbox": "https://remote.example/users/bob/inbox",
    "sharedInbox": "",
}
SELF_META: dict[str, Any] = {
    "id": BASE_URL,
    "url": BASE_URL,
    "name": "Fixture User",
    "preferredUsername": "fixture",
    "icon": {"url": f"{BASE_URL}/static/nopic.png"},
    "inbox": f"{BASE_URL}/inbox",
    "sharedInbox": f"{BASE_URL}/inbox",
}

NOTE_ID = "abc123"
OBJECT_ID = f"{BASE_URL}/outbox/{NOTE_ID}/activity"
NOTE: dict[str, Any] = {
    "type": "Note",
    "id": OBJECT_ID,
    "attributedTo": BASE_URL,
    "url": f"{BASE_URL}/note/{NOTE_ID}",
    "content": '<p>Hello <a href="https://remote.example/page">world</a> 😀</p>',
    "published": NOW,
    "tag": [{"type": "Hashtag", "name": "#neuler", "href": f"{BASE_URL}/tags/neuler"}],
    "attachment": [
        {
            "type": "Document",
            "mediaType": "image/png",
            "url": "https://remote.example/img.png",
            "name": "img.png",
        },
        {
            "type": "Document",
            "mediaType": "text/plain",
            "url": "https://remote.example/f.txt",
            "name": "f.txt",
        },
    ],
}
NOTE_META: dict[str, Any] = {
    "undo": False,
    "deleted": False,
    "count_reply": 1,
    "count_like": 1,
    "count_boost": 1,
    "liked": f"{BASE_URL}/outbox/like1",
    "boosted": f"{BASE_URL}/outbox/ann1",
    "og_metadata": [
        {
            "url": "https://remote.example/page",
            "image": "https://remote.example/og.png",
            "title": "Remote page",
            "description": "An example page for OG rendering.",
            "site_name": "remote.example",
        }
    ],
}

REMOTE_NOTE: dict[str, Any] = {
    "type": "Note",
    "id": "https://remote.example/notes/1/activity",
    "attributedTo": ACTOR["id"],
    "url": "https://remote.example/notes/1",
    "content": "<p>A remote note.</p>",
    "published": NOW,
}

OUTBOX_NOTE: dict[str, Any] = {
    "box": "outbox",
    "type": ["Create"],
    "remote_id": f"{BASE_URL}/outbox/{NOTE_ID}",
    "activity": {
        "@context": "x",
        "type": "Create",
        "id": f"{BASE_URL}/outbox/{NOTE_ID}",
        "actor": BASE_URL,
        "object": NOTE,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
    },
    "meta": dict(NOTE_META),
}
OUTBOX_REPLY: dict[str, Any] = {
    "box": "outbox",
    "type": ["Create"],
    "remote_id": f"{BASE_URL}/outbox/reply1",
    "activity": {
        "@context": "x",
        "type": "Create",
        "id": f"{BASE_URL}/outbox/reply1",
        "actor": BASE_URL,
        "object": {
            "type": "Note",
            "id": f"{BASE_URL}/outbox/reply1/activity",
            "attributedTo": BASE_URL,
            "url": f"{BASE_URL}/note/reply1",
            "content": "<p>And a reply.</p>",
            "published": NOW,
            "inReplyTo": OBJECT_ID,
        },
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
    },
    "meta": {"undo": False, "deleted": False, "thread_root_parent": OBJECT_ID},
}
OUTBOX_IMAGE_NOTE: dict[str, Any] = {
    "box": "outbox",
    "type": ["Create"],
    "remote_id": f"{BASE_URL}/outbox/imgonly1",
    "activity": {
        "@context": "x",
        "type": "Create",
        "id": f"{BASE_URL}/outbox/imgonly1",
        "actor": BASE_URL,
        "object": {
            "type": "Note",
            "id": f"{BASE_URL}/outbox/imgonly1/activity",
            "attributedTo": BASE_URL,
            "url": f"{BASE_URL}/note/imgonly1",
            "content": "<p>Image only.</p>",
            "published": NOW,
            "attachment": [
                {
                    "type": "Document",
                    "mediaType": "image/png",
                    "url": "https://remote.example/only.png",
                    "name": "only.png",
                }
            ],
        },
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
    },
    "meta": {"undo": False, "deleted": False},
}
OUTBOX_ANNOUNCE: dict[str, Any] = {
    "box": "outbox",
    "type": ["Announce"],
    "remote_id": f"{BASE_URL}/outbox/ann1",
    "activity": {
        "@context": "x",
        "type": "Announce",
        "id": f"{BASE_URL}/outbox/ann1",
        "actor": BASE_URL,
        "object": REMOTE_NOTE["id"],
    },
    "meta": {
        "undo": False,
        "deleted": False,
        "actor": SELF_META,
        "object": REMOTE_NOTE,
        "object_actor": ACTOR_META,
    },
}
OUTBOX_FOLLOW: dict[str, Any] = {
    "box": "outbox",
    "type": ["Follow"],
    "remote_id": f"{BASE_URL}/outbox/follow1",
    "activity": {
        "@context": "x",
        "type": "Follow",
        "id": f"{BASE_URL}/outbox/follow1",
        "actor": BASE_URL,
        "object": ACTOR["id"],
    },
    "meta": {"undo": False, "deleted": False, "object": ACTOR_META},
}
OUTBOX_LIKE: dict[str, Any] = {
    "box": "outbox",
    "type": ["Like"],
    "remote_id": f"{BASE_URL}/outbox/like1",
    "activity": {
        "@context": "x",
        "type": "Like",
        "id": f"{BASE_URL}/outbox/like1",
        "actor": BASE_URL,
        "object": REMOTE_NOTE["id"],
    },
    "meta": {
        "undo": False,
        "deleted": False,
        "actor": SELF_META,
        "object": REMOTE_NOTE,
        "object_actor": ACTOR_META,
    },
}
INBOX_CREATE: dict[str, Any] = {
    "box": "inbox",
    "type": ["Create"],
    "remote_id": "https://remote.example/notes/1",
    "activity": {
        "@context": "x",
        "type": "Create",
        "id": "https://remote.example/notes/1",
        "actor": ACTOR["id"],
        "object": REMOTE_NOTE,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
    },
    "meta": {
        "undo": False,
        "deleted": False,
        "stream": True,
        "actor": ACTOR_META,
        "object": REMOTE_NOTE,
        "object_actor": ACTOR_META,
    },
}
INBOX_FOLLOW: dict[str, Any] = {
    "box": "inbox",
    "type": ["Follow"],
    "remote_id": "https://remote.example/follows/1",
    "activity": {
        "@context": "x",
        "type": "Follow",
        "id": "https://remote.example/follows/1",
        "actor": ACTOR["id"],
        "object": BASE_URL,
    },
    "meta": {"undo": False, "deleted": False, "actor": ACTOR_META},
}
INBOX_LIKE: dict[str, Any] = {
    "box": "inbox",
    "type": ["Like"],
    "remote_id": "https://remote.example/likes/1",
    "activity": {
        "@context": "x",
        "type": "Like",
        "id": "https://remote.example/likes/1",
        "actor": ACTOR["id"],
        "object": OBJECT_ID,
    },
    "meta": {
        "undo": False,
        "deleted": False,
        "actor": ACTOR_META,
        "object": NOTE,
        "object_actor": SELF_META,
    },
}
INBOX_ANNOUNCE: dict[str, Any] = {
    "box": "inbox",
    "type": ["Announce"],
    "remote_id": "https://remote.example/announces/1",
    "activity": {
        "@context": "x",
        "type": "Announce",
        "id": "https://remote.example/announces/1",
        "actor": ACTOR["id"],
        "object": REMOTE_NOTE["id"],
    },
    "meta": {
        "undo": False,
        "deleted": False,
        "actor": ACTOR_META,
        "object": REMOTE_NOTE,
        "object_actor": ACTOR_META,
    },
}
INBOX_ACCEPT: dict[str, Any] = {
    "box": "inbox",
    "type": ["Accept"],
    "remote_id": "https://remote.example/accepts/1",
    "activity": {
        "@context": "x",
        "type": "Accept",
        "id": "https://remote.example/accepts/1",
        "actor": ACTOR["id"],
        "object": INBOX_FOLLOW["activity"],
    },
    "meta": {"undo": False, "deleted": False, "actor": ACTOR_META},
}
INBOX_UNDO: dict[str, Any] = {
    "box": "inbox",
    "type": ["Undo"],
    "remote_id": "https://remote.example/undos/1",
    "activity": {
        "@context": "x",
        "type": "Undo",
        "id": "https://remote.example/undos/1",
        "actor": ACTOR["id"],
        "object": INBOX_LIKE["activity"],
    },
    "meta": {"undo": False, "deleted": False, "actor": ACTOR_META},
}

FIXTURES = [
    OUTBOX_NOTE,
    OUTBOX_REPLY,
    OUTBOX_IMAGE_NOTE,
    OUTBOX_ANNOUNCE,
    OUTBOX_FOLLOW,
    OUTBOX_LIKE,
    INBOX_CREATE,
    INBOX_FOLLOW,
    INBOX_LIKE,
    INBOX_ANNOUNCE,
    INBOX_ACCEPT,
    INBOX_UNDO,
]


def seed() -> str:
    """Resets the temp DB and inserts the fixtures; returns a page cursor."""
    DB.activities.delete_many({})
    DB.cache2.delete_many({})
    for doc in FIXTURES:
        DB.activities.insert_one(dict(doc))
    first = DB.activities.find_one({"remote_id": OUTBOX_NOTE["remote_id"]})
    return str(first["_id"])


def render_pages(out_dir: str) -> list[str]:
    """Renders every page; returns the names of the unexpected responses."""
    older = seed()
    logged_in = app.test_client()
    with logged_in.session_transaction() as session:
        session["logged_in"] = True
    anonymous = app.test_client()

    pages = {
        "index": (logged_in, "/"),
        "index_paged": (logged_in, f"/?older_than={older}"),
        "with_replies": (logged_in, "/with_replies"),
        "note": (logged_in, f"/note/{NOTE_ID}"),
        "note_images_only": (logged_in, "/note/imgonly1"),
        "followers": (logged_in, "/followers"),
        "following": (logged_in, "/following"),
        "liked": (logged_in, "/liked"),
        "tags": (logged_in, "/tags/neuler"),
        "admin": (logged_in, "/admin"),
        "admin_new": (logged_in, "/admin/new"),
        "admin_new_reply": (logged_in, f"/admin/new?reply={OBJECT_ID}"),
        "admin_notifications": (logged_in, "/admin/notifications"),
        "admin_stream": (logged_in, "/admin/stream"),
        "admin_stream_debug": (logged_in, "/admin/stream?debug=1"),
        "admin_stream_debug_inbox": (logged_in, "/admin/stream?debug=1&debug_inbox=1"),
        "admin_lookup": (logged_in, "/admin/lookup"),
        "admin_thread": (logged_in, f"/admin/thread?oid={OBJECT_ID}"),
        "admin_thread_debug": (logged_in, f"/admin/thread?oid={OBJECT_ID}&debug=1"),
        "remote_follow": (anonymous, "/remote_follow"),
        "authorize_follow": (
            logged_in,
            "/authorize_follow?profile=@bob@remote.example",
        ),
        "webauthn_register": (logged_in, "/webauthn/register"),
        "indieauth": (
            logged_in,
            "/indieauth?me=x&redirect_uri=/&state=s&response_type=id&scope=read",
        ),
        "anon_index": (anonymous, "/"),
        "anon_note": (anonymous, f"/note/{NOTE_ID}"),
        "anon_login": (anonymous, "/login"),
        "anon_remote_follow": (anonymous, "/remote_follow"),
    }

    failures = []
    for name, (client, path) in pages.items():
        resp = client.get(path)
        status = resp.status_code
        with open(os.path.join(out_dir, f"{name}.html"), "w") as f:
            f.write(resp.get_data(as_text=True))
        print(f"{name:26s} {path:60s} {status}")
        if status != 200:
            failures.append(f"{name}: {path} -> {status}")

    with app.test_request_context("/"):
        extras = {
            "500": render_template("500.html"),
            "error": render_template("error.html", message="missing content"),
        }
    for name, body in extras.items():
        with open(os.path.join(out_dir, f"{name}.html"), "w") as f:
            f.write(body)
        print(f"{name:26s} {'(direct render)':60s} 200")

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".html-render", help="output directory")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    failures = render_pages(args.out)
    if failures:
        print("\nunexpected responses:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
