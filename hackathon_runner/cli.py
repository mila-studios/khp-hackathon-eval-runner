from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Hackathon master evaluation runner (clone -> validate_repo -> configure -> predict -> validate_predictions -> evaluate)."
    )
    p.add_argument("--teams-csv", type=Path, default=Path(os.environ.get("TEAM_LIST", "teams.csv")))
    p.add_argument("--input-csv", type=Path, default=Path(os.environ.get("INPUT_CSV", "inputs/input.csv")))

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

    p.add_argument("--eval-script", type=Path, default=Path(os.environ.get("EVAL_SCRIPT", "eval/evaluate.py")))

    p.add_argument("--pred-filename", default="predictions/predictions.csv")
    p.add_argument("--metrics-filename", default="metrics/metrics.csv")

    p.add_argument("--only-teams", default="", help="Comma-separated team_ids to run (optional).")
    return p.parse_args(argv)

