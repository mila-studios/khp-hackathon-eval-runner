from __future__ import annotations

import sys
from typing import Optional

from .cli import parse_args
from .orchestrator import run


def main(argv: Optional[list[str]] = None) -> int:
    if sys.version_info < (3, 12):
        raise SystemExit(
            "ERROR: This runner requires Python 3.12+.\n"
            f"Detected: {sys.version.split()[0]} ({sys.executable})\n"
        )
    args = parse_args(argv)
    return run(args)

