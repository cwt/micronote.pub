"""Local federation matrix driver (first slice of the full harness).

Drives scripts/stub_remote.py fixtures through the peer's /inbox and
asserts DB side effects plus strict signature verification of the
peer's deliveries back to the stub. No real fediverse involved.

Peer requirements: MICRONOTE_DEBUG=1 (lets active-boxes fetch the
http://localhost stub) and either MICRONOTE_TASK_EAGER=1 or a
running worker.py (the driver polls for async completion).

Run:  python scripts/ap_matrix.py [follow|create|like|unsigned-follow|all]
      [--peer http://localhost:5005] [--stub http://localhost:5006]
"""
import argparse
import json
import os
import sys
import time

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from active_boxes.key import Key  # noqa: E402

from config import DB  # noqa: E402
from utils.delivery import sign_delivery_request  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not condition:
        FAILURES.append(name)


def stub_key(stub_base):
    port = stub_base.rsplit(":", 1)[1]
    key_file = os.path.join(REPO_ROOT, "scripts", f".stub_key_{port}.pem")
    actor = stub_base + "/actor"
    key = Key(actor)
    with open(key_file) as f:
        key.load(f.read())
    return key


def signed_post(url, payload, key):
    body = json.dumps(payload)
    headers = sign_delivery_request(url, body, key, "stub-remote/0.1")
    return requests.post(url, data=body, headers=headers, timeout=15)


def stub_received(stub_base):
    return requests.get(stub_base + "/received", timeout=15).json()


