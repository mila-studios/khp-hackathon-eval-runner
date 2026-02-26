## Hackathon evaluation runner

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
- `datasets/sample_guardrail_data.csv`: sample input CSV you can use for local testing
- `teams.example.csv`: example team list format
- `.env.example`: runner config template (copy to `.env`)

## Team submission contract (required)

Each team repo must contain:

- `hackathon.json` (required): declares resource needs
- `project/scripts/configure.sh` (required)
- `project/scripts/predict.sh` (required)

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

It also forwards environment variables from your shell, and (if present) loads `.env` from the runner repo root and forwards those values too (without overriding already-exported variables). This is how secrets like `OPENAI_API_KEY` are made available to team code.

## Quickstart (runner)

1) Create your `teams.csv`:

```bash
cp teams.example.csv teams.csv
```

2) (Optional) Create a `.env` file for configuration/secrets:

```bash
cp .env.example .env
```

3) Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

4) Run:

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
  - `project/scripts/configure.sh` (required)
  - `project/scripts/predict.sh` (required)
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

