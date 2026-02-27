from __future__ import annotations

import csv
import io
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.models import AppSettings, Dataset, Job, JobTeam, Team, TeamRunLog, TeamRunMetric
from db.session import get_db, get_database_url
from hackathon_runner.config import RunConfig
from hackathon_runner.dispatcher import ThreadJobDispatcher
from hackathon_runner.reporter import DbStageReporter
from hackathon_runner.team import Team as RunnerTeam

router = APIRouter(prefix="/admin/dashboard")

_templates_dir = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

_dispatcher = ThreadJobDispatcher()

_SESSION_COOKIE = "eval_session"
_session_tokens: set[str] = set()

STAGES = ["clone", "validate_repo", "configure", "predict", "validate_predictions", "evaluate"]


def _check_session(eval_session: Optional[str] = Cookie(None)) -> bool:
    return eval_session is not None and eval_session in _session_tokens


def _flash_redirect(url: str, msg: str, msg_type: str = "success") -> RedirectResponse:
    resp = RedirectResponse(url=f"{url}?flash={msg}&flash_type={msg_type}", status_code=303)
    return resp


def _template_ctx(request: Request, **kwargs):
    ctx = {"request": request}
    flash_msg = request.query_params.get("flash")
    flash_type = request.query_params.get("flash_type", "success")
    if flash_msg:
        ctx["flash_msg"] = flash_msg
        ctx["flash_type"] = flash_type
    ctx.update(kwargs)
    return ctx


# ── Auth ─────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
def login_submit(request: Request, api_key: str = Form(...)):
    expected = os.environ.get("ADMIN_API_KEY", "")
    if not expected or api_key != expected:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid API key"})
    token = secrets.token_urlsafe(32)
    _session_tokens.add(token)
    resp = RedirectResponse(url="/admin/dashboard/", status_code=303)
    resp.set_cookie(_SESSION_COOKIE, token, httponly=True, samesite="lax")
    return resp


@router.get("/logout")
def logout(eval_session: Optional[str] = Cookie(None)):
    if eval_session:
        _session_tokens.discard(eval_session)
    resp = RedirectResponse(url="/admin/dashboard/login", status_code=303)
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


def _require_login(request: Request, eval_session: Optional[str] = Cookie(None)):
    if not eval_session or eval_session not in _session_tokens:
        return None
    return True


# ── Dashboard Home ───────────────────────────────────────────────

def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppSettings).filter_by(key=key).first()
    return row.value if row else default


@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request, db: Session = Depends(get_db), eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)

    team_count = db.query(Team).count()
    dataset_count = db.query(Dataset).count()
    job_count = db.query(Job).count()
    running_count = db.query(Job).filter(Job.status == "RUNNING").count()

    recent_jobs = db.query(Job).order_by(Job.created_at.desc()).limit(5).all()

    leaderboard_mode = _get_setting(db, "leaderboard_mode", "off")
    official_run_id = _get_setting(db, "official_run_id", "")
    all_runs = [
        r for (r,) in db.query(Job.run_id)
        .distinct().order_by(Job.run_id.desc()).all()
    ]

    return templates.TemplateResponse("dashboard.html", _template_ctx(
        request,
        team_count=team_count,
        dataset_count=dataset_count,
        job_count=job_count,
        running_count=running_count,
        recent_jobs=recent_jobs,
        leaderboard_mode=leaderboard_mode,
        official_run_id=official_run_id,
        completed_runs=all_runs,
    ))


@router.post("/settings/leaderboard")
def save_leaderboard_settings(
    request: Request,
    leaderboard_mode: str = Form("off"),
    official_run_id: str = Form(""),
    db: Session = Depends(get_db),
    eval_session: Optional[str] = Cookie(None),
):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)

    for key, val in [("leaderboard_mode", leaderboard_mode), ("official_run_id", official_run_id)]:
        row = db.query(AppSettings).filter_by(key=key).first()
        if row:
            row.value = val
        else:
            db.add(AppSettings(key=key, value=val))
    db.commit()
    return _flash_redirect("/admin/dashboard/", f"Leaderboard settings saved (mode={leaderboard_mode})")


