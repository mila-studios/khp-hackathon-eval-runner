# Hackathon Eval Runner — API Service & DB Schema Proposal

## Context

The existing `master_eval.py` CLI orchestrates a pipeline per team:
**clone → validate_repo → configure → predict → validate_predictions → evaluate**, producing a `TeamReportRow` (status, logs, elapsed time) and a `GuardrailMetricsResult` (precision, recall, F1, latency). This proposal wraps that same logic behind an HTTP API backed by Postgres, while keeping the CLI fully functional.

---

## Deployment Phases

The system is designed to evolve through three phases without restructuring the core logic:

| Phase | Runner | Persistence | Status |
|---|---|---|---|
| **1 — CLI** | `python master_eval.py` | Filesystem only (`outputs/`, logs, CSVs) | Today |
| **2 — API + DB** | FastAPI, background threads | Filesystem + Postgres | Next |
| **3 — API + DB + K8S** | FastAPI, K8S Job pods | Filesystem (pod-local) + Postgres | Future |

The DB schema and API surface are **identical in Phase 2 and 3**. The only things that change in Phase 3 are how jobs are dispatched and where the runner process executes.

---

## Guiding Principle

Keep it simple — this starts as a short-term, one-off project. No message brokers, no job queues, no microservices. A single FastAPI process running jobs in background threads (Phase 2) is sufficient. The design is structured so that Phase 3 (K8S) is a targeted swap of three thin abstractions, not a rewrite.

---

## Design Decisions

| Question | Decision |
|---|---|
| Team registry | Stored in DB. `teams.csv` becomes a one-time import tool. |
| Input datasets | Named datasets registered in the DB. Jobs reference one by `dataset_id`. |
| Public test dataset | One dataset flagged `is_public_test = true` in DB. Hardcoded in the public API — participants don't upload anything. |
| Job execution | Background `threading.Thread` per job inside the FastAPI process. |
| DB persistence granularity | Per-stage: `StageReporter.on_stage_complete` writes each stage's log and status to DB as it finishes. |
| Re-runs | Never reset/overwrite. Every execution appends new rows. A Postgres view gives the latest result per team per campaign. |

---

## Concepts: run_id vs job_id

- **`run_id`**: A human-chosen label for a logical evaluation campaign (e.g. `run_final_day1`). Optional at job creation — auto-generated if omitted. Multiple jobs can share one `run_id` (e.g. re-running failed teams after they fix their submission).
- **`job_id`**: A system UUID assigned to each individual `POST /admin/jobs` call. Tracks one specific execution attempt — which teams, which dataset, when.

**Example workflow:**

```
POST /admin/jobs { run_id: "run_final_day1", team_ids: "all", ... }
→ job_id: abc-123 — all 12 teams run; team_003 and team_007 fail

Teams fix their submissions.

POST /admin/jobs { run_id: "run_final_day1", team_ids: ["team_003", "team_007"], ... }
→ job_id: def-456 — only 2 teams re-run

GET /admin/runs/run_final_day1/report
→ consolidated latest result per team (team_003 and team_007 from job def-456, rest from job abc-123)
```

---

## Architecture

### Phase 2 — API + DB (background threads)

```
┌─────────────────┐   X-Api-Key    ┌──────────────────────────────┐
│   Admin Client  │ ─────────────► │                              │
└─────────────────┘                │        FastAPI App           │
                                   │       (single process)       │
┌─────────────────┐   no auth      │                              │
│  Participant    │ ─────────────► │  /admin/*      /public/*     │
└─────────────────┘                └──────────┬───────────────────┘
                                              │
                                   ThreadJobDispatcher
                                   (background thread)
                                              │
                                              ▼
                                   ┌──────────────────┐
                                   │ hackathon_runner  │
                                   │ (Python library)  │
                                   │  DbStageReporter  │ ──► Postgres
                                   └──────────────────┘
```

### Phase 3 — API + DB + K8S (future, no schema change)

