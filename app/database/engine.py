"""ARVO DB engine — async SQLAlchemy on SQLite (local) or Postgres (Supabase).

Engines are cached per running event loop: asyncpg connections are bound to the
loop that opened them, so a single process that runs more than one loop (tests,
scripted jobs) must not share a pool across loops.
"""

import asyncio
import weakref

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings


class Base(DeclarativeBase):
    pass


_engines: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_sessions: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_default_engine = None
_default_session = None


def _engine_for(url: str):
    kwargs = dict(echo=settings.debug, pool_pre_ping=True)
    # SQLite uses NullPool; pool_size/max_overflow invalid there.
    if not url.startswith("sqlite"):
        kwargs.update(pool_size=5, max_overflow=5)
    return create_async_engine(url, **kwargs)


def get_engine():
    global _default_engine
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        if _default_engine is None:
            _default_engine = _engine_for(_normalized_url())
        return _default_engine
    engine = _engines.get(loop)
    if engine is None:
        engine = _engine_for(_normalized_url())
        _engines[loop] = engine
    return engine


def get_sessionmaker():
    global _default_session
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        if _default_session is None:
            _default_session = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
        return _default_session
    maker = _sessions.get(loop)
    if maker is None:
        maker = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
        _sessions[loop] = maker
    return maker


def _normalized_url() -> str:
    url = settings.database_url
    # Coolify injects postgres:// ; SQLAlchemy needs postgresql+asyncpg://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def init_db():
    # Create tables if using sqlite dev; in Supabase use Alembic migrations
    if "sqlite" in settings.database_url:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def check_db() -> bool:
    try:
        from sqlalchemy import text
        async with get_sessionmaker()() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
