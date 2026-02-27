#!/usr/bin/env python3
"""
Fetch each team's `hackathon.json` without a full clone and extract `needs_gpu`.

This is useful for a K8S "discovery" step where you decide whether a team should
run on a GPU node before scheduling the actual evaluation job.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional


def _run(cmd: list[str], *, cwd: Optional[Path] = None) -> None:
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None)


def _read_teams_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        rows: list[dict[str, str]] = []
        for row in r:
            team_id = (row.get("team_id") or "").strip()
            git_url = (row.get("git_url") or "").strip()
            if not team_id or not git_url:
                continue
            rows.append({"team_id": team_id, "git_url": git_url})
        return rows


def fetch_hackathon_json(
    *,
    repo_url: str,
    hackathon_path: str = "hackathon.json",
    tmp_root: Optional[Path] = None,
) -> str:
    """
    Fetch a single file from a git repo using partial+sparse checkout.

    Returns the file contents as text.
    """
    if tmp_root is None:
        # Prefer working directory to support restricted environments (e.g. some containers/sandboxes)
        # where OS temp dirs may be mounted read-only or blocked by policy.
        tmp_root = Path.cwd() / ".tmp" / "hackathon_discovery"
    tmp_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hackathon_discovery_", dir=str(tmp_root)) as td:
        td_path = Path(td)
        repo_dir = td_path / "repo"
        _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--sparse",
                repo_url,
                str(repo_dir),
            ]
        )
        # `--sparse` uses "cone mode" by default which only supports directory patterns.
        # We want to fetch a single file, so switch to non-cone sparse patterns.
        _run(["git", "-C", str(repo_dir), "sparse-checkout", "init", "--no-cone"])
        # In non-cone mode, git recommends a leading "/" for single-file patterns.
        sparse_pattern = hackathon_path if hackathon_path.startswith("/") else f"/{hackathon_path}"
        _run(["git", "-C", str(repo_dir), "sparse-checkout", "set", "--no-cone", sparse_pattern])
        _run(["git", "-C", str(repo_dir), "checkout", "-q"])
        p = repo_dir / hackathon_path
        return p.read_text(encoding="utf-8", errors="replace")


def parse_needs_gpu(hackathon_json_text: str) -> bool:
    obj = json.loads(hackathon_json_text)
    v = obj.get("needs_gpu", False)
    return bool(v)


def iter_results(
    teams: Iterable[dict[str, str]], *, hackathon_path: str, tmp_root: Optional[Path]
) -> Iterator[Dict[str, Any]]:
    for t in teams:
        team_id = t["team_id"]
        git_url = t["git_url"]
        try:
            txt = fetch_hackathon_json(repo_url=git_url, hackathon_path=hackathon_path, tmp_root=tmp_root)
            needs_gpu = parse_needs_gpu(txt)
            yield {"team_id": team_id, "git_url": git_url, "needs_gpu": needs_gpu, "error": ""}
        except Exception as e:
            yield {
                "team_id": team_id,
                "git_url": git_url,
                "needs_gpu": None,
                "error": f"{type(e).__name__}: {e}",
            }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Discover teams' needs_gpu from hackathon.json (sparse git fetch).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--teams-csv", type=Path, help="CSV with headers team_id,git_url")
    g.add_argument("--repo-url", help="Single repo URL (instead of teams.csv)")
    p.add_argument("--team-id", default="team_001", help="Used only with --repo-url (default: team_001).")
    p.add_argument("--hackathon-path", default="hackathon.json", help="Path in repo (default: hackathon.json).")
    p.add_argument(
        "--tmp-root",
        type=Path,
        default=Path(os.environ.get("HACKATHON_DISCOVERY_TMP_ROOT", "")) if os.environ.get("HACKATHON_DISCOVERY_TMP_ROOT") else None,
        help="Where to create temporary sparse clones (default: ./.tmp/hackathon_discovery).",
    )
    p.add_argument(
        "--format",
        choices=("jsonl", "csv"),
        default="jsonl",
        help="Output format (default: jsonl).",
    )
    args = p.parse_args(argv)

    if args.repo_url:
        teams = [{"team_id": args.team_id, "git_url": args.repo_url}]
    else:
        teams = _read_teams_csv(args.teams_csv)
        if not teams:
            print(f"No teams found in {args.teams_csv}", file=sys.stderr)
            return 2

    results = list(iter_results(teams, hackathon_path=args.hackathon_path, tmp_root=args.tmp_root))

    if args.format == "jsonl":
        for row in results:
            sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
        return 0

    # csv
    w = csv.DictWriter(sys.stdout, fieldnames=["team_id", "git_url", "needs_gpu", "error"])
    w.writeheader()
    for row in results:
        w.writerow(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

