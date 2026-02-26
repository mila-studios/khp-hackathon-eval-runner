from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from .util import ts


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(content)


def ensure_file(path: Path, desc: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing {desc}: {path}")


def is_executable_file(path: Path) -> bool:
    return path.exists() and os.access(str(path), os.X_OK)


def _read_status_kv(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        data[k] = v
    return data


def update_status(
    team_out: Path,
    *,
    overall: Optional[str] = None,
    last_stage: Optional[str] = None,
    last_stage_status: Optional[str] = None,
    failed_stage: Optional[str] = None,
) -> None:
    """
    Updates `outputs/<run_id>/<team_id>/status.txt`.

    This is a lightweight, human-readable state marker (not used for control flow).
    """
    path = team_out / "status.txt"
    data = _read_status_kv(path)
    if overall is not None:
        data["overall"] = overall
    if last_stage is not None:
        data["last_stage"] = last_stage
    if last_stage_status is not None:
        data["last_stage_status"] = last_stage_status
    if failed_stage is not None:
        data["failed_stage"] = failed_stage
    data["updated_at"] = ts()

    # Stable order for readability.
    order = ["overall", "last_stage", "last_stage_status", "failed_stage", "updated_at"]
    lines = [f"{k}={data[k]}" for k in order if k in data and data[k] != ""]
    for k in sorted(set(data.keys()) - set(order)):
        if data[k] != "":
            lines.append(f"{k}={data[k]}")
    write_text(path, "\n".join(lines) + "\n")

