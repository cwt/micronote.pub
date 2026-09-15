"""Fake remote ActivityPub node for local federation testing.

Serves one actor (plus key), a note, and fixed activities. Captures
inbound deliveries and strictly verifies their HTTP Signatures.
The peer under test fetches from here over plain HTTP, which
active-boxes allows in backend debug mode (MICRONOTE_DEBUG=1).

Run:  python scripts/stub_remote.py [--port 5006] [--peer http://localhost:5005]

The RSA key persists in scripts/.stub_key_<port>.pem so the driver
(scripts/ap_matrix.py) can sign with the same key across restarts.
"""
import argparse
import asyncio
import json
import logging
import os

from active_boxes import activitypub as ap
from active_boxes.backend import Backend
from active_boxes.httpsig import verify_request_sync
from active_boxes.key import Key
from flask import Flask, Response, jsonify, request

log = logging.getLogger(__name__)

# Populated by create_app(); declared here for type checkers.
STUB_BASE: str
ACTOR: str
KEY_ID: str
PEER: str
STUB_KEY: Key
RECEIVED: list[dict] = []


def key_file_for(port: int) -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), f".stub_key_{port}.pem"
    )


class StubBackend(Backend):
    def base_url(self) -> str:
        return STUB_BASE

    def activity_url(self, obj_id: str) -> str:
        return f"{STUB_BASE}/activity/{obj_id}"

    def note_url(self, obj_id: str) -> str:
        return f"{STUB_BASE}/note/{obj_id}"

    def debug_mode(self) -> bool:
        return True

    async def fetch_iri(self, iri, **kwargs):
        # Test scaffolding: plain fetch of known-localhost URLs. (The
        # peer under test goes through its own debug-aware path.)
        import requests as rq

        resp = await asyncio.to_thread(
            rq.get,
            iri,
            headers={"Accept": "application/activity+json, application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


def load_or_create_key(actor_id, port):
    key_file = key_file_for(port)
    key = Key(actor_id)
    if os.path.isfile(key_file):
        with open(key_file) as f:
            key.load(f.read())
    else:
        key.new()
        with open(key_file, "w") as f:
            os.chmod(key_file, 0o600)
            f.write(key.privkey_pem)
    return key


def person_doc():
    return {
        "type": "Person",
        "id": ACTOR,
        "inbox": f"{STUB_BASE}/inbox",
        "outbox": f"{STUB_BASE}/outbox",
        "followers": f"{STUB_BASE}/followers",
        "following": f"{STUB_BASE}/following",
        "preferredUsername": "stubbie",
        "name": "Stubbie Remote",
        "url": ACTOR,
        "publicKey": {
            "id": KEY_ID,
            "owner": ACTOR,
            "publicKeyPem": STUB_KEY.pubkey_pem,
        },
    }


def note_doc():
    return {
        "type": "Note",
        "id": f"{STUB_BASE}/note/1",
        "attributedTo": ACTOR,
        "content": "hello from the stub node",
        "to": [ap.AS_PUBLIC],
        "published": "2026-09-14T00:00:00Z",
    }


def create_doc():
    return {
        "type": "Create",
        "id": f"{STUB_BASE}/create/1",
        "actor": ACTOR,
        "object": note_doc(),
        "to": [ap.AS_PUBLIC],
        "published": "2026-09-14T00:00:00Z",
    }


def follow_doc():
    return {
        "type": "Follow",
        "id": f"{STUB_BASE}/follow/1",
        "actor": ACTOR,
        "object": PEER,
    }


def create_app(port, peer):
    global STUB_BASE, ACTOR, KEY_ID, PEER, STUB_KEY
    STUB_BASE = f"http://localhost:{port}"
    ACTOR = f"{STUB_BASE}/actor"
    KEY_ID = f"{ACTOR}#main-key"
    PEER = peer
    STUB_KEY = load_or_create_key(ACTOR, port)
    ap.use_backend(StubBackend())

    store = {
        f"{STUB_BASE}/note/1": note_doc(),
        f"{STUB_BASE}/create/1": create_doc(),
        f"{STUB_BASE}/follow/1": follow_doc(),
    }

    app = Flask(__name__)

    @app.route("/actor")
    def actor():
        return jsonify(person_doc())

    @app.route("/<kind>/<name>")
    def serve(kind, name):
        doc = store.get(f"{STUB_BASE}/{kind}/{name}")
        if doc is None:
            return jsonify(error="not found"), 404
        return jsonify(doc)

    @app.route("/stage", methods=["POST"])
    def stage():
        doc = request.get_json(force=True)
        store[doc["id"]] = doc
        return jsonify(stored=doc["id"]), 201

    @app.route("/inbox", methods=["POST"])
    def inbox():
        body = request.get_data(as_text=True)
        headers = {key.lower(): value for key, value in request.headers.items()}
        try:
            verified = verify_request_sync("POST", "/inbox", headers, body)
        except Exception:
            log.exception("stub verification blew up")
            verified = False
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        RECEIVED.append({
            "headers": {key: value for key, value in headers.items() if key != "authorization"},
            "body": payload,
            "verified": verified,
        })
        log.info(f"stub inbox got {payload.get('type') if payload else None} verified={verified}")
        return Response(status=202)

    @app.route("/received")
    def received():
        return jsonify(RECEIVED)

    @app.route("/reset", methods=["POST"])
    def reset():
        del RECEIVED[:]
        return jsonify(count=0)

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5006)
    parser.add_argument("--peer", default="http://localhost:5005")
    args = parser.parse_args()
    create_app(args.port, args.peer).run(port=args.port, threaded=True)


if __name__ == "__main__":
    main()
