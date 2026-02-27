# Phase 2 Implementation: CLI Refactor + API Service + DB

## Overview

Refactor the CLI orchestrator to use `RunConfig` + `StageReporter` abstractions, then build the Phase 2 API service (FastAPI) and Postgres persistence layer (SQLAlchemy + Alembic) on top of the shared runner.

---

## Target File Structure

New and modified files (existing unchanged files omitted):

```
hackathon_runner/
    app.py              # MODIFIED — builds RunConfig from parsed args
    config.py           # NEW — RunConfig dataclass
    reporter.py         # NEW — StageReporter protocol, NullStageReporter, DbStageReporter
    dispatcher.py       # NEW — JobDispatcher protocol, ThreadJobDispatcher
    orchestrator.py     # MODIFIED — run(config, reporter) signature

db/
    __init__.py         # NEW
    models.py           # NEW — SQLAlchemy ORM models (7 tables)
    session.py          # NEW — engine + SessionLocal factory

api/
    __init__.py         # NEW
    main.py             # NEW — FastAPI app + lifespan + CORS
    auth.py             # NEW — X-Api-Key dependency (reads ADMIN_API_KEY env var)
    schemas.py          # NEW — Pydantic request/response models
    routers/
        __init__.py     # NEW
        admin.py        # NEW — /admin/teams, /admin/datasets, /admin/jobs, /admin/runs
        public.py       # NEW — /public/eval, /public/jobs/{job_id}

alembic/              # NEW — migration directory
    env.py
    versions/
        001_initial.py

alembic.ini           # NEW
requirements.txt      # MODIFIED — add fastapi, uvicorn, sqlalchemy, alembic, psycopg2-binary, pydantic
```

---

## Step 1 — Orchestrator Refactor (CLI stays working)

### 1a. Create `hackathon_runner/config.py`

Extract `RunConfig` from the fields currently on `argparse.Namespace`. All paths are plain strings for JSON serializability.

```python
@dataclass
class RunConfig:
    run_id: str
    teams: list[Team]
    root_dir: str             # project root — used as cwd for the evaluate stage (PYTHONPATH=.)
    input_csv: str
    work_dir: str
    out_dir: str
    eval_script: str
    configure_script: str
    predict_script: str
    clone_timeout: int
    configure_timeout: int
    predict_timeout: int
    eval_timeout: int
    pred_filename: str
    metrics_filename: str
    continue_on_failure: bool
    extra_env: dict[str, str]
```

### 1b. Create `hackathon_runner/reporter.py`

```python
class StageReporter(Protocol):
    def on_stage_complete(self, team_id: str, stage: str, success: bool, log_content: str) -> None: ...
    def on_team_complete(self, team_id: str, result: TeamReportRow) -> None: ...

class NullStageReporter:
    """No-op for CLI mode."""
    def on_stage_complete(self, *a, **kw): pass
    def on_team_complete(self, *a, **kw): pass
```

The `on_team_complete` callback is added so `DbStageReporter` can update `job_teams.status`, `elapsed_s`, `error`, and `completed_at` when a team finishes, and persist metrics/artifacts.

### 1c. Modify `hackathon_runner/orchestrator.py`

Key changes (the bulk of the refactor):

- **Signature**: `def run(config: RunConfig, reporter: StageReporter | None = None) -> int`
- **Move out of `run()`**: `.env` loading, teams CSV reading, path resolution, team filtering. These move to `app.py` (CLI adapter).
- **Keep in `run()`**: filename validation, dir creation, manifest writing, team iteration loop, report writing.
- **Convert paths**: `run()` internally does `Path(config.work_dir)` etc. at the top.
- **Add reporter calls**: After each stage completes/fails in `_run_team()`, call `reporter.on_stage_complete(team_id, stage, success, log_content)` where `log_content` is read from the stage's `.log` file. After the team finishes, call `reporter.on_team_complete(team_id, result)`.

The 6 integration points inside `_run_team()` in `hackathon_runner/orchestrator.py`:

1. After clone (line ~231) — read `logs/clone.log`
2. After validate_repo (line ~296 success / ~251 failure) — read `logs/validate_repo.log`
3. After configure (line ~343) — read `logs/configure.log`
4. After predict (line ~356) — read `logs/predict.log`
5. After validate_predictions (line ~380) — read `logs/validate_predictions.log`
6. After evaluate (line ~393) — read `logs/evaluate.log`

Each reads the log file that was already written by `run_cmd()` or the inline validation logic. Note: `validate_predictions` only writes a log file on failure. On success, the reporter receives an empty string for `log_content`.

### 1d. Modify `hackathon_runner/app.py`

Move the setup logic here — this becomes the CLI adapter:

```python
def main(argv=None) -> int:
    # ... version check ...
    args = parse_args(argv)

    root_dir = Path.cwd()
    set_display_root(root_dir)

    dotenv_vars = _load_dotenv_file(root_dir / ".env")
    extra_env = {k: v for k, v in dotenv_vars.items() if k not in os.environ and k.endswith("_API_KEY")}

    teams_csv = resolve_path(args.teams_csv, root_dir)
    input_csv = resolve_path(args.input_csv, root_dir)
    eval_script = resolve_path(args.eval_script, root_dir)

    ensure_file(teams_csv, "teams CSV (--teams-csv)")
    ensure_file(input_csv, "input CSV (--input-csv)")
    ensure_file(eval_script, "eval script (--eval-script)")

    teams = read_teams_csv(teams_csv)
    if args.only_teams.strip():
        teams = [t for t in teams if t.team_id in {x.strip() for x in args.only_teams.split(",") if x.strip()}]

    config = RunConfig(
        run_id=args.run_id,
        teams=teams,
        root_dir=str(root_dir),
        input_csv=str(input_csv),
        work_dir=str(resolve_path(args.work_dir, root_dir) if args.work_dir else root_dir / "work" / args.run_id),
        out_dir=str(resolve_path(args.out_dir, root_dir) if args.out_dir else root_dir / "outputs" / args.run_id),
        eval_script=str(eval_script),
        # ... remaining fields from args ...
        extra_env=extra_env,
    )
    return run(config)
```

