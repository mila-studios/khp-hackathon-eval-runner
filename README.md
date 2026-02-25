## Hackathon evaluation runner

This repo contains a Python master runner (`master_eval.py`) that loops over team submissions and executes:

- `git clone` (per team)
- `scripts/configure.sh` (per team repo)
- `scripts/predict.sh <input.csv> <predictions.csv>` (per team repo)
- an evaluator command (default: `python3 eval/evaluate.py --pred ... --out ...`)

It writes per-team logs/artifacts under `outputs/<run_id>/` and produces a run-level `report.csv`.

## Repo layout

- `master_eval.py`: main runner (clone → configure → predict → validate_predictions → evaluate)
- `eval/evaluate.py`: minimal example evaluator (replace/override for your hackathon)
- `inputs/input.csv`: sample input CSV you can use for local testing
- `teams.example.csv`: example team list format
- `team_repo_template/`: a minimal submission template teams can copy

## Team submission contract (required)

Each team repo must contain:

- `hackathon.json` (required): declares resource needs
- `scripts/configure.sh` (required, executable)
- `scripts/predict.sh` (required, executable)

Make scripts executable:

```bash
chmod +x scripts/configure.sh scripts/predict.sh
```

### `hackathon.json` schema

`hackathon.json` must be in the team repo root by default (or you can configure alternate locations via `--repo-config-paths`):

```json
{
  "needs_gpu": false,
  "needs_llm_judge": false
}
```

### Environment variables passed to team scripts

The runner passes these environment variables to `configure.sh` and `predict.sh`:

- `HACKATHON_NEEDS_GPU`: `0` or `1`
- `HACKATHON_NEEDS_LLM_JUDGE`: `0` or `1`
- `HACKATHON_MODE`: `cpu` | `gpu` | `llm_judge` | `gpu_llm_judge`

## Quickstart (runner)

1) Create your `teams.csv`:

```bash
cp teams.example.csv teams.csv
```

2) Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

3) Run:

```bash
python3 master_eval.py \
  --teams-csv teams.csv \
  --input-csv inputs/input.csv
```

If you don’t see colored logs, force-enable colors:

```bash
FORCE_COLOR=1 python3 master_eval.py --help
```

## Key CLI options (most used)

- `--run-id <id>`: run identifier (default: `run_YYYYmmdd_HHMMSS`)
- `--teams-csv <path>`: CSV with headers `team_id,git_url` (default: `teams.csv`, env `TEAM_LIST`)
- `--input-csv <path>`: input CSV passed to `predict.sh` (default: `data/input.csv`, env `INPUT_CSV`)
- `--work-dir <path>`: where repos are cloned (default: `work/<run_id>/`, env `WORK_DIR`)
- `--out-dir <path>`: output root (default: `outputs/<run_id>/`, env `OUT_DIR`)
- `--resume`: skip stages already marked `DONE` under `outputs/<run_id>/<team_id>/status/` (env `RESUME=1`)
- `--start-at <stage>` / `--stop-after <stage>`: stage controls (env `START_AT`, `STOP_AFTER`)
- `--fail-fast`: stop scheduling new teams after the first failure
- `--only-teams team_001,team_005`: run a subset
- `--max-workers N`: max teams in parallel (default 4, env `MAX_WORKERS`)
- `--max-gpu N`: max concurrent GPU teams (`needs_gpu=true`) (default 1, env `MAX_GPU`)
- `--max-llm-judge N`: max concurrent LLM judge teams (`needs_llm_judge=true`) (default 4, env `MAX_LLM_JUDGE`)

Stage names accepted by `--start-at/--stop-after`:

- `clone`
- `repo_config`
- `configure`
- `predict`
- `validate_predictions`
- `evaluate`

## Advanced options / knobs

- **Repo config discovery**
  - `--repo-config-paths "hackathon.json,config/hackathon.json"`: comma-separated relative paths to search within the team repo (env `REPO_CONFIG_PATHS`)

- **Per-team repo layout**
  - `--repo-subdir repo`: where the team repo is cloned under `work/<run_id>/<team_id>/` (default `repo`)

- **Stage scripts**
  - `--configure-path scripts/configure.sh`
  - `--predict-path scripts/predict.sh`

