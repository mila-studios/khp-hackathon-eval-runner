from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from .config import RunConfig
from .orchestrator import run
from .reporter import StageReporter

_log = logging.getLogger(__name__)

CancelCheck = Callable[[], bool]


class JobDispatcher(Protocol):
    def dispatch(self, config: RunConfig, reporter: StageReporter, job_id: str, db_url: str) -> None: ...


class ThreadJobDispatcher:
    """Phase 2 — runs the orchestrator in a background thread."""

    def __init__(self) -> None:
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def cancel(self, job_id: str) -> bool:
        """Signal a running job to stop scheduling new stages/teams.

        Returns True if the job was found and signalled.
        """
        with self._lock:
            ev = self._cancel_events.get(job_id)
            if ev is None:
                return False
            ev.set()
            return True

    def dispatch(
        self,
        config: RunConfig,
        reporter: StageReporter,
        job_id: str,
        db_url: str,
    ) -> None:
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event

        def _cancel_check() -> bool:
            return cancel_event.is_set()

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

                exit_code = run(config, reporter, cancel_check=_cancel_check)

                job = session.query(Job).filter_by(id=job_id).first()
                if job:
                    if cancel_event.is_set():
                        job.status = "CANCELLED"
                    else:
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
                with self._lock:
                    self._cancel_events.pop(job_id, None)

        t = threading.Thread(target=_worker, daemon=True, name=f"job-{job_id}")
        t.start()
