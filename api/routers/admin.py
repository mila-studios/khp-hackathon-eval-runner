from __future__ import annotations

import csv
import io
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from api.auth import require_admin_key
from api.schemas import (
    DatasetCreate,
    DatasetOut,
    JobCreate,
    JobDetail,
    JobListItem,
    JobOut,
    JobTeamDetail,
    MetricsOut,
    RunSummary,
    RunTeamResult,
    TeamCreate,
    TeamOut,
    TeamUpdate,
)
from db.models import (
    Dataset,
    Job,
    JobTeam,
    Team,
    TeamRunLog,
    TeamRunMetric,
)
from db.session import get_db, get_database_url
from hackathon_runner.config import RunConfig
from hackathon_runner.dispatcher import ThreadJobDispatcher
from hackathon_runner.reporter import DbStageReporter
from hackathon_runner.team import Team as RunnerTeam

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_key)])

_dispatcher = ThreadJobDispatcher()


def _materialise_dataset(dataset: Dataset) -> str:
    """Write dataset content from DB to a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".csv", prefix=f"dataset_{dataset.name}_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(dataset.content)
    return path


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Teams ────────────────────────────────────────────────────────

@router.post("/teams", response_model=TeamOut, status_code=201)
def create_team(body: TeamCreate, db: Session = Depends(get_db)):
    if db.query(Team).filter_by(team_id=body.team_id).first():
        raise HTTPException(400, f"Team {body.team_id!r} already exists")
    team = Team(team_id=body.team_id, git_url=body.git_url)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@router.get("/teams", response_model=List[TeamOut])
def list_teams(db: Session = Depends(get_db)):
    return db.query(Team).order_by(Team.team_id).all()


@router.get("/teams/{team_id}", response_model=TeamOut)
def get_team(team_id: str, db: Session = Depends(get_db)):
    team = db.query(Team).filter_by(team_id=team_id).first()
    if not team:
        raise HTTPException(404, f"Team {team_id!r} not found")
    return team


@router.put("/teams/{team_id}", response_model=TeamOut)
def update_team(team_id: str, body: TeamUpdate, db: Session = Depends(get_db)):
    team = db.query(Team).filter_by(team_id=team_id).first()
    if not team:
        raise HTTPException(404, f"Team {team_id!r} not found")
    team.git_url = body.git_url
    db.commit()
    db.refresh(team)
    return team


@router.delete("/teams/{team_id}", status_code=204)
def delete_team(team_id: str, db: Session = Depends(get_db)):
    team = db.query(Team).filter_by(team_id=team_id).first()
    if not team:
        raise HTTPException(404, f"Team {team_id!r} not found")
    db.delete(team)
    db.commit()


@router.post("/teams/import", status_code=201)
def import_teams(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = file.file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    created = 0
    updated = 0
    for row in reader:
        tid = (row.get("team_id") or "").strip()
        gurl = (row.get("git_url") or "").strip()
        if not tid or not gurl:
            continue
        existing = db.query(Team).filter_by(team_id=tid).first()
        if existing:
            existing.git_url = gurl
            updated += 1
        else:
            db.add(Team(team_id=tid, git_url=gurl))
            created += 1
    db.commit()
    return {"created": created, "updated": updated}


# ── Datasets ─────────────────────────────────────────────────────

@router.post("/datasets", response_model=DatasetOut, status_code=201)
def create_dataset(
    name: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    is_public_test: bool = False,
    db: Session = Depends(get_db),
):
    if db.query(Dataset).filter_by(name=name).first():
        raise HTTPException(400, f"Dataset {name!r} already exists")

    if is_public_test:
        prev = db.query(Dataset).filter_by(is_public_test=True).first()
        if prev:
            prev.is_public_test = False

    raw = file.file.read()
    csv_text = raw.decode("utf-8", errors="replace")
    row_count = csv_text.count("\n")

    ds = Dataset(
        name=name,
        description=description,
        content=csv_text,
        row_count=max(row_count - 1, 0),
        is_public_test=is_public_test,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


@router.get("/datasets", response_model=List[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    return db.query(Dataset).order_by(Dataset.name).all()


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: str, db: Session = Depends(get_db)):
    ds = db.query(Dataset).filter_by(id=dataset_id).first()
    if not ds:
        raise HTTPException(404, "Dataset not found")
    referencing = db.query(Job).filter_by(dataset_id=ds.id).count()
    if referencing:
        raise HTTPException(409, f"Cannot delete: dataset is referenced by {referencing} job(s)")
    db.delete(ds)
    db.commit()


# ── Jobs ─────────────────────────────────────────────────────────

@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobCreate, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter_by(id=body.dataset_id).first()
    if not dataset:
        raise HTTPException(400, f"Dataset {body.dataset_id!r} not found")

    if body.team_ids == "all":
        teams = db.query(Team).order_by(Team.team_id).all()
    else:
        teams = db.query(Team).filter(Team.team_id.in_(body.team_ids)).all()
        found = {t.team_id for t in teams}
        missing = [tid for tid in body.team_ids if tid not in found]
        if missing:
            raise HTTPException(400, f"Unknown team_ids: {missing}")

    if not teams:
        raise HTTPException(400, "No teams selected")

    run_id = body.run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    job = Job(
        run_id=run_id,
        status="PENDING",
        triggered_by="admin",
        dataset_id=dataset.id,
        fail_fast=body.fail_fast,
    )
    db.add(job)
    db.flush()

    for t in teams:
        jt = JobTeam(job_id=job.id, team_id=t.team_id)
        db.add(jt)

    db.commit()
    db.refresh(job)

    root_dir = str(Path.cwd())
    db_url = get_database_url()
    input_csv_path = _materialise_dataset(dataset)

    config = RunConfig(
        run_id=run_id,
        teams=[RunnerTeam(team_id=t.team_id, git_url=t.git_url) for t in teams],
        root_dir=root_dir,
        input_csv=input_csv_path,
        work_dir=str(Path(root_dir) / "work" / run_id),
        out_dir=str(Path(root_dir) / "outputs" / run_id),
        eval_script=str(Path(root_dir) / os.environ.get("EVAL_SCRIPT", "scripts/evaluate.sh")),
        configure_script=os.environ.get("CONFIGURE_SCRIPT", "project/scripts/configure.sh"),
        predict_script=os.environ.get("PREDICT_SCRIPT", "project/scripts/predict.sh"),
        clone_timeout=int(os.environ.get("CLONE_TIMEOUT", "600")),
        configure_timeout=int(os.environ.get("CONFIGURE_TIMEOUT", "600")),
        predict_timeout=int(os.environ.get("PREDICT_TIMEOUT", "7200")),
        eval_timeout=int(os.environ.get("EVAL_TIMEOUT", "600")),
        pred_filename=os.environ.get("PRED_FILENAME", "predictions/predictions.csv"),
        metrics_filename=os.environ.get("METRICS_FILENAME", "metrics/metrics.csv"),
        continue_on_failure=not body.fail_fast,
        extra_env={},
    )

    reporter = DbStageReporter(job_id=str(job.id), db_url=db_url)
    _dispatcher.dispatch(config, reporter, job_id=str(job.id), db_url=db_url)

    return JobOut(job_id=str(job.id), run_id=run_id)


@router.get("/jobs", response_model=List[JobListItem])
def list_jobs(
    status: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Job)
    if status:
        q = q.filter(Job.status == status)
    if run_id:
        q = q.filter(Job.run_id == run_id)
    rows = q.order_by(Job.created_at.desc()).all()
    return [
        JobListItem(
            id=str(j.id), run_id=j.run_id, status=j.status,
            triggered_by=j.triggered_by, created_at=j.created_at,
            started_at=j.started_at, completed_at=j.completed_at,
        )
        for j in rows
    ]


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    team_details = [
        JobTeamDetail(
            id=str(jt.id), team_id=jt.team_id, status=jt.status,
            current_stage=jt.current_stage, failed_stage=jt.failed_stage,
            elapsed_s=jt.elapsed_s, error=jt.error,
            created_at=jt.created_at, completed_at=jt.completed_at,
        )
        for jt in job.job_teams
    ]

    return JobDetail(
        id=str(job.id), run_id=job.run_id, status=job.status,
        triggered_by=job.triggered_by, dataset_id=str(job.dataset_id),
        fail_fast=job.fail_fast, created_at=job.created_at,
        started_at=job.started_at, completed_at=job.completed_at,
        teams=team_details,
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in ("PENDING", "RUNNING"):
        raise HTTPException(409, f"Cannot cancel job in {job.status} state")

    from hackathon_runner.dispatcher import ThreadJobDispatcher
    # Try to signal the dispatcher if the job is running in-process
    # (API-triggered jobs use their own dispatcher instance, so this mainly
    # serves as a DB-level cancel for PENDING jobs)
    job.status = "CANCELLED"
    job.completed_at = datetime.now(timezone.utc).isoformat()
    for jt in job.job_teams:
        if jt.status in (None, "PENDING", "QUEUED"):
            jt.status = "CANCELLED"
    db.commit()
    return {"status": "cancelled", "job_id": job_id}


@router.get("/jobs/{job_id}/teams/{team_id}/logs/{stage}")
def get_job_team_log(job_id: str, team_id: str, stage: str, db: Session = Depends(get_db)):
    jt = db.query(JobTeam).filter_by(job_id=job_id, team_id=team_id).first()
    if not jt:
        raise HTTPException(404, "Job/team combination not found")
    log = db.query(TeamRunLog).filter_by(job_team_id=jt.id, stage=stage).first()
    if not log:
        raise HTTPException(404, f"No log for stage {stage!r}")
    return {"stage": log.stage, "success": log.success, "log_content": log.log_content}


@router.get("/jobs/{job_id}/teams/{team_id}/metrics", response_model=MetricsOut)
def get_job_team_metrics(job_id: str, team_id: str, db: Session = Depends(get_db)):
    jt = db.query(JobTeam).filter_by(job_id=job_id, team_id=team_id).first()
    if not jt:
        raise HTTPException(404, "Job/team combination not found")
    m = db.query(TeamRunMetric).filter_by(job_team_id=jt.id).first()
    if not m:
        raise HTTPException(404, "No metrics available for this team run")
    return m


# ── Runs (campaign view) ────────────────────────────────────────

@router.get("/runs", response_model=List[RunSummary])
def list_runs(db: Session = Depends(get_db)):
    from sqlalchemy import distinct, func, text

    rows = (
        db.query(
            Job.run_id,
            func.count(distinct(Job.id)).label("job_count"),
            func.count(distinct(JobTeam.team_id)).label("team_count"),
        )
        .join(JobTeam, JobTeam.job_id == Job.id)
        .group_by(Job.run_id)
        .order_by(Job.run_id.desc())
        .all()
    )

    results = []
    for run_id, job_count, team_count in rows:
        ok = (
            db.execute(
                text("""
                    SELECT count(*) FROM latest_team_results_by_run
                    WHERE run_id = :rid AND status = 'OK'
                """),
                {"rid": run_id},
            ).scalar() or 0
        )
        failed = (
            db.execute(
                text("""
                    SELECT count(*) FROM latest_team_results_by_run
                    WHERE run_id = :rid AND status != 'OK'
                """),
                {"rid": run_id},
            ).scalar() or 0
        )
        results.append(RunSummary(
            run_id=run_id, job_count=job_count, team_count=team_count,
            ok_count=ok, failed_count=failed,
        ))
    return results


@router.get("/runs/{run_id}", response_model=List[RunTeamResult])
def get_run(run_id: str, db: Session = Depends(get_db)):
    from sqlalchemy import text

    rows = db.execute(
        text("SELECT * FROM latest_team_results_by_run WHERE run_id = :rid"),
        {"rid": run_id},
    ).mappings().all()

    if not rows:
        raise HTTPException(404, f"No results for run_id {run_id!r}")

    return [
        RunTeamResult(
            run_id=r["run_id"], team_id=r["team_id"],
            job_id=str(r["job_id"]), job_team_id=str(r["job_team_id"]),
            status=r["status"], failed_stage=r["failed_stage"],
            elapsed_s=r["elapsed_s"], error=r["error"],
            completed_at=r["completed_at"],
        )
        for r in rows
    ]


@router.get("/runs/{run_id}/report")
def get_run_report(run_id: str, db: Session = Depends(get_db)):
    from fastapi.responses import StreamingResponse
    from sqlalchemy import text

    rows = db.execute(
        text("SELECT * FROM latest_team_results_by_run WHERE run_id = :rid ORDER BY team_id"),
        {"rid": run_id},
    ).mappings().all()

    if not rows:
        raise HTTPException(404, f"No results for run_id {run_id!r}")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["team_id", "status", "failed_stage", "elapsed_s", "error", "job_id"])
    for r in rows:
        w.writerow([r["team_id"], r["status"], r["failed_stage"] or "", r["elapsed_s"] or "", r["error"] or "", str(r["job_id"])])

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=report_{run_id}.csv"},
    )