- **Timeouts (seconds)**
  - `--clone-timeout` (env `CLONE_TIMEOUT`, default 600)
  - `--configure-timeout` (env `CONFIGURE_TIMEOUT`, default 600)
  - `--predict-timeout` (env `PREDICT_TIMEOUT`, default 7200)
  - `--eval-timeout` (env `EVAL_TIMEOUT`, default 600)

- **Failure handling**
  - The runner **continues after failures by default**.
  - To stop on first failure, pass `--fail-fast` (or set `CONTINUE_ON_FAILURE=0` in the environment).

- **Clone/workdir controls**
  - `--skip-clone`: assume `work/<run_id>/<team_id>/<repo-subdir>/` already exists; do not run `git clone`
  - `--keep-workdir`: don’t delete existing per-team work dirs on a fresh run

- **Artifact filenames**
  - `--pred-filename predictions/predictions.csv`
  - `--metrics-filename metrics/metrics.csv`

## Evaluator (`--eval-cmd`)

By default the runner executes:

- `--eval-script eval/evaluate.py`
- `--eval-cmd "python3 {eval_script} --pred {pred} --out {out}"`

You can override `--eval-cmd` with a template string. Available placeholders:

- `{eval_script}`: path from `--eval-script` (empty string if not needed)
- `{pred}`: team predictions CSV path under `outputs/<run_id>/<team_id>/...`
- `{out}` / `{metrics}`: metrics CSV path under `outputs/<run_id>/<team_id>/...`
- `{input}`: the input CSV path (same for all teams)

Example (custom evaluator entrypoint):

```bash
python3 master_eval.py \
  --teams-csv teams.csv \
  --input-csv inputs/input.csv \
  --eval-script eval/evaluate.py \
  --eval-cmd "python3 {eval_script} --pred {pred} --out {out} --expected-rows 501"
```

## Outputs

Run-level files:

- `outputs/<run_id>/run_manifest.txt`: resolved paths + options for the run
- `outputs/<run_id>/report.csv`: one row per team
- `outputs/<run_id>/summary.txt`: totals (ok/failed)

`report.csv` schema:

- `team_id`
- `final_status`: `OK`, `FAILED`, `CANCELLED`, or `OK_STOP_AFTER_<stage>`
- `failed_stage`: stage name when `final_status=FAILED`
- `log_path`: failing stage log (when failed)
- `pred_path`: predictions artifact path (when available)
- `metrics_path`: metrics artifact path (when available)
- `elapsed_s`

Per-team folder:

```
outputs/<run_id>/<team_id>/
  logs/<stage>.log
  status/<stage>.DONE|FAILED
  predictions/predictions.csv
  metrics/metrics.csv
  team_manifest.txt
```

Default artifact filenames (overridable):

- predictions: `--pred-filename` (default `predictions/predictions.csv`)
- metrics: `--metrics-filename` (default `metrics/metrics.csv`)

## Resume / rerun workflows

If a team fails at a stage, inspect `outputs/<run_id>/<team_id>/logs/<stage>.log`, fix the issue, then rerun.

Resume and skip already-DONE stages:

```bash
python3 master_eval.py \
  --run-id <run_id> \
  --teams-csv teams.csv \
  --input-csv inputs/input.csv \
  --resume
```

Rerun `predict` (and downstream stages) **without rerunning `configure`**:

- Reuse the same `--run-id` so the already-configured checkout under `work/<run_id>/...` is reused.
- Start at `predict` so earlier stages aren’t scheduled.

```bash
python3 master_eval.py \
  --run-id <run_id> \
  --teams-csv teams.csv \
  --input-csv inputs/input.csv \
  --start-at predict
```

Rerun starting from a specific stage (example: rerun prediction + downstream):

```bash
python3 master_eval.py \
  --run-id <run_id> \
  --teams-csv teams.csv \
  --input-csv inputs/input.csv \
  --start-at predict
```

## Readiness audit (clone + repo config only)

To quickly find submissions that are misconfigured (missing `hackathon.json`, missing scripts, scripts not executable), run:

```bash
python3 master_eval.py \
  --run-id <run_id> \
  --teams-csv teams.csv \
  --input-csv inputs/input.csv \
  --stop-after repo_config
```

