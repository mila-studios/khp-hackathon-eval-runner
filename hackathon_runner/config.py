from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .team import Team


@dataclass(frozen=True)
class TeamReportRow:
    """Result of evaluating a single team within a run."""

    team_id: str
    final_status: str  # OK | FAILED | CANCELLED
    failed_stage: str
    log_path: str
    pred_path: str
    metrics_path: str
    error: str
    elapsed_s: float


@dataclass
class RunConfig:
    """Typed configuration for a single evaluation run.

    Built by the CLI adapter (app.py) from argparse, or by the API layer
    from DB state + request parameters.  All paths are plain strings
    (not pathlib.Path) so the object is JSON-serializable for K8S Phase 3.
    """

    run_id: str
    teams: List[Team]
    root_dir: str
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
    extra_env: Dict[str, str] = field(default_factory=dict)
