from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .fs_utils import append_text, ensure_file, update_status, write_text
from .logging_utils import log, log_context
from .proc import run_cmd
from .team import Team, read_repo_requirements, read_teams_csv, validate_team_scripts
from .util import set_display_root, short_path, ts


def _iter_unique_ordered(xs: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in xs:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


@dataclass(frozen=True)
class TeamReportRow:
    team_id: str
    final_status: str
    failed_stage: str
    log_path: str
    pred_path: str
    metrics_path: str
    error: str
    elapsed_s: float


def _load_dotenv_file(path: Path) -> Dict[str, str]:
    """
    Minimal .env loader (KEY=VALUE lines).

    - Ignores blank lines and comments (#...)
    - Supports optional `export KEY=VALUE`
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


def _validate_filename_arg(value: str, arg_name: str) -> None:
    p = Path(value)
    if p.is_absolute() or any(part == ".." for part in p.parts):
        raise ValueError(f"--{arg_name} {value!r} must be a relative path with no '..' components")


def run(args: argparse.Namespace) -> int:
    _validate_filename_arg(args.pred_filename, "pred-filename")
    _validate_filename_arg(args.metrics_filename, "metrics-filename")

    configure_path = "project/scripts/configure.sh"
    predict_path = "project/scripts/predict.sh"

    root_dir = Path.cwd()
    set_display_root(root_dir)

    # Load .env (if present) and pass any missing vars into subprocess env.
    # We do NOT overwrite explicitly-set process env vars.
    dotenv_vars = _load_dotenv_file(root_dir / ".env")
    dotenv_extra_env = {
        k: v for (k, v) in dotenv_vars.items()
        if k not in os.environ and k.endswith("_API_KEY")
    }

    def git_head_sha(repo: Path) -> Optional[str]:
        try:
            out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo), stderr=subprocess.STDOUT)
        except (subprocess.CalledProcessError, OSError):
            return None
        sha = out.decode("utf-8", errors="replace").strip()
        return sha or None

    def resolve_path(p: Path) -> Path:
        return p if p.is_absolute() else (root_dir / p)

    work_dir = resolve_path(args.work_dir) if args.work_dir else (root_dir / "work" / args.run_id)
    out_dir = resolve_path(args.out_dir) if args.out_dir else (root_dir / "outputs" / args.run_id)

    teams_csv: Path = resolve_path(args.teams_csv)
    input_csv: Path = resolve_path(args.input_csv)
    eval_script: Path = resolve_path(args.eval_script)

    ensure_file(teams_csv, "teams CSV (--teams-csv)")
    ensure_file(input_csv, "input CSV (--input-csv)")
    ensure_file(eval_script, "eval script (--eval-script)")

    teams = read_teams_csv(teams_csv)
    if args.only_teams.strip():
        only = _iter_unique_ordered([x.strip() for x in args.only_teams.split(",") if x.strip()])
        wanted = set(only)
        teams = [t for t in teams if t.team_id in wanted]
        log(f"Filtered teams: {len(teams)} selected via --only-teams")

    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_text(
        out_dir / "run_manifest.txt",
        "\n".join(
            [
                f"run_id={args.run_id}",
                f"started_at={ts()}",
                f"root_dir={root_dir}",
                f"teams_csv={teams_csv}",
                f"input_csv={input_csv}",
                f"work_dir={work_dir}",
                f"out_dir={out_dir}",
                "repo_config_path=hackathon.json",
                f"clone_timeout={args.clone_timeout}",
                f"configure_timeout={args.configure_timeout}",
                f"predict_timeout={args.predict_timeout}",
                f"eval_timeout={args.eval_timeout}",
                f"eval_script={eval_script}",
                "",
            ]
        ),
    )
    total = len(teams)
    ok = 0
    fail = 0

    if not teams:
        log("No teams to run.")
        return 1

    def _run_team(team: Team) -> TeamReportRow:
        t_start = time.monotonic()
        team_work = work_dir / team.team_id
        repo_dir = team_work / "repo"
        team_out = out_dir / team.team_id
        team_out.mkdir(parents=True, exist_ok=True)

        # Fresh-run cleanup: ensure the per-team output folder is clean.
        for sub in ("logs", "predictions", "metrics"):
            p = team_out / sub
            if p.exists():
                try:
                    shutil.rmtree(p)
                except OSError as e:
                    log(f"WARN: could not remove {p}: {e}", level="WARN")
        status_file = team_out / "status.txt"
        if status_file.exists():
            try:
                status_file.unlink()
            except OSError as e:
                log(f"WARN: could not remove {status_file}: {e}", level="WARN")

        with log_context(team=team.team_id):
            with log_context(stage="-"):
                log("=" * 60)
                log(f"TEAM {team.team_id}", level="STAGE")
                log(f"repo={team.git_url}")
                log(f"work_dir={short_path(team_work)}")
                log(f"out_dir={short_path(team_out)}")

            write_text(
                team_out / "team_manifest.txt",
                "\n".join([f"team_id={team.team_id}", f"git_url={team.git_url}", f"started_at={ts()}", ""]),
            )
            update_status(team_out, overall="RUNNING", last_stage="init", last_stage_status="STARTED")

            team_result_ok: bool = False
            team_result_stage: str = "init"
            team_result_log: Optional[Path] = None
            team_pred_path: Optional[Path] = None
            team_metrics_path: Optional[Path] = None

            def set_team_result(*, ok_flag: bool, stage: str, log_path: Optional[Path] = None) -> None:
                nonlocal team_result_ok, team_result_stage, team_result_log
                team_result_ok = ok_flag
                team_result_stage = stage
                team_result_log = log_path

            try:
                # Prepare workdir (always start from a clean checkout).
                if team_work.exists():
                    try:
                        shutil.rmtree(team_work)
                    except OSError as e:
                        raise RuntimeError(f"Could not clean work directory {team_work}: {e}") from e
                team_work.mkdir(parents=True, exist_ok=True)

                repo_config_paths = ["hackathon.json"]

                # clone
                if not run_cmd(
                    stage="clone",
                    team_out=team_out,
                    cmd=["git", "clone", "--depth", "1", team.git_url, str(repo_dir)],
                    cwd=None,
                    timeout_s=args.clone_timeout,
                    extra_env=None,
                ):
                    clone_log = team_out / "logs" / "clone.log"
                    set_team_result(ok_flag=False, stage="clone", log_path=clone_log)
                    return TeamReportRow(
                        team.team_id, "FAILED", "clone", short_path(clone_log), "", "", "", time.monotonic() - t_start
                    )

                sha = git_head_sha(repo_dir)
                if sha is not None:
                    append_text(team_out / "team_manifest.txt", f"git_commit={sha}\n")
                else:
                    append_text(team_out / "team_manifest.txt", "git_commit=\n")

                # validate_repo
                with log_context(stage="validate_repo"):
                    log(f"Starting (required config: {', '.join(repo_config_paths)})", level="STAGE")
                try:
                    needs_gpu, cfg_src = read_repo_requirements(repo_dir, candidates=repo_config_paths)
                except Exception as e:
                    repo_cfg_log = team_out / "logs" / "validate_repo.log"
                    write_text(repo_cfg_log, f"[{ts()}] ERROR: {type(e).__name__}: {e}\n")
                    update_status(team_out, overall="FAILED", last_stage="validate_repo", last_stage_status="FAILED", failed_stage="validate_repo")
                    with log_context(stage="validate_repo"):
                        log(f"FAILED: {type(e).__name__}: {e}", level="ERROR")
                        log(f"see {repo_cfg_log}", level="ERROR")
                    set_team_result(ok_flag=False, stage="validate_repo", log_path=repo_cfg_log)
                    return TeamReportRow(
                        team.team_id,
                        "FAILED",
                        "validate_repo",
                        short_path(repo_cfg_log),
                        "",
                        "",
                        f"{type(e).__name__}: {e}",
                        time.monotonic() - t_start,
                    )

                script_errors = validate_team_scripts(repo_dir=repo_dir, configure_path=configure_path, predict_path=predict_path)
                if script_errors:
                    repo_cfg_log = team_out / "logs" / "validate_repo.log"
                    write_text(repo_cfg_log, f"[{ts()}] ERROR: script contract check failed\n" + "\n".join(script_errors) + "\n")
                    update_status(team_out, overall="FAILED", last_stage="validate_repo", last_stage_status="FAILED", failed_stage="validate_repo")
                    with log_context(stage="validate_repo"):
                        log("FAILED: script contract check failed", level="ERROR")
                        for line in script_errors:
                            log(f"- {line}", level="ERROR")
                        log(f"see {repo_cfg_log}", level="ERROR")
                    set_team_result(ok_flag=False, stage="validate_repo", log_path=repo_cfg_log)
                    return TeamReportRow(
                        team.team_id,
                        "FAILED",
                        "validate_repo",
                        short_path(repo_cfg_log),
                        "",
                        "",
                        "script contract check failed",
                        time.monotonic() - t_start,
                    )

                if needs_gpu:
                    effective_mode = "gpu"
                else:
                    effective_mode = "cpu"

                append_text(
                    team_out / "logs" / "validate_repo.log",
                    f"[{ts()}] source={cfg_src} needs_gpu={int(needs_gpu)} mode={effective_mode}\n"
                    f"[{ts()}] configure_script={repo_dir / configure_path}\n"
                    f"[{ts()}] predict_script={repo_dir / predict_path}\n",
                )
                update_status(team_out, last_stage="validate_repo", last_stage_status="DONE")

                with log_context(stage="validate_repo"):
                    log(f"Config OK needs_gpu={int(needs_gpu)} mode={effective_mode}", level="SUCCESS")

                stage_env = {
                    **dotenv_extra_env,
                    "HACKATHON_NEEDS_GPU": "1" if needs_gpu else "0",
                    "HACKATHON_MODE": effective_mode,
                }

                # Many team repos install deps into repo-local virtualenvs (commonly ".venv")
                # during configure, but their scripts may still call "python". Prepending the
                # venv bin dir to PATH makes "python" resolve correctly.
                base_path = os.environ.get("PATH", "")
                venv_bin_candidates = [
                    repo_dir / ".venv" / "bin",
                    repo_dir / "venv" / "bin",
                    repo_dir / "project" / ".venv" / "bin",
                ]
                team_exec_env = dict(stage_env)
                # Note: we include these paths even if they don't exist yet; they commonly get
                # created during `configure`, and we want `predict` to pick them up reliably.
                venv_bins = [str(p) for p in venv_bin_candidates]
                team_exec_env["PATH"] = os.pathsep.join(venv_bins + ([base_path] if base_path else []))

                # Artifact paths
                pred_path = team_out / args.pred_filename
                metrics_path = team_out / args.metrics_filename
                team_pred_path = pred_path
                team_metrics_path = metrics_path
                pred_path.parent.mkdir(parents=True, exist_ok=True)
                metrics_path.parent.mkdir(parents=True, exist_ok=True)

                # configure
                if not run_cmd(
                    stage="configure",
                    team_out=team_out,
                    cmd=["bash", configure_path],
                    cwd=repo_dir,
                    timeout_s=args.configure_timeout,
                    extra_env=team_exec_env,
                ):
                    cfg_log = team_out / "logs" / "configure.log"
                    set_team_result(ok_flag=False, stage="configure", log_path=cfg_log)
                    return TeamReportRow(
                        team.team_id, "FAILED", "configure", short_path(cfg_log), "", "", "", time.monotonic() - t_start
                    )

                # predict
                if not run_cmd(
                    stage="predict",
                    team_out=team_out,
                    cmd=["bash", predict_path, str(input_csv), str(pred_path)],
                    cwd=repo_dir,
                    timeout_s=args.predict_timeout,
                    extra_env=team_exec_env,
                ):
                    p_log = team_out / "logs" / "predict.log"
                    set_team_result(ok_flag=False, stage="predict", log_path=p_log)
                    return TeamReportRow(team.team_id, "FAILED", "predict", short_path(p_log), "", "", "", time.monotonic() - t_start)

                # validate_predictions
                if not pred_path.exists() or pred_path.stat().st_size == 0:
                    v_log = team_out / "logs" / "validate_predictions.log"
                    write_text(v_log, f"Missing/empty predictions: {pred_path}\n")
                    update_status(
                        team_out,
                        overall="FAILED",
                        last_stage="validate_predictions",
                        last_stage_status="FAILED",
                        failed_stage="validate_predictions",
                    )
                    set_team_result(ok_flag=False, stage="validate_predictions", log_path=v_log)
                    return TeamReportRow(
                        team.team_id,
                        "FAILED",
                        "validate_predictions",
                        short_path(v_log),
                        "",
                        "",
                        f"Missing/empty predictions: {pred_path}",
                        time.monotonic() - t_start,
                    )
                update_status(team_out, last_stage="validate_predictions", last_stage_status="DONE")

                # evaluate
                if not run_cmd(
                    stage="evaluate",
                    team_out=team_out,
                    cmd=["bash", str(eval_script), str(pred_path), str(metrics_path)],
                    cwd=root_dir,
                    timeout_s=args.eval_timeout,
                    extra_env=stage_env,
                ):
                    e_log = team_out / "logs" / "evaluate.log"
                    set_team_result(ok_flag=False, stage="evaluate", log_path=e_log)
                    return TeamReportRow(team.team_id, "FAILED", "evaluate", short_path(e_log), "", "", "", time.monotonic() - t_start)

                append_text(team_out / "team_manifest.txt", f"finished_at={ts()}\nstatus=OK\n")
                update_status(team_out, overall="OK", last_stage="evaluate", last_stage_status="DONE")
                set_team_result(ok_flag=True, stage="evaluate")
                return TeamReportRow(
                    team.team_id,
                    "OK",
                    "",
                    "",
                    short_path(team_pred_path) if team_pred_path else "",
                    short_path(team_metrics_path) if team_metrics_path else "",
                    "",
                    time.monotonic() - t_start,
                )
            finally:
                with log_context(stage="summary"):
                    if team_result_ok:
                        log(f"TEAM RESULT OK stage={team_result_stage}", level="SUCCESS")
                        if team_pred_path is not None and team_metrics_path is not None:
                            log(
                                f"artifacts pred={short_path(team_pred_path)} metrics={short_path(team_metrics_path)}",
                                level="INFO",
                            )
                    else:
                        where = short_path(team_result_log) if team_result_log else short_path(team_out / "logs")
                        log(f"TEAM RESULT FAILED stage={team_result_stage} see={where}", level="ERROR")

    results: Dict[str, TeamReportRow] = {}
    for t in teams:
        try:
            row = _run_team(t)
        except Exception as e:
            # Unexpected crash: capture in report.
            with log_context(team=t.team_id, stage="summary"):
                log(f"TEAM RESULT FAILED stage=exception see=console ({type(e).__name__}: {e})", level="ERROR")
            row = TeamReportRow(t.team_id, "FAILED", "exception", "", "", "", f"{type(e).__name__}: {e}", 0.0)

        results[t.team_id] = row
        if row.final_status.startswith("OK"):
            ok += 1
        else:
            fail += 1

        if fail > 0 and not args.continue_on_failure:
            break

    # Write report.csv and report.jsonl
    report_path = out_dir / "report.csv"
    report_jsonl_path = out_dir / "report.jsonl"
    with report_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["team_id", "final_status", "failed_stage", "log_path", "pred_path", "metrics_path", "error", "elapsed_s"])
        for t in teams:
            r = results.get(t.team_id)
            if r is None:
                w.writerow([t.team_id, "CANCELLED", "", "", "", "", "", ""])
                continue
            w.writerow([r.team_id, r.final_status, r.failed_stage, r.log_path, r.pred_path, r.metrics_path, r.error, f"{r.elapsed_s:.3f}"])

    with report_jsonl_path.open("w", encoding="utf-8") as f:
        for t in teams:
            r = results.get(t.team_id)
            if r is None:
                obj = {
                    "team_id": t.team_id,
                    "final_status": "CANCELLED",
                    "failed_stage": "",
                    "log_path": "",
                    "pred_path": "",
                    "metrics_path": "",
                    "error": "",
                    "elapsed_s": None,
                }
            else:
                obj = {
                    "team_id": r.team_id,
                    "final_status": r.final_status,
                    "failed_stage": r.failed_stage,
                    "log_path": r.log_path,
                    "pred_path": r.pred_path,
                    "metrics_path": r.metrics_path,
                    "error": r.error,
                    "elapsed_s": round(r.elapsed_s, 3),
                }
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    append_text(out_dir / "run_manifest.txt", f"ended_at={ts()}\ntotal={total}\nok={ok}\nfailed={fail}\n")
    write_text(out_dir / "summary.txt", f"ended_at={ts()}\ntotal={total}\nok={ok}\nfailed={fail}\n")
    with log_context(team="-", stage="-"):
        log("=" * 60)
        log(f"RUN COMPLETE total={total} ok={ok} failed={fail}", level=("SUCCESS" if fail == 0 else "ERROR"))
    return 2 if fail > 0 else 0

