# Architecture

Three deployment phases — same runner core, progressively richer infrastructure.

---

## Phase 1 — CLI only

The operator runs `master_eval.py` directly. No database, no API. All state lives on the local filesystem.

```mermaid
flowchart LR
    Operator["Operator (shell)"]
    CLI["master_eval.py"]
    RunConfig[RunConfig]
    Runner["hackathon_runner\n(orchestrator)"]
    NullReporter[NullStageReporter]
    FS["Filesystem\noutputs/ work/"]
    TeamRepos["Team Git Repos"]

    Operator -->|"args + teams.csv"| CLI
    CLI --> RunConfig
    RunConfig --> Runner
    Runner --- NullReporter
    Runner -->|"clone"| TeamRepos
    Runner -->|"logs, reports,\npredictions, metrics"| FS
```

**Key traits:**
- Single process, sequential team execution
- `NullStageReporter` — no DB writes, filesystem only
- Input: `teams.csv` + `datasets/*.csv` on disk
- Output: `outputs/<run_id>/` (logs, predictions, metrics, report.csv)

---

## Phase 2 — API + DB (current)

FastAPI wraps the same runner. Jobs execute in background threads. Stage progress is persisted to Postgres via `DbStageReporter`.

```mermaid
flowchart TB
    Admin["Admin Client"]
    Participant["Participant"]
    API["FastAPI App\n(single process)"]
    AdminRoutes["/admin/*"]
    PublicRoutes["/public/*"]
    Dispatcher["ThreadJobDispatcher\n(background thread)"]
    Runner["hackathon_runner\n(orchestrator)"]
    Reporter["DbStageReporter"]
    PG[("Postgres")]
    FS["Filesystem\noutputs/ work/"]
    TeamRepos["Team Git Repos"]

    Admin -->|"X-Api-Key"| AdminRoutes
    Participant -->|"no auth"| PublicRoutes
    AdminRoutes --> API
    PublicRoutes --> API
    API -->|"RunConfig + Reporter"| Dispatcher
    Dispatcher --> Runner
    Runner --- Reporter
    Reporter -->|"stage logs, metrics,\njob status"| PG
    API -->|"CRUD: teams,\ndatasets, jobs"| PG
    Runner -->|"clone"| TeamRepos
    Runner -->|"logs, predictions,\nmetrics"| FS
```

**Key traits:**
- Single FastAPI process, jobs in `threading.Thread`
- `DbStageReporter` writes per-stage progress to Postgres as it happens
- Dataset CSV content stored in Postgres (no file_path dependency)
- Filesystem outputs still written (but DB is the source of truth for status/metrics)

---

## Phase 3 — API + DB + K8S (future)

Only the dispatcher changes: `K8sJobDispatcher` creates a K8S Job pod instead of a local thread. The runner, reporter, DB schema, and API are identical to Phase 2.

```mermaid
flowchart TB
    Admin["Admin Client"]
    Participant["Participant"]
    API["FastAPI App\n(single process)"]
    AdminRoutes["/admin/*"]
    PublicRoutes["/public/*"]
    Dispatcher["K8sJobDispatcher"]
    K8S["K8S Job Pod"]
    Runner["hackathon_runner\n(orchestrator)"]
    Reporter["DbStageReporter"]
    PG[("Postgres")]
    FS["Pod-local Filesystem\n(emptyDir)"]
    TeamRepos["Team Git Repos"]
    Secret["K8S Secret\n(DATABASE_URL)"]

    Admin -->|"X-Api-Key"| AdminRoutes
    Participant -->|"no auth"| PublicRoutes
    AdminRoutes --> API
    PublicRoutes --> API
    API -->|"create K8S Job\nw/ RunConfig JSON"| Dispatcher
    Dispatcher --> K8S
    Secret -->|"env inject"| K8S
    K8S --> Runner
    Runner --- Reporter
    Reporter -->|"stage logs, metrics,\njob status"| PG
    API -->|"CRUD: teams,\ndatasets, jobs"| PG
    Runner -->|"clone"| TeamRepos
    Runner -->|"ephemeral logs"| FS
```

**Key traits:**
- API process no longer runs eval workloads — just dispatches
- Each job gets its own pod (isolates CPU/memory/GPU per team)
- `discover_needs_gpu.py` drives node pool selection (GPU vs CPU) before pod creation
- Pod filesystem is ephemeral (emptyDir) — all durable state is in Postgres
- DB schema and API surface are unchanged from Phase 2

---

## What changes between phases

```mermaid
flowchart LR
    subgraph same ["Unchanged across all phases"]
        RunConfig[RunConfig]
        Orchestrator["hackathon_runner\n(orchestrator)"]
        Stages["clone -> validate_repo ->\nconfigure -> predict ->\nvalidate_predictions -> evaluate"]
    end

    subgraph swapped ["Swapped per phase"]
        Reporter["StageReporter\n(Null / Db)"]
        Dispatcher["JobDispatcher\n(Thread / K8s)"]
    end

    RunConfig --> Orchestrator
    Orchestrator --> Stages
    Reporter -.->|"Phase 1: Null\nPhase 2-3: Db"| Orchestrator
    Dispatcher -.->|"Phase 2: Thread\nPhase 3: K8s"| Orchestrator
```

| Component | Phase 1 (CLI) | Phase 2 (API) | Phase 3 (K8S) |
|---|---|---|---|
| Entry point | `master_eval.py` | `POST /admin/jobs` | `POST /admin/jobs` |
| StageReporter | `NullStageReporter` | `DbStageReporter` | `DbStageReporter` |
| JobDispatcher | n/a | `ThreadJobDispatcher` | `K8sJobDispatcher` |
| Persistence | Filesystem only | Filesystem + Postgres | Postgres (pod FS ephemeral) |
| Runner location | Local process | Background thread | K8S pod |
