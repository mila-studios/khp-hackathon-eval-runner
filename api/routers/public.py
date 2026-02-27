from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.schemas import MetricsOut, PublicEvalRequest, PublicEvalResponse, PublicJobStatus
from db.models import Dataset, Job, JobTeam, Team, TeamRunLog, TeamRunMetric
from db.session import get_db, get_database_url
from hackathon_runner.config import RunConfig
from hackathon_runner.dispatcher import ThreadJobDispatcher
from hackathon_runner.reporter import DbStageReporter
from hackathon_runner.team import Team as RunnerTeam

router = APIRouter(prefix="/public")

_dispatcher = ThreadJobDispatcher()

STAGES = ["clone", "validate_repo", "configure", "predict", "validate_predictions", "evaluate"]


@router.post("/eval", response_model=PublicEvalResponse, status_code=201)
def trigger_public_eval(body: PublicEvalRequest, db: Session = Depends(get_db)):
    team = db.query(Team).filter_by(team_id=body.team_id).first()
    if not team:
        raise HTTPException(404, f"Team {body.team_id!r} not found")

    dataset = db.query(Dataset).filter_by(is_public_test=True).first()
    if not dataset:
        raise HTTPException(500, "No public test dataset configured")

    run_id = f"public_{body.team_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    job = Job(
        run_id=run_id,
        status="PENDING",
        triggered_by="public",
        dataset_id=dataset.id,
        fail_fast=False,
    )
    db.add(job)
    db.flush()

    jt = JobTeam(job_id=job.id, team_id=team.team_id)
    db.add(jt)
    db.commit()
    db.refresh(job)

    root_dir = str(Path.cwd())
    db_url = get_database_url()

    fd, input_csv_path = tempfile.mkstemp(suffix=".csv", prefix=f"dataset_{dataset.name}_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(dataset.content)

    config = RunConfig(
        run_id=run_id,
        teams=[RunnerTeam(team_id=team.team_id, git_url=team.git_url)],
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
        continue_on_failure=True,
        extra_env={},
    )

    reporter = DbStageReporter(job_id=str(job.id), db_url=db_url)
    _dispatcher.dispatch(config, reporter, job_id=str(job.id), db_url=db_url)

    return PublicEvalResponse(job_id=str(job.id))


@router.get("/jobs/{job_id}", response_model=PublicJobStatus)
def get_public_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    jt = db.query(JobTeam).filter_by(job_id=job.id).first()
    if not jt:
        raise HTTPException(404, "No team data for this job")

    logs = db.query(TeamRunLog).filter_by(job_team_id=jt.id).all()
    stage_statuses: dict[str, str] = {}
    completed_stages = {lg.stage: lg for lg in logs}

    for s in STAGES:
        if s in completed_stages:
            stage_statuses[s] = "OK" if completed_stages[s].success else "FAILED"
        elif jt.current_stage == s and jt.status == "RUNNING":
            stage_statuses[s] = "RUNNING"
        else:
            stage_statuses[s] = "PENDING"

    metrics_out = None
    m = db.query(TeamRunMetric).filter_by(job_team_id=jt.id).first()
    if m:
        metrics_out = MetricsOut.model_validate(m)

    return PublicJobStatus(
        job_id=str(job.id),
        status=jt.status,
        team_id=jt.team_id,
        current_stage=jt.current_stage,
        stage_statuses=stage_statuses,
        metrics=metrics_out,
    )
