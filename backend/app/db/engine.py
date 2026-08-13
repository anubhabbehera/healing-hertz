from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(f"sqlite+aiosqlite:///{get_settings().db_path}")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db() -> None:
    from sqlalchemy import text

    from .models import Base

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Minimal in-place migration for pre-existing DBs (no Alembic in v1).
        result = await conn.execute(text("PRAGMA table_info(scan_runs)"))
        columns = {row[1] for row in result}
        for column in ("unsupported_json", "advice_error", "devices_json"):
            if column not in columns:
                await conn.execute(text(f"ALTER TABLE scan_runs ADD COLUMN {column} TEXT"))
        result = await conn.execute(text("PRAGMA table_info(findings)"))
        if "dismissed" not in {row[1] for row in result}:
            await conn.execute(
                text("ALTER TABLE findings ADD COLUMN dismissed BOOLEAN NOT NULL DEFAULT 0")
            )


async def dispose() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
