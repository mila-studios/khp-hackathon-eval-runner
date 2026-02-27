from __future__ import annotations

import logging
import subprocess
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response

from db.session import init_db

_log = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Run alembic upgrade head at startup (idempotent)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            _log.info("DB migrations: up to date")
        else:
            _log.error("DB migration failed (exit %d): %s", result.returncode, result.stderr)
    except Exception as exc:
        _log.error("DB migration error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_migrations()
    init_db()
    yield


app = FastAPI(
    title="Hackathon Eval Runner API",
    version="0.1.0",
    lifespan=lifespan,
)

from api.routers.admin import router as admin_router  # noqa: E402
from api.routers.public import router as public_router  # noqa: E402
from api.routers.dashboard import router as dashboard_router  # noqa: E402
from api.routers.board import router as board_router  # noqa: E402

app.include_router(admin_router)
app.include_router(public_router)
app.include_router(dashboard_router)
app.include_router(board_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
