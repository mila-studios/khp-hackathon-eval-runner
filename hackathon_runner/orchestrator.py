from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import csv
import shlex
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .fs_utils import append_text, ensure_file, mark_status, write_text
from .logging_utils import log, log_context
from .proc import run_cmd
from .team import Team, read_repo_requirements, read_teams_csv, validate_team_scripts
from .util import set_display_root, short_path, ts


def fmt_cmd_template(
    template: str, *, input_csv: Path, pred_csv: Path, metrics_csv: Path, eval_script: Optional[Path]
) -> List[str]:
    values = {
        "input": str(input_csv),
        "pred": str(pred_csv),
        "out": str(metrics_csv),
        "metrics": str(metrics_csv),
        "eval_script": str(eval_script) if eval_script else "",
    }
    rendered = template.format(**values)
    return shlex.split(rendered)


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
    elapsed_s: float


def run(args: argparse.Namespace) -> int:
    root_dir = Path.cwd()
    set_display_root(root_dir)

    def resolve_path(p: Path) -> Path:
        return p if p.is_absolute() else (root_dir / p)

    work_dir = resolve_path(args.work_dir) if args.work_dir else (root_dir / "work" / args.run_id)
    out_dir = resolve_path(args.out_dir) if args.out_dir else (root_dir / "outputs" / args.run_id)

    teams_csv: Path = resolve_path(args.teams_csv)
    input_csv: Path = resolve_path(args.input_csv)
    eval_script: Path = resolve_path(args.eval_script)

    ensure_file(teams_csv, "teams CSV (--teams-csv)")
    ensure_file(input_csv, "input CSV (--input-csv)")

    # eval_script is optional only if eval_cmd doesn't require it.
    if "{eval_script}" in args.eval_cmd:
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
                f"repo_config_paths={args.repo_config_paths}",
                f"clone_timeout={args.clone_timeout}",
                f"configure_timeout={args.configure_timeout}",
                f"predict_timeout={args.predict_timeout}",
                f"eval_timeout={args.eval_timeout}",
                f"eval_script={eval_script}",
                f"eval_cmd={args.eval_cmd}",
                f"max_workers={args.max_workers}",
                f"max_gpu={args.max_gpu}",
                f"max_llm_judge={args.max_llm_judge}",
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

    stage_order = ["clone", "repo_config", "configure", "predict", "validate_predictions", "evaluate"]
    start_idx = stage_order.index(args.start_at)
    stop_idx = stage_order.index(args.stop_after)
    if stop_idx < start_idx:
        raise ValueError(f"--stop-after ({args.stop_after}) must be >= --start-at ({args.start_at})")

    def stage_done(team_out: Path, stage: str) -> bool:
        return (team_out / "status" / f"{stage}.DONE").exists()

    def should_run(team_out: Path, stage: str) -> bool:
        if stage_order.index(stage) < start_idx:
            return False
        if stage_order.index(stage) > stop_idx:
            return False
        if args.resume and stage_done(team_out, stage):
            return False
        return True

    gpu_slots = threading.Semaphore(max(1, args.max_gpu))
    llm_slots = threading.Semaphore(max(1, args.max_llm_judge))

    @contextlib.contextmanager
    def _acquire_slot(*, sem: threading.Semaphore, name: str) -> Iterable[None]:
        with log_context(stage=f"{name}_slot"):
            log(f"Waiting for {name} slot", level="STAGE")
        sem.acquire()
        try:
            with log_context(stage=f"{name}_slot"):
                log(f"Acquired {name} slot", level="SUCCESS")
            yield
        finally:
            sem.release()
            with log_context(stage=f"{name}_slot"):
                log(f"Released {name} slot", level="INFO")

    def _run_team(team: Team) -> TeamReportRow:
        t_start = time.monotonic()
        team_work = work_dir / team.team_id
        repo_dir = team_work / args.repo_subdir
        team_out = out_dir / team.team_id
        team_out.mkdir(parents=True, exist_ok=True)

        # Fresh-run cleanup: only wipe outputs when starting from the beginning and not resuming.
        if not args.resume and args.start_at == "clone":
            for sub in ("status", "logs", "predictions", "metrics"):
                p = team_out / sub
                if p.exists():
                    shutil.rmtree(p)

        with log_context(team=team.team_id):
            with log_context(stage="-"):
                log("=" * 60)
                log(f"TEAM {team.team_id}", level="STAGE")
                log(f"repo={team.git_url}")
                log(f"work_dir={short_path(team_work)}")
                log(f"out_dir={short_path(team_out)}")
                log(
                    f"options resume={args.resume} start_at={args.start_at} stop_after={args.stop_after} "
                    f"skip_clone={args.skip_clone} keep_workdir={args.keep_workdir}"
                )

            write_text(
                team_out / "team_manifest.txt",
                "\n".join([f"team_id={team.team_id}", f"git_url={team.git_url}", f"started_at={ts()}", ""]),
            )

            team_result_ok: bool = False
            team_result_stage: str = "init"
            team_result_hint: str = ""
            team_result_log: Optional[Path] = None
            team_pred_path: Optional[Path] = None
            team_metrics_path: Optional[Path] = None

            def set_team_result(*, ok_flag: bool, stage: str, hint: str = "", log_path: Optional[Path] = None) -> None:
                nonlocal team_result_ok, team_result_stage, team_result_hint, team_result_log
                team_result_ok = ok_flag
                team_result_stage = stage
                team_result_hint = hint
                team_result_log = log_path

            try:
                # Prepare workdir (wipe only for fresh clone runs).
                if (
                    team_work.exists()
                    and not args.keep_workdir
                    and not args.skip_clone
                    and not args.resume
                    and args.start_at == "clone"
                ):
                    shutil.rmtree(team_work)
                team_work.mkdir(parents=True, exist_ok=True)

                repo_config_paths = [x.strip() for x in args.repo_config_paths.split(",") if x.strip()] or ["hackathon.json"]

                # clone
                if should_run(team_out, "clone"):
                    if not args.skip_clone:
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
                            return TeamReportRow(team.team_id, "FAILED", "clone", short_path(clone_log), "", "", time.monotonic() - t_start)
                    else:
                        if not repo_dir.exists():
                            clone_log = team_out / "logs" / "clone.log"
                            write_text(clone_log, f"[{ts()}] ERROR: --skip-clone but repo missing: {repo_dir}\n")
                            mark_status(team_out, "clone", "FAILED")
                            set_team_result(ok_flag=False, stage="clone", log_path=clone_log)
                            return TeamReportRow(team.team_id, "FAILED", "clone", short_path(clone_log), "", "", time.monotonic() - t_start)
                        mark_status(team_out, "clone", "DONE")
                else:
                    # Clone stage skipped (resume or start-at later). Ensure repo exists.
                    if not repo_dir.exists():
                        clone_log = team_out / "logs" / "clone.log"
                        write_text(clone_log, f"[{ts()}] ERROR: repo missing but clone stage skipped: {repo_dir}\n")
                        mark_status(team_out, "clone", "FAILED")
                        set_team_result(ok_flag=False, stage="clone", log_path=clone_log)
                        return TeamReportRow(team.team_id, "FAILED", "clone", short_path(clone_log), "", "", time.monotonic() - t_start)
                    with log_context(stage="clone"):
                        log("SKIPPED (not scheduled)", level="WARN")

                if args.stop_after == "clone":
                    append_text(team_out / "team_manifest.txt", f"finished_at={ts()}\nstatus=OK_STOP_AFTER_clone\n")
                    set_team_result(ok_flag=True, stage="clone", hint="stop_after=clone")
                    return TeamReportRow(team.team_id, "OK_STOP_AFTER_clone", "", "", "", "", time.monotonic() - t_start)

                # repo_config
                with log_context(stage="repo_config"):
                    log(f"Starting (required config: {', '.join(repo_config_paths)})", level="STAGE")
                try:
                    needs_gpu, needs_llm_judge, cfg_src = read_repo_requirements(repo_dir, candidates=repo_config_paths)
                except Exception as e:
                    repo_cfg_log = team_out / "logs" / "repo_config.log"
                    write_text(repo_cfg_log, f"[{ts()}] ERROR: {type(e).__name__}: {e}\n")
                    mark_status(team_out, "repo_config", "FAILED")
                    with log_context(stage="repo_config"):
                        log(f"FAILED: {type(e).__name__}: {e}", level="ERROR")
                        log(f"see {repo_cfg_log}", level="ERROR")
                    set_team_result(ok_flag=False, stage="repo_config", log_path=repo_cfg_log)
                    return TeamReportRow(team.team_id, "FAILED", "repo_config", short_path(repo_cfg_log), "", "", time.monotonic() - t_start)

                script_errors = validate_team_scripts(
                    repo_dir=repo_dir, configure_path=args.configure_path, predict_path=args.predict_path
                )
                if script_errors:
                    repo_cfg_log = team_out / "logs" / "repo_config.log"
                    write_text(repo_cfg_log, f"[{ts()}] ERROR: script contract check failed\n" + "\n".join(script_errors) + "\n")
                    mark_status(team_out, "repo_config", "FAILED")
                    with log_context(stage="repo_config"):
                        log("FAILED: script contract check failed", level="ERROR")
                        for line in script_errors:
                            log(f"- {line}", level="ERROR")
                        log(f"see {repo_cfg_log}", level="ERROR")
                    set_team_result(ok_flag=False, stage="repo_config", log_path=repo_cfg_log)
                    return TeamReportRow(team.team_id, "FAILED", "repo_config", short_path(repo_cfg_log), "", "", time.monotonic() - t_start)

                if needs_gpu and needs_llm_judge:
                    effective_mode = "gpu_llm_judge"
                elif needs_gpu:
                    effective_mode = "gpu"
                elif needs_llm_judge:
                    effective_mode = "llm_judge"
                else:
                    effective_mode = "cpu"

                if should_run(team_out, "repo_config") or not stage_done(team_out, "repo_config"):
                    append_text(
                        team_out / "logs" / "repo_config.log",
                        f"[{ts()}] source={cfg_src} needs_gpu={int(needs_gpu)} needs_llm_judge={int(needs_llm_judge)} mode={effective_mode}\n"
                        f"[{ts()}] configure_script={repo_dir / args.configure_path} executable=1\n"
                        f"[{ts()}] predict_script={repo_dir / args.predict_path} executable=1\n",
                    )
                    mark_status(team_out, "repo_config", "DONE")

                with log_context(stage="repo_config"):
                    log(f"Config OK needs_gpu={int(needs_gpu)} needs_llm_judge={int(needs_llm_judge)} mode={effective_mode}", level="SUCCESS")

                stage_env = {
                    "HACKATHON_NEEDS_GPU": "1" if needs_gpu else "0",
                    "HACKATHON_NEEDS_LLM_JUDGE": "1" if needs_llm_judge else "0",
                    "HACKATHON_MODE": effective_mode,
                }

                # stop-after repo_config
                if args.stop_after == "repo_config":
                    append_text(team_out / "team_manifest.txt", f"finished_at={ts()}\nstatus=OK_STOP_AFTER_repo_config\n")
                    set_team_result(ok_flag=True, stage="repo_config", hint="stop_after=repo_config")
                    return TeamReportRow(team.team_id, "OK_STOP_AFTER_repo_config", "", "", "", "", time.monotonic() - t_start)

                # Artifact paths
                pred_path = team_out / args.pred_filename
                metrics_path = team_out / args.metrics_filename
                team_pred_path = pred_path
                team_metrics_path = metrics_path
                pred_path.parent.mkdir(parents=True, exist_ok=True)
                metrics_path.parent.mkdir(parents=True, exist_ok=True)

                # Decide which slots to acquire for this run.
                need_gpu_slot = needs_gpu and (should_run(team_out, "configure") or should_run(team_out, "predict"))
                need_llm_slot = needs_llm_judge and (should_run(team_out, "predict") or should_run(team_out, "evaluate"))

                gpu_cm = _acquire_slot(sem=gpu_slots, name="gpu") if need_gpu_slot else contextlib.nullcontext()
                llm_cm = _acquire_slot(sem=llm_slots, name="llm_judge") if need_llm_slot else contextlib.nullcontext()

                # Run configure/predict/evaluate under appropriate slots.
                with gpu_cm:
                    # configure
                    if should_run(team_out, "configure"):
                        if not run_cmd(
                            stage="configure",
                            team_out=team_out,
                            cmd=["bash", args.configure_path],
                            cwd=repo_dir,
                            timeout_s=args.configure_timeout,
                            extra_env=stage_env,
                        ):
                            cfg_log = team_out / "logs" / "configure.log"
                            set_team_result(ok_flag=False, stage="configure", log_path=cfg_log)
                            return TeamReportRow(team.team_id, "FAILED", "configure", short_path(cfg_log), "", "", time.monotonic() - t_start)
                    elif args.resume and stage_done(team_out, "configure"):
                        with log_context(stage="configure"):
                            log("SKIPPED (already DONE)", level="WARN")

                    if args.stop_after == "configure":
                        append_text(team_out / "team_manifest.txt", f"finished_at={ts()}\nstatus=OK_STOP_AFTER_configure\n")
                        set_team_result(ok_flag=True, stage="configure", hint="stop_after=configure")
                        return TeamReportRow(team.team_id, "OK_STOP_AFTER_configure", "", "", "", "", time.monotonic() - t_start)

                    with llm_cm:
                        # predict
                        if should_run(team_out, "predict"):
                            if not run_cmd(
                                stage="predict",
                                team_out=team_out,
                                cmd=["bash", args.predict_path, str(input_csv), str(pred_path)],
                                cwd=repo_dir,
                                timeout_s=args.predict_timeout,
                                extra_env=stage_env,
                            ):
                                p_log = team_out / "logs" / "predict.log"
                                set_team_result(ok_flag=False, stage="predict", log_path=p_log)
                                return TeamReportRow(team.team_id, "FAILED", "predict", short_path(p_log), "", "", time.monotonic() - t_start)
                        elif args.resume and stage_done(team_out, "predict"):
                            with log_context(stage="predict"):
                                log("SKIPPED (already DONE)", level="WARN")

                        if args.stop_after == "predict":
                            append_text(team_out / "team_manifest.txt", f"finished_at={ts()}\nstatus=OK_STOP_AFTER_predict\n")
                            set_team_result(ok_flag=True, stage="predict", hint="stop_after=predict")
                            return TeamReportRow(team.team_id, "OK_STOP_AFTER_predict", "", "", "", "", time.monotonic() - t_start)

                        # validate_predictions
                        if should_run(team_out, "validate_predictions"):
                            if not pred_path.exists() or pred_path.stat().st_size == 0:
                                v_log = team_out / "logs" / "validate_predictions.log"
                                write_text(v_log, f"Missing/empty predictions: {pred_path}\n")
                                mark_status(team_out, "validate_predictions", "FAILED")
                                set_team_result(ok_flag=False, stage="validate_predictions", log_path=v_log)
                                return TeamReportRow(
                                    team.team_id, "FAILED", "validate_predictions", short_path(v_log), "", "", time.monotonic() - t_start
                                )
                            mark_status(team_out, "validate_predictions", "DONE")
                        elif args.resume and stage_done(team_out, "validate_predictions"):
                            with log_context(stage="validate_predictions"):
                                log("SKIPPED (already DONE)", level="WARN")

                        if args.stop_after == "validate_predictions":
                            append_text(
                                team_out / "team_manifest.txt",
                                f"finished_at={ts()}\nstatus=OK_STOP_AFTER_validate_predictions\n",
                            )
                            set_team_result(
                                ok_flag=True, stage="validate_predictions", hint="stop_after=validate_predictions"
                            )
                            return TeamReportRow(
                                team.team_id,
                                "OK_STOP_AFTER_validate_predictions",
                                "",
                                "",
                                "",
                                "",
                                time.monotonic() - t_start,
                            )

                        # evaluate
                        eval_cmd = fmt_cmd_template(
                            args.eval_cmd, input_csv=input_csv, pred_csv=pred_path, metrics_csv=metrics_path, eval_script=eval_script
                        )
                        if should_run(team_out, "evaluate"):
                            if not run_cmd(
                                stage="evaluate",
                                team_out=team_out,
                                cmd=eval_cmd,
                                cwd=root_dir,
                                timeout_s=args.eval_timeout,
                                extra_env=stage_env,
                            ):
                                e_log = team_out / "logs" / "evaluate.log"
                                set_team_result(ok_flag=False, stage="evaluate", log_path=e_log)
                                return TeamReportRow(team.team_id, "FAILED", "evaluate", short_path(e_log), "", "", time.monotonic() - t_start)
                        elif args.resume and stage_done(team_out, "evaluate"):
                            with log_context(stage="evaluate"):
                                log("SKIPPED (already DONE)", level="WARN")

                        append_text(team_out / "team_manifest.txt", f"finished_at={ts()}\nstatus=OK\n")
                        set_team_result(ok_flag=True, stage="evaluate")
                        return TeamReportRow(
                            team.team_id,
                            "OK",
                            "",
                            "",
                            short_path(team_pred_path) if team_pred_path else "",
                            short_path(team_metrics_path) if team_metrics_path else "",
                            time.monotonic() - t_start,
                        )
            finally:
                with log_context(stage="summary"):
                    if team_result_ok:
                        extra = f" ({team_result_hint})" if team_result_hint else ""
                        log(f"TEAM RESULT OK stage={team_result_stage}{extra}", level="SUCCESS")
                        if team_pred_path is not None and team_metrics_path is not None:
                            log(
                                f"artifacts pred={short_path(team_pred_path)} metrics={short_path(team_metrics_path)}",
                                level="INFO",
                            )
                    else:
                        where = short_path(team_result_log) if team_result_log else short_path(team_out / "logs")
                        extra = f" ({team_result_hint})" if team_result_hint else ""
                        log(f"TEAM RESULT FAILED stage={team_result_stage}{extra} see={where}", level="ERROR")

    # Run teams concurrently.
    results: Dict[str, TeamReportRow] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as ex:
        future_map: Dict[concurrent.futures.Future[TeamReportRow], str] = {}
        for t in teams:
            future_map[ex.submit(_run_team, t)] = t.team_id

        for fut in concurrent.futures.as_completed(future_map):
            team_id = future_map[fut]
            try:
                row = fut.result()
            except Exception as e:
                # Unexpected crash: capture in report.
                with log_context(team=team_id, stage="summary"):
                    log(f"TEAM RESULT FAILED stage=exception see=console ({type(e).__name__}: {e})", level="ERROR")
                row = TeamReportRow(team_id, "FAILED", "exception", "", "", "", 0.0)
            results[team_id] = row

            if row.final_status.startswith("OK"):
                ok += 1
            else:
                fail += 1

            if fail > 0 and not args.continue_on_failure:
                # Best-effort cancel queued tasks (running tasks cannot be cancelled).
                for f2 in future_map:
                    f2.cancel()
                break

    # Write report.csv
    report_path = out_dir / "report.csv"
    with report_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["team_id", "final_status", "failed_stage", "log_path", "pred_path", "metrics_path", "elapsed_s"])
        for t in teams:
            r = results.get(t.team_id)
            if r is None:
                w.writerow([t.team_id, "CANCELLED", "", "", "", "", ""])
                continue
            w.writerow([r.team_id, r.final_status, r.failed_stage, r.log_path, r.pred_path, r.metrics_path, f"{r.elapsed_s:.3f}"])

    append_text(out_dir / "run_manifest.txt", f"ended_at={ts()}\ntotal={total}\nok={ok}\nfailed={fail}\n")
    write_text(out_dir / "summary.txt", f"ended_at={ts()}\ntotal={total}\nok={ok}\nfailed={fail}\n")
    with log_context(team="-", stage="-"):
        log("=" * 60)
        log(f"RUN COMPLETE total={total} ok={ok} failed={fail}", level=("SUCCESS" if fail == 0 else "ERROR"))
    return 2 if fail > 0 else 0

