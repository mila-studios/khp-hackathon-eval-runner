from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import sys
from typing import Any, Iterable, Optional

_CTX_TEAM: contextvars.ContextVar[str] = contextvars.ContextVar("team", default="-")
_CTX_STAGE: contextvars.ContextVar[str] = contextvars.ContextVar("stage", default="-")


@contextlib.contextmanager
def log_context(*, team: Optional[str] = None, stage: Optional[str] = None) -> Iterable[None]:
    team_token = None
    stage_token = None
    if team is not None:
        team_token = _CTX_TEAM.set(team)
    if stage is not None:
        stage_token = _CTX_STAGE.set(stage)
    try:
        yield
    finally:
        if stage_token is not None:
            _CTX_STAGE.reset(stage_token)
        if team_token is not None:
            _CTX_TEAM.reset(team_token)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.team = _CTX_TEAM.get()  # type: ignore[attr-defined]
        record.stage = _CTX_STAGE.get()  # type: ignore[attr-defined]
        return True


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR") in ("1", "true", "True", "yes", "YES"):
        return True
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _build_logger() -> logging.Logger:
    """
    Console logger using `colorlog` (when color is enabled).
    Per-stage log files remain plain text and are handled separately.
    """
    STAGE_LEVEL = 25
    SUCCESS_LEVEL = 35
    logging.addLevelName(STAGE_LEVEL, "STAGE")
    logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

    logger = logging.getLogger("master_eval")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(stream=sys.stdout)

    base_fmt = "[%(asctime)s] %(levelname)s [team=%(team)s stage=%(stage)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    if _color_enabled():
        try:
            import colorlog  # type: ignore

            handler.setFormatter(
                colorlog.ColoredFormatter(
                    fmt="%(log_color)s" + base_fmt + "%(reset)s",
                    datefmt=datefmt,
                    log_colors={
                        "DEBUG": "cyan",
                        "INFO": "white",
                        "STAGE": "cyan",
                        "SUCCESS": "green",
                        "WARNING": "yellow",
                        "ERROR": "red",
                        "CRITICAL": "red,bg_white",
                    },
                )
            )
        except Exception:
            # If colorlog isn't available for some reason, fall back to plain logs.
            handler.setFormatter(logging.Formatter(fmt=base_fmt, datefmt=datefmt))
    else:
        handler.setFormatter(logging.Formatter(fmt=base_fmt, datefmt=datefmt))

    handler.addFilter(_ContextFilter())
    logger.addHandler(handler)
    logger.propagate = False

    # Convenience methods for custom levels.
    def stage(msg: str, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        logger.log(STAGE_LEVEL, msg, *args, **kwargs)

    def success(msg: str, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        logger.log(SUCCESS_LEVEL, msg, *args, **kwargs)

    setattr(logger, "stage", stage)
    setattr(logger, "success", success)
    return logger


_LOGGER = _build_logger()


def log(msg: str, *, level: str = "INFO") -> None:
    lvl = level.upper()
    if lvl in ("ERROR", "FAILED"):
        _LOGGER.error(msg)
    elif lvl in ("WARN", "WARNING"):
        _LOGGER.warning(msg)
    elif lvl in ("OK", "DONE", "SUCCESS"):
        # type: ignore[attr-defined]
        _LOGGER.success(msg)  # pyright: ignore[reportGeneralTypeIssues]
    elif lvl in ("STAGE",):
        # type: ignore[attr-defined]
        _LOGGER.stage(msg)  # pyright: ignore[reportGeneralTypeIssues]
    else:
        _LOGGER.info(msg)

