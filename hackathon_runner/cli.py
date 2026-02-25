from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hackathon master evaluation runner (clone -> configure -> predict -> evaluate).")
    p.add_argument("--teams-csv", type=Path, default=Path(os.environ.get("TEAM_LIST", "teams.csv")))
    p.add_argument("--input-csv", type=Path, default=Path(os.environ.get("INPUT_CSV", "data/input.csv")))

    def _env_path(name: str) -> Optional[Path]:
        v = os.environ.get(name)
        return Path(v) if v else None

    p.add_argument("--work-dir", type=Path, default=_env_path("WORK_DIR"))
    p.add_argument("--out-dir", type=Path, default=_env_path("OUT_DIR"))

    p.add_argument("--run-id", default=os.environ.get("RUN_ID") or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    p.add_argument("--configure-timeout", type=int, default=int(os.environ.get("CONFIGURE_TIMEOUT", "600")))
    p.add_argument("--predict-timeout", type=int, default=int(os.environ.get("PREDICT_TIMEOUT", "7200")))
    p.add_argument("--eval-timeout", type=int, default=int(os.environ.get("EVAL_TIMEOUT", "600")))
    p.add_argument("--clone-timeout", type=int, default=int(os.environ.get("CLONE_TIMEOUT", "600")))

    default_continue = os.environ.get("CONTINUE_ON_FAILURE", "1") not in ("0", "false", "False")
    p.set_defaults(continue_on_failure=default_continue)
    failure_group = p.add_mutually_exclusive_group()
    failure_group.add_argument(
        "--continue-on-failure",
        dest="continue_on_failure",
        action="store_true",
        help="Continue running other teams after failures (default). Set CONTINUE_ON_FAILURE=0 to default to fail-fast.",
    )
    failure_group.add_argument(
        "--fail-fast",
        dest="continue_on_failure",
        action="store_false",
        help="Stop scheduling new teams after the first failure.",
    )

    p.add_argument("--repo-subdir", default="repo", help="Subdirectory under each team work dir for the clone.")
    p.add_argument("--configure-path", default="scripts/configure.sh")
    p.add_argument("--predict-path", default="scripts/predict.sh")

    p.add_argument("--eval-script", type=Path, default=Path(os.environ.get("EVAL_SCRIPT", "eval/evaluate.py")))
    p.add_argument(
        "--eval-cmd",
        default=os.environ.get("EVAL_CMD", "python3 {eval_script} --pred {pred} --out {out}"),
        help="Evaluator command template. Placeholders: {eval_script} {pred} {out} {input}.",
    )

    p.add_argument("--pred-filename", default="predictions/predictions.csv")
    p.add_argument("--metrics-filename", default="metrics/metrics.csv")

    p.add_argument("--only-teams", default="", help="Comma-separated team_ids to run (optional).")
    p.add_argument("--skip-clone", action="store_true", help="Assume repo already exists in work-dir; do not run git clone.")
    p.add_argument("--keep-workdir", action="store_true", help="Do not delete existing per-team work dirs.")
    p.add_argument(
        "--start-at",
        default=os.environ.get("START_AT", "clone"),
        choices=["clone", "repo_config", "configure", "predict", "validate_predictions", "evaluate"],
        help="Start execution at this stage (useful to rerun from a failure point).",
    )
    p.add_argument(
        "--stop-after",
        default=os.environ.get("STOP_AFTER", "evaluate"),
        choices=["clone", "repo_config", "configure", "predict", "validate_predictions", "evaluate"],
        help="Stop after this stage completes successfully (useful for readiness audits).",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        default=os.environ.get("RESUME", "") in ("1", "true", "True", "yes", "YES"),
        help="Skip stages that are already marked DONE under outputs/<run-id>/<team-id>/status/.",
    )
    p.add_argument(
        "--repo-config-paths",
        default=os.environ.get("REPO_CONFIG_PATHS", "hackathon.json"),
        help="Comma-separated relative paths (within team repo) to search for required JSON config.",
    )
    p.add_argument("--max-workers", type=int, default=int(os.environ.get("MAX_WORKERS", "4")), help="Max teams to run concurrently.")
    p.add_argument("--max-gpu", type=int, default=int(os.environ.get("MAX_GPU", "1")), help="Max concurrent GPU teams (needs_gpu=true).")
    p.add_argument(
        "--max-llm-judge",
        type=int,
        default=int(os.environ.get("MAX_LLM_JUDGE", "4")),
        help="Max concurrent LLM judge teams (needs_llm_judge=true).",
    )
    return p.parse_args(argv)