def poll_received(stub_base, match, timeout=30):
    """Polls the stub until match(entry) or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for entry in stub_received(stub_base):
            if match(entry):
                return entry
        time.sleep(1)
    return None


def is_accept_for(entry, follow_id):
    body = entry.get("body") or {}
    if body.get("type") != "Accept":
        return False
    obj = body.get("object") or {}
    return obj.get("id") == follow_id


def reset_stub(stub_base):
    requests.post(stub_base + "/reset", timeout=15).raise_for_status()


def unique_id(stub_base, kind):
    return f"{stub_base}/{kind}/{int(time.time() * 1000)}"


def stage(stub_base, doc):
    """Registers doc on the stub so fetch-fallback can dereference it."""
    resp = requests.post(stub_base + "/stage", json=doc, timeout=15)
    resp.raise_for_status()
    return doc


def admin_key():
    with open(os.path.join(REPO_ROOT, "config", "admin_api_key.key")) as f:
        return f.read().strip()


def active_stub_follows(stub_actor):
    return list(DB.activities.find({
        "box": "inbox", "type": "Follow", "meta.undo": False,
        "activity.actor": stub_actor,
    }))


def undo_active_follows(peer, stub):
    """Cleanup preamble: Undo every active stub follow (unsigned, via fetch fallback).

    Makes follow cases repeatable regardless of previous runs, and proves
    re-follow after Undo still goes through.
    """
    for doc in active_stub_follows(stub + "/actor"):
        undo = {
            "type": "Undo",
            "id": unique_id(stub, "undo"),
            "actor": stub + "/actor",
            "object": doc["activity"],
        }
        stage(stub, undo)
        resp = requests.post(peer + "/inbox", json=undo, timeout=15)
        check("cleanup undo accepted", resp.status_code == 201, f"HTTP {resp.status_code}")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not active_stub_follows(stub + "/actor"):
            break
        time.sleep(1)
    check("no active stub follows remain", not active_stub_follows(stub + "/actor"))


def count_accepts_for(stub_base, follow_id):
    return len([e for e in stub_received(stub_base) if is_accept_for(e, follow_id)])


def case_follow(peer, stub, key):
    print("--- follow: stub follows peer, expect auto-Accept ---")
    undo_active_follows(peer, stub)
    reset_stub(stub)
    follow = requests.get(stub + "/follow/1", timeout=15).json()
    follow["id"] = unique_id(stub, "follow")
    stage(stub, follow)
    resp = signed_post(peer + "/inbox", follow, key)
    check("follow accepted", resp.status_code == 201, f"HTTP {resp.status_code}")
    entry = poll_received(stub, match=lambda e: is_accept_for(e, follow["id"]))
    check("Accept delivered", entry is not None)
    if entry:
        check("Accept signature verifies", entry["verified"] is True)
    doc = DB.activities.find_one({"box": "inbox", "remote_id": follow["id"]})
    check("Follow stored", doc is not None)
    check("stub is follower", len(active_stub_follows(stub + "/actor")) == 1)

    print("--- re-follow same actor with new id, expect no duplicate ---")
    follow2 = requests.get(stub + "/follow/1", timeout=15).json()
    follow2["id"] = unique_id(stub, "follow")
    stage(stub, follow2)
    resp = signed_post(peer + "/inbox", follow2, key)
    check("re-follow accepted", resp.status_code == 201, f"HTTP {resp.status_code}")
    time.sleep(8)  # absence needs a window; eager mode settles instantly
    check("no second Accept", count_accepts_for(stub, follow2["id"]) == 0)
    check("still one follower row", len(active_stub_follows(stub + "/actor")) == 1)


def case_create(peer, stub, key):
    print("--- create: stub posts note, expect stream flag ---")
    create = requests.get(stub + "/create/1", timeout=15).json()
    create["id"] = unique_id(stub, "create")
    create["object"]["id"] = unique_id(stub, "note")
    stage(stub, create)
    before = len(stub_received(stub))
    resp = signed_post(peer + "/inbox", create, key)
    check("create accepted", resp.status_code == 201, f"HTTP {resp.status_code}")
    doc = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        doc = DB.activities.find_one({"remote_id": create["id"]})
        if doc and "stream" in doc.get("meta", {}):
            break
        time.sleep(1)
    check("Create stored", doc is not None)
    if doc:
        check("stream flagged", doc["meta"].get("stream") is True or doc["meta"].get("stream") == 1)
    time.sleep(3)
    check("no outbound delivery", len(stub_received(stub)) == before)


def case_like(peer, stub, key):
    print("--- like: stub likes local note, expect counter ---")
    note_resp = requests.post(
        peer + "/api/new_note",
        headers={"Authorization": "Bearer " + admin_key()},
        data={"content": "matrix like target"},
        timeout=15,
    )
    check("note posted", note_resp.status_code == 201, f"HTTP {note_resp.status_code}")
    activity_id = note_resp.json()["activity"]
    note_id = activity_id + "/activity"
    like = {
        "type": "Like",
        "id": unique_id(stub, "like"),
        "actor": stub + "/actor",
        "object": note_id,
    }
    stage(stub, like)
    resp = signed_post(peer + "/inbox", like, key)
    check("like accepted", resp.status_code == 201, f"HTTP {resp.status_code}")
    doc = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        doc = DB.activities.find_one({"activity.object.id": note_id})
        if doc and doc.get("meta", {}).get("count_like"):
            break
        time.sleep(1)
    check("like counted", doc is not None and doc["meta"].get("count_like") == 1)


def case_unsigned_follow(peer, stub, key):
    print("--- unsigned-follow: no signature, expect fetch fallback ---")
    undo_active_follows(peer, stub)
    reset_stub(stub)
    follow = requests.get(stub + "/follow/1", timeout=15).json()
    follow["id"] = unique_id(stub, "follow")
    stage(stub, follow)
    resp = requests.post(peer + "/inbox", json=follow, timeout=15)
    check("fallback accepted", resp.status_code == 201, f"HTTP {resp.status_code}")
    entry = poll_received(stub, match=lambda e: is_accept_for(e, follow["id"]))
    check("Accept delivered", entry is not None)
    if entry:
        check("Accept signature verifies", entry["verified"] is True)
    check("stub is follower", len(active_stub_follows(stub + "/actor")) == 1)


CASES = {
    "follow": case_follow,
    "create": case_create,
    "like": case_like,
    "unsigned-follow": case_unsigned_follow,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", nargs="?", default="all", choices=list(CASES) + ["all"])
    parser.add_argument("--peer", default="http://localhost:5005")
    parser.add_argument("--stub", default="http://localhost:5006")
    args = parser.parse_args()

    print(f"peer={args.peer} stub={args.stub} (peer needs MICRONOTE_DEBUG=1)")
    key = stub_key(args.stub)
    names = list(CASES) if args.case == "all" else [args.case]
    for name in names:
        CASES[name](args.peer, args.stub, key)

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES: {FAILURES}")
        sys.exit(1)
    print("\nall matrix cases passed")


if __name__ == "__main__":
    main()
