from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.models import AppSettings, Dataset, Job, JobTeam, Team, TeamRunLog, TeamRunMetric
from db.session import get_db, get_database_url
from hackathon_runner.config import RunConfig
from hackathon_runner.dispatcher import ThreadJobDispatcher
from hackathon_runner.reporter import DbStageReporter
from hackathon_runner.team import Team as RunnerTeam

router = APIRouter(prefix="/public/dashboard")

_templates_dir = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

_dispatcher = ThreadJobDispatcher()

STAGES = ["clone", "validate_repo", "configure", "predict", "validate_predictions", "evaluate"]

EVAL_COOLDOWN_SECONDS = int(os.environ.get("PUBLIC_EVAL_COOLDOWN", "900"))


def _flash_redirect(url: str, msg: str, msg_type: str = "success") -> RedirectResponse:
    return RedirectResponse(url=f"{url}?flash={msg}&flash_type={msg_type}", status_code=303)


def _board_ctx(request: Request, **kwargs):
    ctx = {"request": request}
    flash_msg = request.query_params.get("flash")
    flash_type = request.query_params.get("flash_type", "success")
    if flash_msg:
        ctx["flash_msg"] = flash_msg
        ctx["flash_type"] = flash_type
    ctx.update(kwargs)
    return ctx


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppSettings).filter_by(key=key).first()
    return row.value if row else default


# ── Home ──────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def board_home(request: Request):
    return templates.TemplateResponse("board_home.html", _board_ctx(request))


# ── Team Page ─────────────────────────────────────────────────────

def _build_team_evals(team_id: str, db: Session):
    """Build the evaluation history for a single team."""
    jt_rows = (
        db.query(JobTeam)
        .filter(JobTeam.team_id == team_id)
        .join(Job)
        .order_by(Job.created_at.desc())
        .all()
    )
    evals = []
    for jt in jt_rows:
        logs = db.query(TeamRunLog).filter_by(job_team_id=jt.id).all()
        completed = {lg.stage: lg for lg in logs}
        stage_statuses = {}
        for s in STAGES:
            if s in completed:
                stage_statuses[s] = "OK" if completed[s].success else "FAILED"
            elif jt.current_stage == s and jt.status == "RUNNING":
                stage_statuses[s] = "RUNNING"
            else:
                stage_statuses[s] = "PENDING"

        metrics = db.query(TeamRunMetric).filter_by(job_team_id=jt.id).first()
        evals.append({
            "job_id": str(jt.job.id),
            "team_id": jt.team_id,
            "run_id": jt.job.run_id,
            "status": jt.status,
            "stage_statuses": stage_statuses,
            "f1": metrics.f1 if metrics else None,
            "precision": metrics.precision if metrics else None,
            "recall": metrics.recall if metrics else None,
            "latency_ms_mean": metrics.latency_ms_mean if metrics else None,
            "latency_ms_total": metrics.latency_ms_total if metrics else None,
            "created_at": jt.job.created_at,
        })
    return evals


@router.get("/team", response_class=HTMLResponse)
def team_page(request: Request, team_id: str = Query(...), db: Session = Depends(get_db)):
    team = db.query(Team).filter_by(team_id=team_id).first()
    if not team:
        return _flash_redirect("/public", f"Team '{team_id}' not found", "error")
    return RedirectResponse(url=f"/public/dashboard/team/{team_id}", status_code=303)


