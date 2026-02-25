#!/usr/bin/env python3
"""
Hackathon master evaluation runner.

This file is intentionally kept as a thin CLI entrypoint so `python3 master_eval.py ...`
continues to work, while the implementation lives in `hackathon_runner/`.
"""

from __future__ import annotations

from hackathon_runner.app import main


if __name__ == "__main__":
    raise SystemExit(main())