```
┌─────────────────┐   X-Api-Key    ┌──────────────────────────────┐
│   Admin Client  │ ─────────────► │                              │
└─────────────────┘                │        FastAPI App           │
                                   │       (single process)       │
┌─────────────────┐   no auth      │                              │
│  Participant    │ ─────────────► │  /admin/*      /public/*     │
└─────────────────┘                └──────────┬───────────────────┘
                                              │
                                   K8sJobDispatcher          ← only this changes
                                   (creates K8S Job pod)
                                              │
                                              ▼
                                   ┌──────────────────────┐
                                   │   K8S Job Pod         │
                                   │   hackathon_runner    │
                                   │   DbStageReporter     │ ──► Postgres
                                   │   (DATABASE_URL from  │
                                   │    K8S Secret)        │
                                   └──────────────────────┘
```

### Three-mode: CLI + API (threads) + API (K8S) with zero duplication

The orchestrator refactor enables all three modes cleanly. Three thin abstractions make Phase 3 a targeted swap, not a rewrite:

**1. `RunConfig` dataclass** — replaces `argparse.Namespace`. Uses plain strings for all paths (not `pathlib.Path`) so it is JSON-serializable and can be embedded in a K8S Job spec in Phase 3.

**2. `StageReporter` protocol** — replaces a raw `Callable`. An object with an `on_stage_complete` method that can be instantiated anywhere — in-process (Phase 2) or inside a K8S pod reading `DATABASE_URL` from a K8S Secret (Phase 3).

**3. `JobDispatcher` protocol** — used only by the API layer. `ThreadJobDispatcher` for Phase 2, `K8sJobDispatcher` for Phase 3. Swap the implementation in Phase 3 without touching anything else.

```
Phase 1 CLI:    parse_args → RunConfig → run(config)
                                         [reporter defaults to NullStageReporter — filesystem only, unchanged behaviour]

Phase 2 API:    POST /admin/jobs → RunConfig → ThreadJobDispatcher
                                               → run(config, DbStageReporter(job_id, db_url))
                                                 [filesystem + Postgres]

Phase 3 K8S:    POST /admin/jobs → RunConfig → K8sJobDispatcher   ← swap here only
                                               → K8S pod: run(config, DbStageReporter(job_id, db_url))
                                                          [filesystem (pod-local) + Postgres]
```

The DB schema, API surface, and all runner logic are **identical across Phase 2 and 3**.

The filesystem outputs (`outputs/<run_id>/`, logs, `report.csv`) continue to work in all modes.

---

## Admin API

All routes require `X-Api-Key: <secret>` header.

### Teams

| Method | Path | Body / Notes |
|---|---|---|
| `POST` | `/admin/teams` | `{ team_id, git_url }` |
| `GET` | `/admin/teams` | List all teams |
| `GET` | `/admin/teams/{team_id}` | Get one team |
| `PUT` | `/admin/teams/{team_id}` | `{ git_url }` |
| `DELETE` | `/admin/teams/{team_id}` | Remove team |
| `POST` | `/admin/teams/import` | Multipart upload of `teams.csv`; bulk upsert |

### Datasets

| Method | Path | Notes |
|---|---|---|
| `POST` | `/admin/datasets` | Multipart upload of CSV + `{ name, description, is_public_test }` |
| `GET` | `/admin/datasets` | List datasets |
| `DELETE` | `/admin/datasets/{dataset_id}` | Remove dataset |

### Jobs

| Method | Path | Notes |
|---|---|---|
| `POST` | `/admin/jobs` | Trigger eval (see body below) |
| `GET` | `/admin/jobs` | List jobs; filterable by `status`, `run_id` |
| `GET` | `/admin/jobs/{job_id}` | Status + per-team results for this specific execution |
| `GET` | `/admin/jobs/{job_id}/teams/{team_id}/logs/{stage}` | Raw log text for a stage (`clone \| validate_repo \| configure \| predict \| validate_predictions \| evaluate`) |
| `GET` | `/admin/jobs/{job_id}/teams/{team_id}/metrics` | Structured metrics (precision, recall, F1, latency) |

**`POST /admin/jobs` body:**

```json
{
  "run_id": "run_final_day1",       // optional — auto-generated if omitted
  "team_ids": ["team_001"] | "all",
  "dataset_id": "<uuid>",
  "fail_fast": false
}
```

**Response:**

```json
{ "job_id": "<uuid>", "run_id": "run_final_day1" }
```

