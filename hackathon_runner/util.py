from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

_DISPLAY_ROOT: Path = Path.cwd()


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def set_display_root(root: Path) -> None:
    global _DISPLAY_ROOT
    _DISPLAY_ROOT = root


def short_path(p: Path) -> str:
    try:
        return str(p.relative_to(_DISPLAY_ROOT))
    except Exception:
        return str(p)


def short_arg(a: str) -> str:
    # Best-effort shortening of absolute paths under the runner root.
    try:
        if os.path.isabs(a):
            return short_path(Path(a))
    except Exception:
        pass
    return a


def fmt_elapsed(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 10:
        return f"{seconds:.2f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds - (m * 60)
    return f"{m}m{s:04.1f}s"