# ── Teams ────────────────────────────────────────────────────────

@router.get("/teams", response_class=HTMLResponse)
def teams_page(request: Request, db: Session = Depends(get_db), eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)
    teams = db.query(Team).order_by(Team.team_id).all()
    return templates.TemplateResponse("teams.html", _template_ctx(request, teams=teams))


@router.post("/teams/import")
def teams_import(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                 eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)
    content = file.file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    created = updated = 0
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
    return _flash_redirect("/admin/dashboard/teams", f"Imported: {created} created, {updated} updated")


@router.post("/teams/{team_id}/delete")
def teams_delete(team_id: str, db: Session = Depends(get_db), eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)
    team = db.query(Team).filter_by(team_id=team_id).first()
    if team:
        db.delete(team)
        db.commit()
    return _flash_redirect("/admin/dashboard/teams", f"Deleted team {team_id}")


# ── Datasets ─────────────────────────────────────────────────────

@router.get("/datasets", response_class=HTMLResponse)
def datasets_page(request: Request, db: Session = Depends(get_db), eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)
    datasets = db.query(Dataset).order_by(Dataset.name).all()
    return templates.TemplateResponse("datasets.html", _template_ctx(request, datasets=datasets))


@router.post("/datasets/upload")
def datasets_upload(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    is_public_test: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    eval_session: Optional[str] = Cookie(None),
):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)

    if db.query(Dataset).filter_by(name=name).first():
        return _flash_redirect("/admin/dashboard/datasets", f"Dataset '{name}' already exists", "error")

    public = is_public_test == "true"
    if public:
        prev = db.query(Dataset).filter_by(is_public_test=True).first()
        if prev:
            prev.is_public_test = False

    raw = file.file.read()
    csv_text = raw.decode("utf-8", errors="replace")
    row_count = csv_text.count("\n")

    ds = Dataset(
        name=name,
        description=description or None,
        content=csv_text,
        row_count=max(row_count - 1, 0),
        is_public_test=public,
    )
    db.add(ds)
    db.commit()
    return _flash_redirect("/admin/dashboard/datasets", f"Dataset '{name}' uploaded ({max(row_count-1,0)} rows)")


@router.post("/datasets/{dataset_id}/delete")
def datasets_delete(dataset_id: str, db: Session = Depends(get_db), eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)
    ds = db.query(Dataset).filter_by(id=dataset_id).first()
    if ds:
        referencing = db.query(Job).filter_by(dataset_id=ds.id).count()
        if referencing:
            return _flash_redirect("/admin/dashboard/datasets",
                                   f"Cannot delete '{ds.name}': referenced by {referencing} job(s)", "error")
        db.delete(ds)
        db.commit()
        return _flash_redirect("/admin/dashboard/datasets", f"Deleted dataset '{ds.name}'")
    return _flash_redirect("/admin/dashboard/datasets", "Dataset not found", "error")


# ── Jobs ─────────────────────────────────────────────────────────

@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    eval_session: Optional[str] = Cookie(None),
):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)

    q = db.query(Job)
    if status:
        q = q.filter(Job.status == status)
    jobs = q.order_by(Job.created_at.desc()).all()
    datasets = db.query(Dataset).order_by(Dataset.name).all()
    teams = db.query(Team).order_by(Team.team_id).all()

    return templates.TemplateResponse("jobs.html", _template_ctx(
        request, jobs=jobs, datasets=datasets, teams=teams, status_filter=status or "",
    ))


