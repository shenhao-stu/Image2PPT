"""Background sweeper. Two passes per cycle:

1. **Retention**: delete finished jobs older than `job_retention_days`.
2. **Orphan**: delete `web/data/{uploads,outputs}/<id>` sub-dirs whose
   id is no longer in the `jobs` table. Catches anything that slipped
   past the DELETE/bulk-delete handlers (busy files at delete-time,
   crashed handlers, manual DB cleanup, …) so the disk can't quietly
   diverge from the DB.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .db import SessionLocal
from .models import Job
from . import queue as job_queue

log = logging.getLogger("deckweaver.retention")


def _sweep_once() -> tuple[int, int]:
    """Run one retention + orphan pass. Returns (jobs_deleted, dirs_swept)."""
    s = get_settings()
    deleted = 0
    if s.job_retention_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=s.job_retention_days)
        db = SessionLocal()
        try:
            stale = (
                db.query(Job)
                .filter(Job.status.in_(("done", "failed", "canceled")))
                .filter(Job.finished_at.is_not(None))
                .filter(Job.finished_at < cutoff)
                .all()
            )
            for j in stale:
                job_queue.cleanup_job_dirs(j)
                db.delete(j)
                deleted += 1
            db.commit()
        finally:
            db.close()
    swept = job_queue.sweep_orphan_dirs()
    return deleted, swept


async def sweeper_loop() -> None:
    s = get_settings()
    while True:
        try:
            deleted, swept = await asyncio.to_thread(_sweep_once)
            if deleted:
                log.info("retention: deleted %d stale job(s)", deleted)
            if swept:
                log.info("retention: swept %d orphan dir(s)", swept)
        except Exception as exc:  # pragma: no cover
            log.warning("retention sweep failed: %s", exc)
        await asyncio.sleep(max(60, s.retention_sweep_seconds))
