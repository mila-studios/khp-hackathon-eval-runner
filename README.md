## Hackathon evaluation runner

## Requirements

- Python **3.12+** (runner)

This repo contains a Python master runner (`master_eval.py`) that loops over team submissions and executes:

- `git clone` (per team)
- read `hackathon.json` + validate required scripts (per team repo)
- `project/scripts/configure.sh` (per team repo)
- `project/scripts/predict.sh <input.csv> <predictions.csv>` (per team repo)
- validate that predictions were produced (per team)
- an evaluator script (default: `bash scripts/evaluate.sh <predictions.csv> <metrics.csv>`)

It writes per-team logs/artifacts under `outputs/<run_id>/` and produces a run-level `report.csv`.

## Repo layout

- `master_eval.py`: main runner (clone → validate_repo → configure → predict → validate_predictions → evaluate)
- `scripts/evaluate.sh`: default evaluator (calls `python -m src.guardrails.get_guardrail_metrics`)
- `scripts/discover_needs_gpu.py`: helper to fetch only `hackathon.json` from git and extract `needs_gpu` (for K8S-style discovery)
- `datasets/sample_guardrail_data.csv`: sample input CSV you can use for local testing
- `teams.example.csv`: example team list format
- `.env.example`: runner config template (copy to `.env`)

## Team submission contract (required)

Each team repo must contain:

- `hackathon.json` (required): declares resource needs
- `project/scripts/configure.sh` (required, path overridable via `--configure-script`)
- `project/scripts/predict.sh` (required, path overridable via `--predict-script`)

### `hackathon.json` schema

`hackathon.json` must be in the team repo root:

```json
{
  "needs_gpu": false
}
```

### Environment variables passed to team scripts

The runner passes these environment variables to team scripts (`configure.sh`, `predict.sh`, and the evaluator):

- `HACKATHON_NEEDS_GPU`: `0` or `1`
- `HACKATHON_MODE`: `cpu` | `gpu`

If a `.env` file is present in the runner repo root, the runner loads it and forwards any variables whose names end in `_API_KEY` (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) to team scripts, without overriding variables already exported in your shell. No other variables from `.env` or the runner's environment are forwarded to team scripts.

Note: `needs_gpu` is currently used only to set `HACKATHON_NEEDS_GPU` / `HACKATHON_MODE` (and to log the selected mode). The runner does not enforce GPU usage on its own — this is intended to be used by an external scheduler (e.g. a future K8S “discovery → schedule” flow).

## Quickstart (runner)

1. Create your `teams.csv`:

```bash
cp teams.example.csv teams.csv
```

1. (Optional) Create a `.env` file for configuration/secrets:

```bash
cp .env.example .env
```

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

1. Run:

```bash
python3 master_eval.py \
  --teams-csv teams.csv \
  --input-csv datasets/sample_guardrail_data.csv
```

If you don’t see colored logs, force-enable colors:

```bash
FORCE_COLOR=1 python3 master_eval.py --help
```

## Key CLI options (most used)

- `--run-id <id>`: run identifier (default: `run_YYYYmmdd_HHMMSS`)
- `--teams-csv <path>`: CSV with headers `team_id,git_url` (default: `teams.csv`, env `TEAM_LIST`)
- `--input-csv <path>`: input CSV passed to `predict.sh` (default: `datasets/input.csv`, env `INPUT_CSV`)
- `--work-dir <path>`: where repos are cloned (default: `work/<run_id>/`, env `WORK_DIR`)
- `--out-dir <path>`: output root (default: `outputs/<run_id>/`, env `OUT_DIR`)
- `--fail-fast`: stop scheduling new teams after the first failure
- `--only-teams team_001,team_005`: run a subset

## Advanced options / knobs

- **Per-team repo layout**
  - The runner clones each team repo under `work/<run_id>/<team_id>/repo/`
- **Stage scripts**
  - `--configure-script` (env `CONFIGURE_SCRIPT`, default `project/scripts/configure.sh`)
  - `--predict-script` (env `PREDICT_SCRIPT`, default `project/scripts/predict.sh`)
  - Note: for `configure`/`predict`, the runner will prepend common repo-local venv locations (like `repo/.venv/bin`) to `PATH` if they exist, so a team script calling `python` typically uses the venv created during `configure`.
- **Timeouts (seconds)**
  - `--clone-timeout` (env `CLONE_TIMEOUT`, default 600)
  - `--configure-timeout` (env `CONFIGURE_TIMEOUT`, default 600)
  - `--predict-timeout` (env `PREDICT_TIMEOUT`, default 7200)
  - `--eval-timeout` (env `EVAL_TIMEOUT`, default 600)
