from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class Team:
    team_id: str
    git_url: str


def read_teams_csv(path: Path) -> List[Team]:
    teams: List[Team] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [h for h in ("team_id", "git_url") if h not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"teams.csv missing headers: {missing}. Found: {reader.fieldnames}")
        for row in reader:
            team_id = (row.get("team_id") or "").strip()
            git_url = (row.get("git_url") or "").strip()
            if not team_id or not git_url:
                continue
            teams.append(Team(team_id=team_id, git_url=git_url))
    return teams


def _load_json_file(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        # Make the failing file path obvious in logs.
        raise ValueError(f"Invalid JSON in {path}: {e.msg} (line {e.lineno} col {e.colno})") from e
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}, got {type(data).__name__}")
    return data


def _as_bool(v: Any, *, key: str, path: Path) -> bool:
    if isinstance(v, bool):
        return v
    raise ValueError(f"{path}: {key} must be a boolean (true/false). Got {type(v).__name__}")


def read_repo_requirements(repo_dir: Path, *, candidates: List[str]) -> Tuple[bool, str]:
    """
    Returns (needs_gpu, source_path).
    Raises if no candidate config file exists or if it is invalid.
    """
    for rel in candidates:
        p = repo_dir / rel
        if not p.exists():
            continue
        cfg = _load_json_file(p)
        if "needs_gpu" not in cfg:
            raise ValueError(f"{p}: missing required key needs_gpu")
        needs_gpu = _as_bool(cfg.get("needs_gpu"), key="needs_gpu", path=p)
        return needs_gpu, str(p)

    raise FileNotFoundError(
        f"Missing required repo config. Looked for: {', '.join(candidates)} (relative to {repo_dir})"
    )


def validate_team_scripts(*, repo_dir: Path, configure_path: str, predict_path: str) -> List[str]:
    """
    Returns a list of human-readable contract errors (empty list = OK).
    """
    configure_sh = repo_dir / configure_path
    predict_sh = repo_dir / predict_path
    errs: List[str] = []
    if not configure_sh.exists():
        errs.append(f"Missing script: {configure_path}")
    if not predict_sh.exists():
        errs.append(f"Missing script: {predict_path}")
    return errs

