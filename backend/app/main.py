import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_dismissals,
    routes_icons,
    routes_rules,
    routes_runs,
    routes_scan,
    routes_settings,
)
from app.db import repo
from app.db.engine import dispose, get_session_factory, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Scans only exist in the process that started them, so any run still marked
    # running belongs to a previous life of this server and will never finish.
    async with get_session_factory()() as session:
        abandoned = await repo.abandon_stale_runs(session)
    if abandoned:
        logger.info("Marked %d stale running scan(s) as failed on startup", abandoned)
    yield
    await dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="healing-hertz", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes_scan.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_dismissals.router)
    app.include_router(routes_rules.router)
    app.include_router(routes_icons.router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