@router.post("/jobs/trigger")
async def jobs_trigger(
    request: Request,
    dataset_id: str = Form(...),
    run_id: str = Form(""),
    fail_fast: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    eval_session: Optional[str] = Cookie(None),
):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)

    form = await request.form()
    selected_team_ids = form.getlist("team_ids")

    dataset = db.query(Dataset).filter_by(id=dataset_id).first()
    if not dataset:
        return _flash_redirect("/admin/dashboard/jobs", "Dataset not found", "error")

    if not selected_team_ids:
        teams = db.query(Team).order_by(Team.team_id).all()
    else:
        teams = db.query(Team).filter(Team.team_id.in_(selected_team_ids)).all()

    if not teams:
        return _flash_redirect("/admin/dashboard/jobs", "No teams selected", "error")

    actual_run_id = run_id.strip() or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    ff = fail_fast == "true"

    job = Job(
        run_id=actual_run_id,
        status="PENDING",
        triggered_by="admin",
        dataset_id=dataset.id,
        fail_fast=ff,
    )
    db.add(job)
    db.flush()

    for t in teams:
        db.add(JobTeam(job_id=job.id, team_id=t.team_id))
    db.commit()
    db.refresh(job)

    root_dir = str(Path.cwd())
    db_url = get_database_url()

    fd, input_csv_path = tempfile.mkstemp(suffix=".csv", prefix=f"dataset_{dataset.name}_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(dataset.content)

    config = RunConfig(
        run_id=actual_run_id,
        teams=[RunnerTeam(team_id=t.team_id, git_url=t.git_url) for t in teams],
        root_dir=root_dir,
        input_csv=input_csv_path,
        work_dir=str(Path(root_dir) / "work" / actual_run_id),
        out_dir=str(Path(root_dir) / "outputs" / actual_run_id),
        eval_script=str(Path(root_dir) / os.environ.get("EVAL_SCRIPT", "scripts/evaluate.sh")),
        configure_script=os.environ.get("CONFIGURE_SCRIPT", "project/scripts/configure.sh"),
        predict_script=os.environ.get("PREDICT_SCRIPT", "project/scripts/predict.sh"),
        clone_timeout=int(os.environ.get("CLONE_TIMEOUT", "600")),
        configure_timeout=int(os.environ.get("CONFIGURE_TIMEOUT", "600")),
        predict_timeout=int(os.environ.get("PREDICT_TIMEOUT", "7200")),
        eval_timeout=int(os.environ.get("EVAL_TIMEOUT", "600")),
        pred_filename=os.environ.get("PRED_FILENAME", "predictions/predictions.csv"),
        metrics_filename=os.environ.get("METRICS_FILENAME", "metrics/metrics.csv"),
        continue_on_failure=not ff,
        extra_env={},
    )

    reporter = DbStageReporter(job_id=str(job.id), db_url=db_url)
    _dispatcher.dispatch(config, reporter, job_id=str(job.id), db_url=db_url)

    return RedirectResponse(url=f"/admin/dashboard/jobs/{job.id}", status_code=303)


# ── Job Detail ───────────────────────────────────────────────────

def _build_job_teams_ctx(job, db: Session):
    result = []
    for jt in sorted(job.job_teams, key=lambda x: x.team_id):
        logs = db.query(TeamRunLog).filter_by(job_team_id=jt.id).all()
        completed_stages = {lg.stage: lg for lg in logs}
        stage_statuses = {}
        for s in STAGES:
            if s in completed_stages:
                stage_statuses[s] = "OK" if completed_stages[s].success else "FAILED"
            elif jt.current_stage == s and jt.status == "RUNNING":
                stage_statuses[s] = "RUNNING"
            else:
                stage_statuses[s] = "PENDING"

        result.append({
            "team_id": jt.team_id,
            "status": jt.status,
            "current_stage": jt.current_stage,
            "failed_stage": jt.failed_stage,
            "elapsed_s": jt.elapsed_s,
            "error": jt.error,
            "stage_statuses": stage_statuses,
        })
    return result


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str, db: Session = Depends(get_db),
               eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)

    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        return _flash_redirect("/admin/dashboard/jobs", "Job not found", "error")

    dataset = db.query(Dataset).filter_by(id=job.dataset_id).first()
    job_teams = _build_job_teams_ctx(job, db)

    return templates.TemplateResponse("job_detail.html", _template_ctx(
        request, job=job, dataset_name=dataset.name if dataset else None,
        job_teams=job_teams, job_id=str(job.id),
    ))