- **Failure handling**
  - The runner **continues after failures by default**.
  - To stop on first failure, pass `--fail-fast` (or set `CONTINUE_ON_FAILURE=0` in the environment).
- **Clone/workdir controls**
  - The runner clones each team repo fresh for each run.
- **Artifact filenames**
  - `--pred-filename predictions/predictions.csv`
  - `--metrics-filename metrics/metrics.csv`

## Evaluator (`--eval-script`)

The runner executes:

- `bash <eval-script> <predictions.csv> <metrics.csv>`

Example (custom evaluator entrypoint):

```bash
python3 master_eval.py \
  --teams-csv teams.csv \
  --input-csv datasets/sample_guardrail_data.csv \
  --eval-script scripts/evaluate.sh
```

## Preflight discovery (K8S: decide GPU vs CPU)

If you want to decide which node pool to schedule a team on *before* running the full evaluation, you can fetch only `hackathon.json` from git (no full clone) and extract `needs_gpu`:

```bash
python3 scripts/discover_needs_gpu.py --teams-csv teams.csv
```

This prints JSONL like:

- `{"team_id":"team_001","git_url":"...","needs_gpu":false,"error":""}`

## Outputs

Run-level files:

- `outputs/<run_id>/run_manifest.txt`: resolved paths + options for the run
- `outputs/<run_id>/report.csv`: one row per team
- `outputs/<run_id>/report.jsonl`: one JSON object per team
- `outputs/<run_id>/summary.txt`: totals (ok/failed)

`report.csv` schema:

- `team_id`
- `final_status`: `OK`, `FAILED`, or `CANCELLED`
- `failed_stage`: stage name when `final_status=FAILED`
- `log_path`: failing stage log (when failed)
- `pred_path`: predictions artifact path (when available)
- `metrics_path`: metrics artifact path (when available)
- `error`: short error message (when available)
- `elapsed_s`

Per-team folder:

```
outputs/<run_id>/<team_id>/
  logs/<stage>.log
  status.txt
  predictions/predictions.csv
  metrics/metrics.csv
  team_manifest.txt
```

Default artifact filenames (overridable):

- predictions: `--pred-filename` (default `predictions/predictions.csv`)
- metrics: `--metrics-filename` (default `metrics/metrics.csv`)

## Rerun workflow

If a team fails, inspect `outputs/<run_id>/<team_id>/logs/<stage>.log`, fix the issue, then rerun the same command (optionally narrowing scope with `--only-teams`).

---

## API Service (Phase 2)

The same evaluation pipeline is also available via an HTTP API backed by Postgres. The CLI continues to work as before — both modes share the same runner code.