@router.get("/team/{team_id}", response_class=HTMLResponse)
def team_detail(request: Request, team_id: str, db: Session = Depends(get_db)):
    team = db.query(Team).filter_by(team_id=team_id).first()
    if not team:
        return _flash_redirect("/public", f"Team '{team_id}' not found", "error")

    public_ds = db.query(Dataset).filter_by(is_public_test=True).first()

    blocked_reason = None
    can_trigger = public_ds is not None
    no_public_dataset = public_ds is None

    if can_trigger:
        running = (
            db.query(JobTeam)
            .filter(JobTeam.team_id == team_id, JobTeam.status == "RUNNING")
            .count()
        )
        if running:
            blocked_reason = "Eval already running"

        if not blocked_reason and EVAL_COOLDOWN_SECONDS > 0:
            latest_jt = (
                db.query(JobTeam)
                .filter(JobTeam.team_id == team_id)
                .join(Job)
                .filter(Job.triggered_by == "public")
                .order_by(Job.created_at.desc())
                .first()
            )
            if latest_jt:
                try:
                    created = datetime.fromisoformat(latest_jt.job.created_at)
                    elapsed = (datetime.now(timezone.utc) - created).total_seconds()
                    remaining = EVAL_COOLDOWN_SECONDS - elapsed
                    if remaining > 0:
                        mins = int(remaining // 60) + 1
                        blocked_reason = f"Cooldown: wait ~{mins} min"
                except (ValueError, TypeError):
                    pass

    evals = _build_team_evals(team_id, db)
    has_running = any(e["status"] == "RUNNING" for e in evals)

    latest_metrics = None
    for e in evals:
        if e["f1"] is not None:
            latest_metrics = e
            break

    return templates.TemplateResponse("board_team.html", _board_ctx(
        request,
        team_id=team_id,
        can_trigger=can_trigger,
        no_public_dataset=no_public_dataset,
        blocked_reason=blocked_reason,
        evals=evals,
        has_running=has_running,
        latest_metrics=latest_metrics,
    ))


@router.get("/team/{team_id}/evals-partial", response_class=HTMLResponse)
def team_evals_partial(request: Request, team_id: str, db: Session = Depends(get_db)):
    evals = _build_team_evals(team_id, db)
    return templates.TemplateResponse("partials/board_evals.html", {"request": request, "evals": evals})


@router.get("/team/{team_id}/logs/{job_id}/{stage}", response_class=HTMLResponse)
def team_stage_log(
    request: Request,
    team_id: str,
    job_id: str,
    stage: str,
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        return HTMLResponse("<p>Job not found</p>")

    jt = db.query(JobTeam).filter_by(job_id=job.id, team_id=team_id).first()
    if not jt:
        return HTMLResponse(f"<p>Team {team_id} not found in this job</p>")

    log_entry = db.query(TeamRunLog).filter_by(job_team_id=jt.id, stage=stage).first()
    log_content = ""
    success = True
    if log_entry:
        log_content = log_entry.log_content or ""
        success = log_entry.success
    else:
        log_path = Path("outputs") / job.run_id / team_id / "logs" / f"{stage}.log"
        if log_path.exists():
            try:
                log_content = log_path.read_text(encoding="utf-8", errors="replace")[-20_000:]
            except Exception:
                log_content = "(could not read log file)"

    return templates.TemplateResponse("partials/stage_log.html", {
        "request": request, "team_id": team_id, "stage": stage,
        "log_content": log_content, "success": success,
    })


@router.post("/team/{team_id}/trigger")
def team_trigger_eval(request: Request, team_id: str, db: Session = Depends(get_db)):
    team = db.query(Team).filter_by(team_id=team_id).first()
    if not team:
        return _flash_redirect("/public", f"Team '{team_id}' not found", "error")

    public_ds = db.query(Dataset).filter_by(is_public_test=True).first()
    if not public_ds:
        return _flash_redirect(f"/public/dashboard/team/{team_id}", "No public dataset configured", "error")

    running = (
        db.query(JobTeam)
        .filter(JobTeam.team_id == team_id, JobTeam.status == "RUNNING")
        .count()
    )
    if running:
        return _flash_redirect(f"/public/dashboard/team/{team_id}", "You already have an eval running", "error")

    if EVAL_COOLDOWN_SECONDS > 0:
        latest_jt = (
            db.query(JobTeam)
            .filter(JobTeam.team_id == team_id)
            .join(Job)
            .filter(Job.triggered_by == "public")
            .order_by(Job.created_at.desc())
            .first()
        )
        if latest_jt:
            try:
                created = datetime.fromisoformat(latest_jt.job.created_at)
                elapsed = (datetime.now(timezone.utc) - created).total_seconds()
                if elapsed < EVAL_COOLDOWN_SECONDS:
                    mins = int((EVAL_COOLDOWN_SECONDS - elapsed) // 60) + 1
                    return _flash_redirect(
                        f"/public/dashboard/team/{team_id}",
                        f"Please wait ~{mins} more minute(s) before submitting again",
                        "error",
                    )
            except (ValueError, TypeError):
                pass

    run_id = f"public_{team_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    job = Job(
        run_id=run_id,
        status="PENDING",
        triggered_by="public",
        dataset_id=public_ds.id,
        fail_fast=False,
    )
    db.add(job)
    db.flush()

    db.add(JobTeam(job_id=job.id, team_id=team_id))
    db.commit()
    db.refresh(job)

    root_dir = str(Path.cwd())
    db_url = get_database_url()

    fd, input_csv_path = tempfile.mkstemp(suffix=".csv", prefix=f"dataset_{public_ds.name}_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(public_ds.content)

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

    return _flash_redirect(f"/public/dashboard/team/{team_id}", "Eval triggered! Refresh to track progress.", "success")


# ── Leaderboard ───────────────────────────────────────────────────

@router.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request, db: Session = Depends(get_db)):
    mode = _get_setting(db, "leaderboard_mode", "off")
    official_run_id = _get_setting(db, "official_run_id", "")

    entries = []
    running_teams = []
    failed_teams = []
    dataset_name = None
    if mode == "full" and official_run_id:
        from sqlalchemy import text

        # Get dataset name from the latest job in this run
        latest_job = db.query(Job).filter_by(run_id=official_run_id).order_by(Job.created_at.desc()).first()
        if latest_job:
            ds = db.query(Dataset).filter_by(id=latest_job.dataset_id).first()
            dataset_name = ds.name if ds else None

        # Use the consolidated view: latest result per team across all jobs in this run
        view_rows = db.execute(
            text("SELECT * FROM latest_team_results_by_run WHERE run_id = :rid"),
            {"rid": official_run_id},
        ).mappings().all()

        for r in view_rows:
            if r["status"] == "OK":
                m = db.query(TeamRunMetric).filter_by(job_team_id=r["job_team_id"]).first()
                if m:
                    entries.append({
                        "team_id": r["team_id"],
                        "f1": m.f1,
                        "precision": m.precision,
                        "recall": m.recall,
                        "latency_ms_mean": m.latency_ms_mean,
                        "latency_ms_total": m.latency_ms_total,
                    })
            elif r["status"] == "FAILED":
                failed_teams.append({"team_id": r["team_id"], "status": r["status"], "failed_stage": r["failed_stage"]})
            else:
                running_teams.append({"team_id": r["team_id"], "status": r["status"], "failed_stage": None})

        entries.sort(key=lambda x: (-x["f1"], x["latency_ms_mean"] or float("inf")))

    return templates.TemplateResponse("board_leaderboard.html", _board_ctx(
        request, mode=mode, official_run_id=official_run_id, entries=entries,
        dataset_name=dataset_name, running_teams=running_teams, failed_teams=failed_teams,
    ))