The `_load_dotenv_file` helper and `resolve_path` helper move from `orchestrator.py` to `app.py` (they are CLI-specific concerns).

### 1e. Verify CLI still works

Run `python master_eval.py --help` and a dry run to confirm the refactor is transparent.

---

## Step 2 — Database Layer

### 2a. Create `db/models.py`

SQLAlchemy 2.0 declarative models for all 7 tables from the proposal:

- `Team`, `Dataset`, `Job`, `JobTeam`, `TeamRunLog`, `TeamRunMetric`, `TeamRunArtifact`

Plus the `latest_team_results_by_run` view (created via Alembic migration raw SQL).

### 2b. Create `db/session.py`

Reads `DATABASE_URL` from env. Creates engine + `sessionmaker`. Provides `get_db()` dependency for FastAPI.

### 2c. Create Alembic migration

`alembic/versions/001_initial.py` — creates all tables + the Postgres view.

---

## Step 3 — DbStageReporter

### 3a. Add `DbStageReporter` to `hackathon_runner/reporter.py`

```python
class DbStageReporter:
    def __init__(self, job_id: str, db_url: str): ...

    def on_stage_complete(self, team_id, stage, success, log_content):
        # 1. Look up job_team row by (self.job_id, team_id)
        # 2. Update job_team.current_stage
        # 3. INSERT into team_run_logs (job_team_id, stage, success, log_content)

    def on_team_complete(self, team_id, result: TeamReportRow):
        # 1. Update job_team: status, failed_stage, elapsed_s, error, completed_at
        # 2. If result.final_status == "OK": parse metrics file, INSERT into team_run_metrics
        # 3. Store predictions CSV + metrics JSON as team_run_artifacts
```

Uses its own `Session` (created from `db_url` in `__init__`), since it runs in a background thread.

---

## Step 4 — API Service

### 4a. Create `api/auth.py`

Simple `Depends()` that checks `X-Api-Key` header against `ADMIN_API_KEY` env var.

### 4b. Create `api/schemas.py`

Pydantic models for all request/response bodies (mirrors the proposal's JSON examples).

### 4c. Create `api/routers/admin.py`

All admin endpoints from the proposal:

- Teams CRUD + import
- Datasets CRUD
- Jobs: POST (trigger), GET list, GET detail, GET logs, GET metrics
- Runs: GET list, GET detail (uses `latest_team_results_by_run` view), GET report

The `POST /admin/jobs` handler:

1. Validates `team_ids` and `dataset_id`
2. Creates `Job` + `JobTeam` rows (status=PENDING)
3. Builds `RunConfig` from DB state
4. Creates `DbStageReporter(job_id, db_url)`
5. Calls `ThreadJobDispatcher().dispatch(config, reporter)`
6. Returns `{ job_id, run_id }`

### 4d. Create `api/routers/public.py`

- `POST /public/eval`: looks up `is_public_test` dataset, creates job for single team
- `GET /public/jobs/{job_id}`: returns limited response (status, current_stage, stage_statuses, metrics if done)

### 4e. Create `api/main.py`

FastAPI app creation, includes routers, lifespan (DB init).

### 4f. Create `hackathon_runner/dispatcher.py`

```python
class ThreadJobDispatcher:
    def dispatch(self, config, reporter):
        def _worker():
            run(config, reporter)
            # Update job status to COMPLETED/FAILED based on return code
        threading.Thread(target=_worker, daemon=True).start()
```

---

## Step 5 — Dependencies + Entrypoint

### 5a. Update `requirements.txt`

Add: `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2.0`, `alembic`, `psycopg2-binary`, `pydantic>=2.0`

### 5b. Run the API

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Required env vars: `DATABASE_URL`, `ADMIN_API_KEY`

---

## Task Checklist

- [x] Create `hackathon_runner/config.py` with `RunConfig` dataclass
- [x] Create `hackathon_runner/reporter.py` with `StageReporter` protocol + `NullStageReporter`
- [x] Modify `orchestrator.py`: new signature `run(config, reporter)`, add reporter callbacks after each stage
- [x] Modify `app.py`: move setup logic from orchestrator, build `RunConfig` from parsed args
- [x] Verify CLI still works identically after refactor
- [x] Create `db/models.py` with SQLAlchemy models (6 tables — `needs_gpu` removed from teams)
- [x] Create `db/session.py` with engine + session factory
- [x] Create Alembic migrations (`001_initial`, `002_dataset_content_inline`, `003_drop_teams_needs_gpu`)
- [x] Implement `DbStageReporter` in `reporter.py`
- [x] Create `api/auth.py` with `ADMIN_API_KEY` check
- [x] Create `api/schemas.py` with Pydantic request/response models
- [x] Create `api/routers/admin.py` with all admin endpoints
- [x] Create `api/routers/public.py` with public eval + job status endpoints
- [x] Create `api/main.py` FastAPI app with routers + lifespan
- [x] Create `hackathon_runner/dispatcher.py` with `ThreadJobDispatcher`
- [x] Update `requirements.txt` and add API startup instructions
