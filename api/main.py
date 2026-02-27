from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Hackathon Eval Runner API",
    version="0.1.0",
    lifespan=lifespan,
)

from api.routers.admin import router as admin_router  # noqa: E402
from api.routers.public import router as public_router  # noqa: E402

app.include_router(admin_router)
app.include_router(public_router)


@app.get("/health")
def health():
    return {"status": "ok"}
