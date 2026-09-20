"""Background worker: drains the jobs queue via NeoSQLite watch().

Single process (run one, not per web worker). Enqueued by tasks.py,
claimed with find_one_and_update, retried with exponential backoff.
The job handlers themselves live in `handlers.py`.
"""

import logging
import os
import random
import time
from datetime import UTC, datetime, timedelta

from micronote.config import DB, create_db_connection
from micronote.handlers import JOB_HANDLERS
from micronote.jobs import MAX_RETRIES, STATUS_FAILED, STATUS_PENDING, STATUS_PROCESSING
from micronote.utils import strtobool

log = logging.getLogger(__name__)

RESUME_TOKEN_ID = "jobs_watch"
SWEEP_INTERVAL_SECONDS = 60
REMOVE_FAILED_JOBS = strtobool(os.getenv("MICRONOTE_REMOVE_FAILED_JOBS", "false"))


def retry_delay(attempts) -> int:
    return int(random.uniform(2, 4) ** attempts)


def run_job(doc: dict) -> None:
    try:
        JOB_HANDLERS[doc["type"]](doc)
    except Exception as err:
        attempts = doc.get("attempts", 0) + 1
        if attempts > MAX_RETRIES:
            if REMOVE_FAILED_JOBS:
                log.exception(f"job {doc['_id']} failed permanently after {attempts} attempts, removing job")
                DB.jobs.delete_one({"_id": doc["_id"]})
            else:
                log.exception(f"job {doc['_id']} failed permanently after {attempts} attempts")
                DB.jobs.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"status": STATUS_FAILED, "attempts": attempts, "error": repr(err)}},
                )
        else:
            next_run = datetime.now(UTC) + timedelta(seconds=retry_delay(attempts))
            DB.jobs.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": STATUS_PENDING,
                        "attempts": attempts,
                        "next_run": next_run,
                        "error": repr(err),
                    }
                },
            )
    else:
        DB.jobs.delete_one({"_id": doc["_id"]})


def drain_jobs(limit: int = 100, max_passes: int = 100) -> int:
    """Claims and runs due jobs. Repeats until a pass claims nothing, so
    jobs chained mid-drain are picked up by the next pass."""
    processed = 0
    for _ in range(max_passes):
        claimed_any = False
        now = datetime.now(UTC)
        due = DB.jobs.find({"status": STATUS_PENDING, "next_run": {"$lte": now}}, limit=limit).sort("next_run", 1)
        for doc in due:
            claimed = DB.jobs.find_one_and_update(
                {"_id": doc["_id"], "status": STATUS_PENDING},
                {"$set": {"status": STATUS_PROCESSING}},
            )
            if not claimed:
                continue
            claimed_any = True
            run_job(claimed)
            processed += 1
        if not claimed_any:
            break
    return processed


def load_resume_token() -> str | None:
    state = DB.worker_state.find_one({"_id": RESUME_TOKEN_ID})
    return state["token"] if state else None


def save_resume_token(token: str) -> None:
    DB.worker_state.update_one({"_id": RESUME_TOKEN_ID}, {"$set": {"token": token}}, upsert=True)


def ensure_jobs_table() -> None:
    # watch() needs the table (and its triggers) to exist; inserts create
    # it on demand, so a sentinel round-trip suffices on fresh databases.
    DB.jobs.insert_one({"type": "_init", "status": STATUS_PROCESSING})
    DB.jobs.delete_many({"type": "_init"})


def run() -> None:
    log.info("worker starting, draining backlog")
    ensure_jobs_table()
    last_sweep = 0.0

    def swept_drain(limit: int) -> int:
        nonlocal last_sweep
        processed = drain_jobs(limit=limit)
        now = time.monotonic()
        if now - last_sweep >= SWEEP_INTERVAL_SECONDS:
            create_db_connection().sweep_ttl_once()
            last_sweep = now
        return processed

    swept_drain(limit=1000)
    resume = load_resume_token()
    while True:
        try:
            stream_kwargs = {
                "pipeline": [{"$match": {"fullDocument.status": STATUS_PENDING}}],
                "full_document": "updateLookup",
            }
            if resume is not None:
                stream_kwargs["resume_after"] = resume
            with DB.jobs.watch(**stream_kwargs) as stream:
                # NOTE: the stream blocks on idle (no None ticks in
                # NeoSQLite 1.16.1), so correctness never depends on
                # wakeups: every event triggers a drain-until-clean pass,
                # which also catches jobs chained by earlier passes.
                for change in stream:
                    if change is None:
                        continue
                    resume = change["_id"]
                    save_resume_token(resume)
                    full_document = change.get("fullDocument") or {}
                    if full_document.get("status") != STATUS_PENDING:
                        continue
                    swept_drain(limit=100)
        except ValueError:
            log.exception("bad resume token, restarting from now")
            resume = None
        except Exception:
            log.exception("watch failed, retrying in 5s")
            time.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        run()
    except KeyboardInterrupt:
        log.info("worker stopped")


if __name__ == "__main__":
    main()