See [API_SERVICE_PROPOSAL.md](docs/API_SERVICE_PROPOSAL.md) for the full design and DB schema.
See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for Phase 1/2/3 architecture diagrams.
See [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the Phase 2 implementation checklist.

### Option A: Docker Compose (recommended)

Runs Postgres + API in containers. No local Python install needed.

```bash
# Start everything (builds image, starts Postgres + API)
docker compose up --build

# Or run in background
docker compose up --build -d

# View logs
docker compose logs -f api

# Stop
docker compose down

# Stop and wipe the database
docker compose down -v
```

Set environment variables before starting. You can use the `.hackathon_env` template:

```bash
cp .hackathon_env.example .hackathon_env   # edit with your values
source .hackathon_env
docker compose up --build
```

Or pass them inline:

```bash
ADMIN_API_KEY=your-secret-key GIT_TOKEN=github_pat_xxx docker compose up --build
```

DB migrations run automatically on startup. The API is available at `http://localhost:8000`.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | Postgres connection string (set automatically by Docker Compose) |
| `ADMIN_API_KEY` | Yes | Protects admin API + dashboard login |
| `GIT_TOKEN` | For private repos | GitHub PAT — injected into HTTPS clone URLs at runtime |
| `CLONE_TIMEOUT` | No (default: 600) | Git clone timeout in seconds |
| `CONFIGURE_TIMEOUT` | No (default: 600) | Configure stage timeout in seconds |
| `PREDICT_TIMEOUT` | No (default: 7200) | Predict stage timeout in seconds |
| `EVAL_TIMEOUT` | No (default: 600) | Evaluate stage timeout in seconds |
| `PUBLIC_EVAL_COOLDOWN` | No (default: 900) | Cooldown between public self-service evals per team (seconds) |
| `EVAL_SCRIPT` | No | Path to evaluator script (default: `scripts/evaluate.sh`) |
| `CONFIGURE_SCRIPT` | No | Relative path to team configure script (default: `project/scripts/configure.sh`) |
| `PREDICT_SCRIPT` | No | Relative path to team predict script (default: `project/scripts/predict.sh`) |
| `*_API_KEY` | No | Any env var ending in `_API_KEY` (e.g. `OPENAI_API_KEY`) is forwarded to team scripts |

**Note:** `DATABASE_URL`, `ADMIN_API_KEY`, and `GIT_TOKEN` should be set via shell exports or `.hackathon_env`, **not** in `.env` — the `.env` file is loaded by the runner and `*_API_KEY` variables are forwarded to team scripts.

### Option B: Local Python + Docker Postgres

If you prefer running Python locally (e.g., for development):

**Prerequisites:**
- Docker (for Postgres)
- Python 3.12+ with all dependencies installed (`pip install -r requirements.txt`)

**1. Start Postgres**

```bash
docker run -d \
  --name hackathon-pg \
  -e POSTGRES_USER=hackathon \
  -e POSTGRES_PASSWORD=hackathon \
  -e POSTGRES_DB=hackathon_eval \
  -p 5432:5432 \
  postgres:16
```

**2. Set environment variables**

```bash
export DATABASE_URL="postgresql://hackathon:hackathon@localhost:5432/hackathon_eval"
export ADMIN_API_KEY="your-secret-key"
```

**3. Start the API server**

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

DB migrations run automatically on startup. The API docs are available at `http://localhost:8000/docs` (Swagger UI).

### Web Dashboards

**Admin Dashboard** — `http://localhost:8000/admin/dashboard/`

Requires the `ADMIN_API_KEY` to log in. Provides a web UI to:

- Import and manage teams (CSV upload)
- Upload and manage datasets
- Trigger evaluation jobs (with multi-team selection)
- Monitor job progress in real time (auto-refreshing stage chips)
- Cancel running or pending jobs (current stage finishes, no new stages/teams start)
- View per-stage logs inline
- Browse runs: consolidated view across all jobs sharing a run ID (latest result per team)
- Control public leaderboard settings (mode + official run)

**Public Board** — `http://localhost:8000/public/dashboard/`

No authentication required. Teams can:

- Enter their team ID to view their evaluation history, dataset used, and latest score
- View per-stage logs (including evaluate) for each evaluation
- Trigger a self-service eval against the public dataset (with rate limiting: one concurrent eval per team, 15-min cooldown)
- View the leaderboard (when enabled by the admin)

The leaderboard has three modes, toggled from the admin dashboard:

- **off** — "Coming Soon" placeholder
- **own_only** — teams can only see their own score
- **full** — ranked leaderboard with all teams from the official run

When set to **full**, the leaderboard consolidates results across all jobs sharing the official run ID (latest result per team). Rankings are ordered by F1 score (descending), with latency (ms/sample, ascending) as a tiebreaker. Teams with in-progress or failed evaluations are listed below the ranked section.

### API overview

**Admin API** (`X-Api-Key` header required):


| Method   | Path                          | Description                                          |
| -------- | ----------------------------- | ---------------------------------------------------- |
| `POST`   | `/admin/teams`                | Create a team                                        |
| `GET`    | `/admin/teams`                | List teams                                           |
| `POST`   | `/admin/teams/import`         | Bulk import from `teams.csv`                         |
| `POST`   | `/admin/datasets`             | Upload an input dataset                              |
| `GET`    | `/admin/datasets`             | List datasets                                        |
| `DELETE` | `/admin/datasets/{id}`        | Delete a dataset (blocked if referenced by jobs)     |
| `POST`   | `/admin/jobs`                 | Trigger an eval job                                  |
| `GET`    | `/admin/jobs`                 | List jobs (filterable by `status`, `run_id`)         |
| `GET`    | `/admin/jobs/{job_id}`        | Job detail + per-team status                         |
| `POST`   | `/admin/jobs/{job_id}/cancel` | Cancel a pending or running job                      |
| `GET`    | `/admin/runs`                 | List campaigns                                       |
| `GET`    | `/admin/runs/{run_id}`        | Latest result per team (consolidated across re-runs) |
| `GET`    | `/admin/runs/{run_id}/report` | Download consolidated `report.csv`                   |


**Public API** (no auth):


| Method | Path                    | Description                                        |
| ------ | ----------------------- | -------------------------------------------------- |
| `POST` | `/public/eval`          | Trigger eval for a team (uses public test dataset) |
| `GET`  | `/public/jobs/{job_id}` | Job status with stage-by-stage progress            |


### Stopping / resetting

```bash
# Stop the API server: Ctrl+C

# Stop and remove Postgres (data is lost):
docker rm -f hackathon-pg

# Or stop without removing (data persists):
docker stop hackathon-pg
# Restart later: docker start hackathon-pg
```

