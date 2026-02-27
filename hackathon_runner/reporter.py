from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .config import TeamReportRow


@runtime_checkable
class StageReporter(Protocol):
    """Reports stage-level and team-level progress during a run.

    Implementations:
      - NullStageReporter  (Phase 1 CLI — no-op)
      - DbStageReporter    (Phase 2/3 — persists to Postgres)
    """

    def on_stage_complete(
        self,
        team_id: str,
        stage: str,
        success: bool,
        log_content: str,
    ) -> None: ...

    def on_team_complete(
        self,
        team_id: str,
        result: TeamReportRow,
    ) -> None: ...


class NullStageReporter:
    """No-op reporter for CLI mode — filesystem-only, unchanged behaviour."""

    def on_stage_complete(
        self,
        team_id: str,
        stage: str,
        success: bool,
        log_content: str,
    ) -> None:
        pass

    def on_team_complete(
        self,
        team_id: str,
        result: TeamReportRow,
    ) -> None:
        pass


_log = logging.getLogger(__name__)


class DbStageReporter:
    """Phase 2 + 3 reporter — persists stage results to Postgres.

    Creates its own SQLAlchemy session from *db_url* so it is safe to use
    from a background thread (no session sharing with the request thread).
    """

    def __init__(self, job_id: str, db_url: str) -> None:
        self._job_id = job_id
        self._db_url = db_url

        from db.session import make_session
        self._session = make_session(db_url)

    # ── helpers ──────────────────────────────────────────────────

    def _utcnow_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _get_job_team_id(self, team_id: str):
        """Look up the job_teams row for (self._job_id, team_id)."""
        from db.models import JobTeam
        row = (
            self._session.query(JobTeam)
            .filter_by(job_id=self._job_id, team_id=team_id)
            .first()
        )
        return row

    # ── protocol methods ─────────────────────────────────────────

    def on_stage_complete(
        self,
        team_id: str,
        stage: str,
        success: bool,
        log_content: str,
    ) -> None:
        try:
            from db.models import JobTeam, TeamRunLog

            jt = self._get_job_team_id(team_id)
            if jt is None:
                _log.warning("DbStageReporter: no job_team row for job=%s team=%s", self._job_id, team_id)
                return

            jt.current_stage = stage
            jt.status = "RUNNING"

            log_row = TeamRunLog(
                job_team_id=jt.id,
                stage=stage,
                success=success,
                log_content=log_content or None,
            )
            self._session.add(log_row)
            self._session.commit()
        except Exception:
            self._session.rollback()
            _log.exception("DbStageReporter.on_stage_complete failed for team=%s stage=%s", team_id, stage)

    def on_team_complete(
        self,
        team_id: str,
        result: TeamReportRow,
    ) -> None:
        try:
            from db.models import JobTeam, TeamRunArtifact, TeamRunMetric

            jt = self._get_job_team_id(team_id)
            if jt is None:
                _log.warning("DbStageReporter: no job_team row for job=%s team=%s", self._job_id, team_id)
                return

            jt.status = result.final_status
            jt.failed_stage = result.failed_stage or None
            jt.elapsed_s = result.elapsed_s
            jt.error = result.error or None
            jt.completed_at = self._utcnow_iso()

            if result.final_status == "OK":
                self._persist_metrics(jt, result)
                self._persist_artifacts(jt, result)

            self._session.commit()
        except Exception:
            self._session.rollback()
            _log.exception("DbStageReporter.on_team_complete failed for team=%s", team_id)

    # ── metrics / artifacts ──────────────────────────────────────

    def _persist_metrics(self, jt, result: TeamReportRow) -> None:
        """Parse the eval_metrics.json produced by the evaluate stage and store in team_run_metrics."""
        from db.models import TeamRunMetric

        if not result.metrics_path:
            return

        metrics_dir = Path(result.metrics_path).parent if result.metrics_path else None
        if metrics_dir is None:
            return

        # The evaluate script writes eval_metrics.json next to metrics.csv
        json_path = metrics_dir / "eval_metrics.json"
        if not json_path.exists():
            # Fall back: try the metrics_path itself in case it's the JSON
            json_path = Path(result.metrics_path)
            if not json_path.exists():
                return

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            _log.warning("Could not parse metrics JSON at %s", json_path)
            return

        guardrail = data.get("guardrail", data)

        metric = TeamRunMetric(
            job_team_id=jt.id,
            precision=float(guardrail.get("precision", 0)),
            recall=float(guardrail.get("recall", 0)),
            f1=float(guardrail.get("f1", 0)),
            support_harmful=int(guardrail.get("support_harmful", 0)),
            support_safe=int(guardrail.get("support_safe", 0)),
            total_samples=int(guardrail.get("total_samples", data.get("total_samples", 0))),
            latency_ms_mean=guardrail.get("latency_ms_mean"),
            latency_ms_total=guardrail.get("latency_ms_total"),
        )
        self._session.add(metric)

    def _persist_artifacts(self, jt, result: TeamReportRow) -> None:
        """Store predictions CSV and metrics JSON as artifacts."""
        from db.models import TeamRunArtifact

        for artifact_type, path_str in [
            ("predictions", result.pred_path),
            ("metrics_csv", result.metrics_path),
        ]:
            if not path_str:
                continue
            p = Path(path_str)
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    self._session.add(TeamRunArtifact(
                        job_team_id=jt.id,
                        artifact_type=artifact_type,
                        content=content,
                    ))
                except Exception:
                    _log.warning("Could not read artifact %s at %s", artifact_type, p)
