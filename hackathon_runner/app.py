from __future__ import annotations

from typing import Optional

from .cli import parse_args
from .orchestrator import run


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    return run(args)

