from unittest.mock import MagicMock, patch

from flask import Flask

from micronote.threads import build_thread


def test_build_thread_handles_self_referencing_reply():
    app = Flask(__name__)

    root_doc = {
        "_id": "1",
        "type": "Create",
        "meta": {"thread_root_parent": "https://example.com/note/root"},
        "activity": {
            "object": {
                "id": "https://example.com/note/root",
                "type": "Note",
                "published": "2026-09-20T00:00:00Z",
            }
        },
    }

    # Reply that self-references (inReplyTo points to its own id)
    self_ref_reply = {
        "_id": "2",
        "type": "Create",
        "meta": {"thread_root_parent": "https://example.com/note/root"},
        "activity": {
            "object": {
                "id": "https://example.com/note/reply-self",
                "inReplyTo": "https://example.com/note/reply-self",
                "type": "Note",
                "published": "2026-09-20T00:01:00Z",
            }
        },
    }

    mock_db = MagicMock()
    mock_db.activities.find.return_value = [self_ref_reply]

    with app.app_context(), patch("micronote.threads.DB", mock_db):
        thread = build_thread(root_doc)

    assert len(thread) >= 1
    assert thread[0]["activity"]["object"]["id"] == "https://example.com/note/root"


def test_build_thread_handles_cyclical_replies():
    app = Flask(__name__)

    root_doc = {
        "_id": "1",
        "type": "Create",
        "meta": {"thread_root_parent": "https://example.com/note/root"},
        "activity": {
            "object": {
                "id": "https://example.com/note/root",
                "type": "Note",
                "published": "2026-09-20T00:00:00Z",
            }
        },
    }

    # Cycle: reply-a replies to root, reply-b replies to reply-a, reply-a also replies to reply-b
    reply_a = {
        "_id": "2",
        "type": "Create",
        "meta": {"thread_root_parent": "https://example.com/note/root"},
        "activity": {
            "object": {
                "id": "https://example.com/note/reply-a",
                "inReplyTo": "https://example.com/note/root",
                "type": "Note",
                "published": "2026-09-20T00:01:00Z",
            }
        },
    }

    reply_b = {
        "_id": "3",
        "type": "Create",
        "meta": {"thread_root_parent": "https://example.com/note/root"},
        "activity": {
            "object": {
                "id": "https://example.com/note/reply-b",
                "inReplyTo": "https://example.com/note/reply-a",
                "type": "Note",
                "published": "2026-09-20T00:02:00Z",
            }
        },
    }

    mock_db = MagicMock()
    mock_db.activities.find.return_value = [reply_a, reply_b]

    with app.app_context(), patch("micronote.threads.DB", mock_db):
        thread = build_thread(root_doc)

    ids = [n["activity"]["object"]["id"] for n in thread]
    assert "https://example.com/note/root" in ids
    assert "https://example.com/note/reply-a" in ids
    assert "https://example.com/note/reply-b" in ids
