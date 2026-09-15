"""ARVO DB engine — async SQLAlchemy + Supabase Postgres. pgvector ready."""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

class Base(DeclarativeBase):
    pass

_engine = None
_session = None

def get_engine():
    global _engine
    if _engine is None:
        url = settings.database_url
        # Coolify injects postgres:// ; SQLAlchemy needs postgresql+asyncpg://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        _engine = create_async_engine(url, echo=settings.debug, pool_pre_ping=True)
    return _engine

def get_sessionmaker():
    global _session
    if _session is None:
        _session = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session

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
