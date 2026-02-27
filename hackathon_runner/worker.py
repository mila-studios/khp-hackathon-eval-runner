"""Standalone worker entry point for Phase 3 (K8S).

Usage:
    python -m hackathon_runner.worker --job-id <JOB_UUID>

Reads the job definition from the database, materialises the dataset to a
temp file, builds a RunConfig, and runs the orchestrator.  Reports results
back to Postgres via DbStageReporter.

Designed to run as a K8S Job container with DATABASE_URL injected from a
Secret.  Exits 0 on success, 1 on failure, 2 on evaluation failures, 3 on
cancellation.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import RunConfig
from .orchestrator import run
from .reporter import DbStageReporter
from .team import Team as RunnerTeam


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an evaluation job from the DB")
    parser.add_argument("--job-id", required=True, help="UUID of the job to execute")
    args = parser.parse_args(argv)

    from db.session import get_database_url, make_session
    from db.models import Dataset, Job, JobTeam, Team

    db_url = get_database_url()
    session = make_session(db_url)

    try:
        job = session.query(Job).filter_by(id=args.job_id).first()
        if not job:
            print(f"ERROR: Job {args.job_id} not found", file=sys.stderr)
            return 1

        if job.status not in ("PENDING", "RUNNING"):
            print(f"ERROR: Job {args.job_id} is in {job.status} state, skipping", file=sys.stderr)
            return 1

        job.status = "RUNNING"
        job.started_at = datetime.now(timezone.utc).isoformat()
        session.commit()

        dataset = session.query(Dataset).filter_by(id=job.dataset_id).first()
        if not dataset:
            print(f"ERROR: Dataset {job.dataset_id} not found", file=sys.stderr)
            job.status = "FAILED"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            return 1

        job_teams = session.query(JobTeam).filter_by(job_id=job.id).all()
        team_ids = [jt.team_id for jt in job_teams]
        teams_db = session.query(Team).filter(Team.team_id.in_(team_ids)).all()
        teams_map = {t.team_id: t for t in teams_db}

        runner_teams = []
        for tid in team_ids:
            t = teams_map.get(tid)
            if t:
                runner_teams.append(RunnerTeam(team_id=t.team_id, git_url=t.git_url))

        if not runner_teams:
            print(f"ERROR: No valid teams for job {args.job_id}", file=sys.stderr)
            job.status = "FAILED"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            return 1

        fd, input_csv_path = tempfile.mkstemp(suffix=".csv", prefix=f"dataset_{dataset.name}_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dataset.content)

        root_dir = str(Path.cwd())

        config = RunConfig(
            run_id=job.run_id,
            teams=runner_teams,
            root_dir=root_dir,
            input_csv=input_csv_path,
            work_dir=str(Path(root_dir) / "work" / job.run_id),
            out_dir=str(Path(root_dir) / "outputs" / job.run_id),
            eval_script=str(Path(root_dir) / os.environ.get("EVAL_SCRIPT", "scripts/evaluate.sh")),
            configure_script=os.environ.get("CONFIGURE_SCRIPT", "project/scripts/configure.sh"),
            predict_script=os.environ.get("PREDICT_SCRIPT", "project/scripts/predict.sh"),
            clone_timeout=int(os.environ.get("CLONE_TIMEOUT", "600")),
            configure_timeout=int(os.environ.get("CONFIGURE_TIMEOUT", "600")),
            predict_timeout=int(os.environ.get("PREDICT_TIMEOUT", "7200")),
            eval_timeout=int(os.environ.get("EVAL_TIMEOUT", "600")),
            pred_filename=os.environ.get("PRED_FILENAME", "predictions/predictions.csv"),
            metrics_filename=os.environ.get("METRICS_FILENAME", "metrics/metrics.csv"),
            continue_on_failure=not job.fail_fast,
            extra_env={},
        )

        reporter = DbStageReporter(job_id=str(job.id), db_url=db_url)
        exit_code = run(config, reporter)

        session.refresh(job)
        if job.status != "CANCELLED":
            job.status = "COMPLETED" if exit_code == 0 else "FAILED"
        job.completed_at = datetime.now(timezone.utc).isoformat()
        session.commit()

        return exit_code

    except Exception as exc:
        print(f"ERROR: Unhandled exception: {exc}", file=sys.stderr)
        try:
            job = session.query(Job).filter_by(id=args.job_id).first()
            if job:
                job.status = "FAILED"
                job.completed_at = datetime.now(timezone.utc).isoformat()
                session.commit()
        except Exception:
            session.rollback()
        return 1
    finally:
        try:
            os.unlink(input_csv_path)
        except (OSError, NameError):
            pass
        session.close()


if __name__ == "__main__":
    sys.exit(main())
