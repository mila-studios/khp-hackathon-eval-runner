from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .cli import parse_args
from .config import RunConfig
from .fs_utils import ensure_file
from .logging_utils import log
from .orchestrator import run
from .team import Team, read_teams_csv
from .util import set_display_root


def _load_dotenv_file(path: Path) -> Dict[str, str]:
    """Minimal .env loader (KEY=VALUE lines).

    - Ignores blank lines and comments (#...)
    - Supports optional ``export KEY=VALUE``
    - Strips single/double quotes around VALUE
    """
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        out[k] = v
    return out


def _resolve_path(p: Path, root: Path) -> Path:
    return p if p.is_absolute() else (root / p)


def _iter_unique_ordered(xs):
    seen = set()
    out: list[str] = []
    for x in xs:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    if sys.version_info < (3, 12):
        raise SystemExit(
            "ERROR: This runner requires Python 3.12+.\n"
            f"Detected: {sys.version.split()[0]} ({sys.executable})\n"
        )

    args = parse_args(argv)

    root_dir = Path.cwd()
    set_display_root(root_dir)

    dotenv_vars = _load_dotenv_file(root_dir / ".env")
    extra_env = {
        k: v
        for k, v in dotenv_vars.items()
        if k not in os.environ and k.endswith("_API_KEY")
    }

    teams_csv = _resolve_path(args.teams_csv, root_dir)
    input_csv = _resolve_path(args.input_csv, root_dir)
    eval_script = _resolve_path(args.eval_script, root_dir)

    ensure_file(teams_csv, "teams CSV (--teams-csv)")
    ensure_file(input_csv, "input CSV (--input-csv)")
    ensure_file(eval_script, "eval script (--eval-script)")

    teams = read_teams_csv(teams_csv)
    if args.only_teams.strip():
        only = _iter_unique_ordered(
            [x.strip() for x in args.only_teams.split(",") if x.strip()]
        )
        wanted = set(only)
        teams = [t for t in teams if t.team_id in wanted]
        log(f"Filtered teams: {len(teams)} selected via --only-teams")

    work_dir = (
        _resolve_path(args.work_dir, root_dir)
        if args.work_dir
        else (root_dir / "work" / args.run_id)
    )
    out_dir = (
        _resolve_path(args.out_dir, root_dir)
        if args.out_dir
        else (root_dir / "outputs" / args.run_id)
    )

    config = RunConfig(
        run_id=args.run_id,
        teams=teams,
        root_dir=str(root_dir),
        input_csv=str(input_csv),
        work_dir=str(work_dir),
        out_dir=str(out_dir),
        eval_script=str(eval_script),
        configure_script=args.configure_script,
        predict_script=args.predict_script,
        clone_timeout=args.clone_timeout,
        configure_timeout=args.configure_timeout,
        predict_timeout=args.predict_timeout,
        eval_timeout=args.eval_timeout,
        pred_filename=args.pred_filename,
        metrics_filename=args.metrics_filename,
        continue_on_failure=args.continue_on_failure,
        extra_env=extra_env,
    )

    return run(config)