### Runs (campaign view)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/admin/runs` | List distinct `run_id` values with summary counts |
| `GET` | `/admin/runs/{run_id}` | Latest result per team across all jobs under this `run_id` |
| `GET` | `/admin/runs/{run_id}/report` | Download consolidated `report.csv` (latest per team) |

---

## Public API

No authentication required.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/public/eval` | `{ team_id: "team_001" }` — uses `is_public_test` dataset automatically |
| `GET` | `/public/jobs/{job_id}` | Status + result for this job (limited fields — no raw logs, no other teams' data) |

> Participants submit their own `team_id`. They could technically trigger evals for other teams — this is accepted by design.

**`POST /public/eval` response:**

```json
{ "job_id": "<uuid>" }
```

**`GET /public/jobs/{job_id}` response (limited):**

```json
{
  "job_id": "<uuid>",
  "status": "RUNNING",
  "team_id": "team_001",
  "current_stage": "predict",
  "stage_statuses": {
    "clone": "OK",
    "validate_repo": "OK",
    "configure": "OK",
    "predict": "RUNNING",
    "validate_predictions": "PENDING",
    "evaluate": "PENDING"
  },
  "metrics": null
}
```

---

## Database Schema

### `teams`

| Column | Type | Notes |
|---|---|---|
| `team_id` | `VARCHAR` PK | e.g. `"team_001"` |
| `git_url` | `TEXT NOT NULL` | |
| `needs_gpu` | `BOOLEAN` | Nullable; populated at first successful clone |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

### `datasets`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `name` | `VARCHAR UNIQUE` | e.g. `"full_eval_v1"`, `"public_test"` |
| `description` | `TEXT` | |
| `file_path` | `TEXT` | Server-side storage path |
| `row_count` | `INTEGER` | |
| `is_public_test` | `BOOLEAN DEFAULT FALSE` | Only one row should be `TRUE` |
| `created_at` | `TIMESTAMPTZ` | |

### `jobs`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | System-generated per API call |
| `run_id` | `VARCHAR NOT NULL` | Human-chosen campaign label; not unique |
| `status` | `VARCHAR` | `PENDING \| RUNNING \| COMPLETED \| FAILED` |
| `triggered_by` | `VARCHAR` | `admin \| public` |
| `dataset_id` | `UUID` FK → `datasets.id` | |
| `fail_fast` | `BOOLEAN DEFAULT FALSE` | |
| `created_at` | `TIMESTAMPTZ` | |
| `started_at` | `TIMESTAMPTZ` | |
| `completed_at` | `TIMESTAMPTZ` | |

### `job_teams`

One row per `(job, team)` pair. Rows are only ever inserted, never deleted or reset.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `job_id` | `UUID` FK → `jobs.id` | |
| `team_id` | `VARCHAR` FK → `teams.team_id` | |
| `status` | `VARCHAR` | `PENDING \| RUNNING \| OK \| FAILED \| CANCELLED` |
| `current_stage` | `VARCHAR` | Updated live as stages progress (`clone \| validate_repo \| configure \| predict \| validate_predictions \| evaluate`) |
| `failed_stage` | `VARCHAR` | Nullable; name of the stage that failed |
| `elapsed_s` | `FLOAT` | |
| `error` | `TEXT` | |
| `created_at` | `TIMESTAMPTZ` | |
| `completed_at` | `TIMESTAMPTZ` | |

Unique constraint on `(job_id, team_id)`.

### `team_run_logs`

One row per `(job_team, stage)`. Written by `StageReporter.on_stage_complete` immediately when a stage finishes.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `job_team_id` | `UUID` FK → `job_teams.id` | |
| `stage` | `VARCHAR` | `clone \| validate_repo \| configure \| predict \| validate_predictions \| evaluate` |
| `success` | `BOOLEAN` | |
| `log_content` | `TEXT` | Full stdout/stderr for this stage |
| `created_at` | `TIMESTAMPTZ` | |

### `team_run_metrics`

One row per `job_team` (written after the evaluate stage succeeds).

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `job_team_id` | `UUID` FK → `job_teams.id` UNIQUE | |
| `precision` | `FLOAT` | |
| `recall` | `FLOAT` | |
| `f1` | `FLOAT` | |
| `support_harmful` | `INTEGER` | |
| `support_safe` | `INTEGER` | |
| `total_samples` | `INTEGER` | |
| `latency_ms_mean` | `FLOAT` | Nullable |
| `latency_ms_total` | `FLOAT` | Nullable |
| `created_at` | `TIMESTAMPTZ` | |

### `team_run_artifacts`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `job_team_id` | `UUID` FK → `job_teams.id` | |
| `artifact_type` | `VARCHAR` | `predictions \| metrics_csv \| eval_metrics_json` |
| `content` | `TEXT` | Raw CSV/JSON stored inline |
| `created_at` | `TIMESTAMPTZ` | |

---

## Entity Relationships

```
datasets ──── jobs
               │
