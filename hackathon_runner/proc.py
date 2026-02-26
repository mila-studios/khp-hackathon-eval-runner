from __future__ import annotations

import os
import shlex
import signal
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Dict, List, Optional

from .fs_utils import update_status
from .logging_utils import log, log_context
from .util import fmt_elapsed, short_arg, ts


def _terminate_process_group(proc: subprocess.Popen[bytes], *, logfile: Path) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        pgid = None

    def _log(s: str) -> None:
        with logfile.open("ab") as f:
            f.write(s.encode("utf-8"))

    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGTERM)
            _log(f"\n[{ts()}] Sent SIGTERM to process group {pgid}\n")
        except Exception as e:
            _log(f"\n[{ts()}] WARN: Failed SIGTERM to pgid {pgid}: {type(e).__name__}: {e}\n")
    else:
        try:
            proc.terminate()
            _log(f"\n[{ts()}] Sent SIGTERM to pid {proc.pid}\n")
        except Exception as e:
            _log(f"\n[{ts()}] WARN: Failed terminate pid {proc.pid}: {type(e).__name__}: {e}\n")

    try:
        proc.wait(timeout=15)
        return
    except Exception:
        pass

    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
            _log(f"\n[{ts()}] Sent SIGKILL to process group {pgid}\n")
        except Exception as e:
            _log(f"\n[{ts()}] WARN: Failed SIGKILL to pgid {pgid}: {type(e).__name__}: {e}\n")
    else:
        try:
            proc.kill()
            _log(f"\n[{ts()}] Sent SIGKILL to pid {proc.pid}\n")
        except Exception as e:
            _log(f"\n[{ts()}] WARN: Failed kill pid {proc.pid}: {type(e).__name__}: {e}\n")

    try:
        proc.wait(timeout=10)
    except Exception:
        _log(f"\n[{ts()}] WARN: Process did not exit after SIGKILL\n")


def run_cmd(
    *,
    stage: str,
    team_out: Path,
    cmd: List[str],
    cwd: Optional[Path],
    timeout_s: int,
    extra_env: Optional[Dict[str, str]] = None,
) -> bool:
    logs_dir = team_out / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = logs_dir / f"{stage}.log"

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    disp_cmd = [short_arg(x) for x in cmd]
    cmd_str = " ".join(shlex.quote(x) for x in cmd)
    cmd_disp_str = " ".join(shlex.quote(x) for x in disp_cmd)
    with log_context(stage=stage):
        log(f"Starting (timeout={timeout_s}s)", level="STAGE")
        wrapped = textwrap.wrap(cmd_disp_str, width=120) or [cmd_disp_str]
        log(f"cmd={wrapped[0]}", level="STAGE")
        for cont in wrapped[1:]:
            log(f"    {cont}", level="STAGE")

    with logfile.open("wb") as f:
        f.write(f"[{ts()}] cwd={cwd}\n".encode("utf-8"))
        f.write(f"[{ts()}] cmd={cmd_str}\n".encode("utf-8"))

    try:
        update_status(team_out, overall="RUNNING", last_stage=stage, last_stage_status="STARTED")
        t0 = time.monotonic()
        with logfile.open("ab") as f:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # lets us kill spawned children on timeout
            )
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                with logfile.open("ab") as ff:
                    ff.write(f"\n[{ts()}] ERROR: Timeout after {timeout_s}s\n".encode("utf-8"))
                _terminate_process_group(proc, logfile=logfile)
                update_status(team_out, overall="FAILED", last_stage=stage, last_stage_status="FAILED", failed_stage=stage)
                with log_context(stage=stage):
                    log(f"FAILED (timeout after {timeout_s}s). See {logfile}", level="ERROR")
                return False

        if rc == 0:
            update_status(team_out, last_stage=stage, last_stage_status="DONE")
            elapsed = time.monotonic() - t0
            with log_context(stage=stage):
                log(f"DONE (elapsed={fmt_elapsed(elapsed)})", level="SUCCESS")
            return True

        with logfile.open("ab") as f:
            f.write(f"\n[{ts()}] ERROR: Exit code {rc}\n".encode("utf-8"))
        update_status(team_out, overall="FAILED", last_stage=stage, last_stage_status="FAILED", failed_stage=stage)
        with log_context(stage=stage):
            log(f"FAILED (exit={rc}). See {logfile}", level="ERROR")
        return False
    except Exception as e:
        with logfile.open("ab") as f:
            f.write(f"\n[{ts()}] ERROR: {type(e).__name__}: {e}\n".encode("utf-8"))
        update_status(team_out, overall="FAILED", last_stage=stage, last_stage_status="FAILED", failed_stage=stage)
        with log_context(stage=stage):
            log(f"FAILED ({type(e).__name__}: {e}). See {logfile}", level="ERROR")
        return False

