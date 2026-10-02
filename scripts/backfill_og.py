#!/usr/bin/env python
"""One-off backfill: re-enqueue fetch_og_metadata for notes missing Open Graph metadata.

Links that are consent-gated (e.g. YouTube) or previously failed leave
``meta.og_metadata`` empty on the stored activity. Re-running the fetch job
regenerates the card. The job is idempotent and bounded by MAX_RETRIES, so
this is safe to re-run.

By default this targets notes that actually contain a YouTube link — the set
the deterministic-card fix (BUG-025) now handles. Use ``--all`` to re-enqueue
every Create activity with empty ``og_metadata`` (a much larger, mostly
no-op burst). Use ``--links`` to target any note containing an external link.

Run it inside the app container (shares the live DB) by piping the file over
stdin so no extra deploy is needed::

    podman exec -i micronote-worker python - --dry-run < scripts/backfill_og.py   # preview
    podman exec -i micronote-worker python - < scripts/backfill_og.py            # enqueue
"""
import json
import sys

from micronote.config import DB
from micronote.jobs import enqueue_job

argv = sys.argv[1:]
DRY_RUN = "--dry-run" in argv
SCOPE_ALL = "--all" in argv
SCOPE_LINKS = "--links" in argv


def _blob(doc: dict) -> str:
    return json.dumps(doc.get("activity") or {})


def _in_scope(doc: dict, blob: str) -> bool:
    """Pick the note-specific scopes; --all is handled by the caller."""
    if SCOPE_LINKS:
        # Anchors appear JSON-escaped as href=\"http in the dumped activity.
        return '<a href=\\"http' in blob or "<a href='http" in blob
    # Default: only notes that reference YouTube (the bug this backfill fixes).
    low = blob.lower()
    return "youtu" in low or "youtube" in low


def main() -> None:
    enqueued: list[str] = []
    skipped_having = 0
    skipped_deleted = 0
    skipped_scope = 0

    for doc in DB.activities.find({"type": "Create"}):
        meta = doc.get("meta") or {}
        if meta.get("deleted"):
            skipped_deleted += 1
            continue
        if meta.get("og_metadata"):
            skipped_having += 1
            continue
        iri = doc.get("remote_id")
        if not iri:
            continue
        if not SCOPE_ALL and not _in_scope(doc, _blob(doc)):
            skipped_scope += 1
            continue
        if not DRY_RUN:
            enqueue_job("fetch_og_metadata", iri=iri)
        enqueued.append(iri)

    prefix = "[dry-run] would enqueue" if DRY_RUN else "Enqueued"
    print(f"{prefix} {len(enqueued)} fetch_og_metadata job(s)")
    print(f"skipped: {skipped_having} already have og_metadata, {skipped_deleted} deleted, {skipped_scope} out of scope")
    for iri in enqueued:
        print("  ", iri)


if __name__ == "__main__":
    main()