teams ───── job_teams ──── team_run_logs
                 │
                 ├───────── team_run_metrics
                 │
                 └───────── team_run_artifacts
```

```
teams          1 ──── * job_teams
datasets       1 ──── * jobs
jobs           1 ──── * job_teams
job_teams      1 ──── * team_run_logs
job_teams      1 ──── 1 team_run_metrics
job_teams      1 ──── * team_run_artifacts
```

---

## Campaign View (Postgres View)

The `latest_team_results_by_run` view gives the most recent result per team per `run_id`, regardless of which `job_id` produced it. This backs `GET /admin/runs/{run_id}` and the consolidated report.

```sql
CREATE VIEW latest_team_results_by_run AS
SELECT DISTINCT ON (j.run_id, jt.team_id)
    j.run_id,
    jt.team_id,
    j.id           AS job_id,
    jt.id          AS job_team_id,
    jt.status,
    jt.failed_stage,
    jt.elapsed_s,
    jt.error,
    jt.completed_at
FROM job_teams jt
JOIN jobs j ON j.id = jt.job_id
ORDER BY j.run_id, jt.team_id, jt.completed_at DESC NULLS LAST;
```

---

## Orchestrator Refactor (minimal)

```python
# hackathon_runner/orchestrator.py

@dataclass
class RunConfig:
    run_id: str
    teams: list[Team]
    root_dir: str         # project root — used as cwd for evaluate stage (PYTHONPATH=.)
    input_csv: str        # plain string, not Path — JSON-serializable for K8S Phase 3
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


# hackathon_runner/reporter.py

class StageReporter(Protocol):
    def on_stage_complete(
        self, team_id: str, stage: str, success: bool, log_content: str
    ) -> None: ...

class NullStageReporter:
    """Phase 1 CLI — no-op, filesystem only."""
    def on_stage_complete(self, *args, **kwargs) -> None:
        pass

class DbStageReporter:
    """Phase 2 + 3 — writes to Postgres. Works in-process or inside a K8S pod.
    Receives job_id and looks up job_team_id dynamically from (job_id, team_id)."""
    def __init__(self, job_id: str, db_url: str): ...
    def on_stage_complete(
        self, team_id: str, stage: str, success: bool, log_content: str
    ) -> None: ...


# hackathon_runner/dispatcher.py

class JobDispatcher(Protocol):
    def dispatch(self, config: RunConfig, reporter: StageReporter) -> None: ...

class ThreadJobDispatcher:
    """Phase 2 — runs the orchestrator in a background thread."""
    def dispatch(self, config: RunConfig, reporter: StageReporter) -> None:
        threading.Thread(target=run, args=(config, reporter), daemon=True).start()

class K8sJobDispatcher:
    """Phase 3 — creates a K8S Job with RunConfig serialized to JSON,
    DATABASE_URL injected from a K8S Secret. No other code changes needed."""
    def dispatch(self, config: RunConfig, reporter: StageReporter) -> None: ...


# orchestrator.py signature
def run(config: RunConfig, reporter: StageReporter | None = None) -> int: ...
```

The CLI builds `RunConfig` from `argparse.Namespace` and calls `run(config)` — `reporter` defaults to `NullStageReporter` internally when `None` is passed.
The API builds `RunConfig` from the request, creates a `DbStageReporter`, and hands both to the active `JobDispatcher`.
In Phase 3, only `JobDispatcher` is swapped — everything else is identical.
