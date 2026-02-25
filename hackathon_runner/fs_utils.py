from __future__ import annotations

import os
from pathlib import Path

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


def mark_status(team_out: Path, stage: str, status: str) -> None:
    status_dir = team_out / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_u = status.upper()
    if status_u not in ("DONE", "FAILED"):
        raise ValueError(f"Unsupported status {status!r}; expected DONE or FAILED")

    # Ensure each stage has only one terminal marker.
    other = "FAILED" if status_u == "DONE" else "DONE"
    other_path = status_dir / f"{stage}.{other}"
    try:
        other_path.unlink(missing_ok=True)
    except TypeError:
        # Python < 3.8 compatibility (missing_ok introduced in 3.8)
        if other_path.exists():
            other_path.unlink()

    (status_dir / f"{stage}.{status_u}").write_text(f"[{ts()}] {stage} {status_u}\n", encoding="utf-8")

