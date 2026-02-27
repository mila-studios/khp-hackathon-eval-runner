from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Protocol

from .config import RunConfig
from .orchestrator import run
from .reporter import StageReporter

_log = logging.getLogger(__name__)


class JobDispatcher(Protocol):
    def dispatch(self, config: RunConfig, reporter: StageReporter, job_id: str, db_url: str) -> None: ...


class ThreadJobDispatcher:
    """Phase 2 — runs the orchestrator in a background thread."""

    def dispatch(
        self,
        config: RunConfig,
        reporter: StageReporter,
        job_id: str,
        db_url: str,
    ) -> None:
        def _worker() -> None:
            from db.models import Job
            from db.session import make_session

            session = make_session(db_url)
            try:
                job = session.query(Job).filter_by(id=job_id).first()
                if job:
                    job.status = "RUNNING"
                    job.started_at = datetime.now(timezone.utc).isoformat()
                    session.commit()

                exit_code = run(config, reporter)

                job = session.query(Job).filter_by(id=job_id).first()
                if job:
                    job.status = "COMPLETED" if exit_code == 0 else "FAILED"
                    job.completed_at = datetime.now(timezone.utc).isoformat()
                    session.commit()
            except Exception:
                _log.exception("Job %s failed with unhandled exception", job_id)
                try:
                    job = session.query(Job).filter_by(id=job_id).first()
                    if job:
                        job.status = "FAILED"
                        job.completed_at = datetime.now(timezone.utc).isoformat()
                        session.commit()
                except Exception:
                    session.rollback()
            finally:
                session.close()

        t = threading.Thread(target=_worker, daemon=True, name=f"job-{job_id}")
        t.start()