@router.get("/jobs/{job_id}/teams-partial", response_class=HTMLResponse)
def job_teams_partial(request: Request, job_id: str, db: Session = Depends(get_db),
                      eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return HTMLResponse(status_code=401)

    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        return HTMLResponse("<p>Job not found</p>")

    job_teams = _build_job_teams_ctx(job, db)
    return templates.TemplateResponse("partials/job_teams.html", {"request": request, "job_teams": job_teams, "job_id": job_id})


# ── Runs ──────────────────────────────────────────────────────────

@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, db: Session = Depends(get_db), eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)

    from sqlalchemy import func, case

    rows = (
        db.query(
            Job.run_id,
            func.count(Job.id).label("job_count"),
            func.min(Job.created_at).label("first_created"),
            func.max(Job.created_at).label("last_created"),
            func.max(case((Job.status == "RUNNING", 1), else_=0)).label("any_running"),
            func.max(case((Job.status == "FAILED", 1), else_=0)).label("any_failed"),
            func.max(Job.triggered_by).label("triggered_by"),
        )
        .group_by(Job.run_id)
        .order_by(func.max(Job.created_at).desc())
        .all()
    )

    runs = [
        {
            "run_id": r.run_id,
            "job_count": r.job_count,
            "first_created": r.first_created,
            "last_created": r.last_created,
            "any_running": r.any_running,
            "any_failed": r.any_failed,
            "triggered_by": r.triggered_by,
        }
        for r in rows
    ]

    return templates.TemplateResponse("runs.html", _template_ctx(request, runs=runs))


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: str, db: Session = Depends(get_db),
               eval_session: Optional[str] = Cookie(None)):
    if not _check_session(eval_session):
        return RedirectResponse(url="/admin/dashboard/login", status_code=303)

    from sqlalchemy import text

    jobs = db.query(Job).filter_by(run_id=run_id).order_by(Job.created_at.desc()).all()
    if not jobs:
        return _flash_redirect("/admin/dashboard/runs", f"No jobs found for run '{run_id}'", "error")

    ds = db.query(Dataset).filter_by(id=jobs[0].dataset_id).first()
    dataset_name = ds.name if ds else None

    view_rows = db.execute(
        text("SELECT * FROM latest_team_results_by_run WHERE run_id = :rid ORDER BY team_id"),
        {"rid": run_id},
    ).mappings().all()

    team_results = []
    for r in view_rows:
        m = db.query(TeamRunMetric).filter_by(job_team_id=r["job_team_id"]).first()
        team_results.append({
            "team_id": r["team_id"],
            "status": r["status"],
            "failed_stage": r["failed_stage"],
            "elapsed_s": r["elapsed_s"],
            "job_id": str(r["job_id"]),
            "f1": m.f1 if m else None,
            "precision": m.precision if m else None,
            "recall": m.recall if m else None,
            "latency_ms_mean": m.latency_ms_mean if m else None,
            "latency_ms_total": m.latency_ms_total if m else None,
        })

    return templates.TemplateResponse("run_detail.html", _template_ctx(
        request, run_id=run_id, jobs=jobs, team_results=team_results, dataset_name=dataset_name,
    ))


@router.get("/jobs/{job_id}/teams/{team_id}/logs/{stage}", response_class=HTMLResponse)
def stage_log_viewer(
    request: Request,
    job_id: str,
    team_id: str,
    stage: str,
    db: Session = Depends(get_db),
    eval_session: Optional[str] = Cookie(None),
):
    if not _check_session(eval_session):
        return HTMLResponse(status_code=401)

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
