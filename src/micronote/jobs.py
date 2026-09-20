"""Background job queue: enqueueing and status constants.

The queue mechanics (claiming, retries, watch loop) live in `worker.py`.
This module is the dependency-free seam that `tasks.py` and `worker.py`
share, so neither has to import the other.
"""

import logging
import os
from datetime import UTC, datetime

from micronote.config import DB
from micronote.utils import strtobool

log = logging.getLogger(__name__)

MAX_RETRIES = int(os.getenv("MICRONOTE_MAX_RETRIES", "3"))

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_FAILED = "failed"

# Local testing without a worker process: drain the job queue inline
# after every enqueue. Production runs worker.py instead.
TASK_EAGER = strtobool(os.getenv("MICRONOTE_TASK_EAGER", "false"))


def enqueue_job(job_type, iri=None, payload=None, to=None, also_cache_attachments=True):
    """Stores a background job; worker.py picks it up via watch()."""
    job = {
        "type": job_type,
        "iri": iri,
        "payload": payload,
        "to": to,
        "also_cache_attachments": also_cache_attachments,
        "status": STATUS_PENDING,
        "attempts": 0,
        "next_run": datetime.now(UTC),
        "error": None,
    }
    DB.jobs.insert_one(job)
    log.info(f"enqueued {job_type} iri={iri}")
    if TASK_EAGER:
        # Function-level import: worker.py imports this module at module
        # level, so a top-level import would create an import cycle.
        from micronote.worker import drain_jobs

        drain_jobs()
    return job
