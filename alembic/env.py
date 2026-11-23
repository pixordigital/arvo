from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.database.engine import Base
import app.database.models  # noqa: F401
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def _version_kwargs(url: str) -> dict:
    kwargs = {"version_table": "alembic_version_arvo"}
    if "sqlite" not in url:
        kwargs["version_table_schema"] = "arvo"
    return kwargs


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    if "sqlite" not in url:
        context.execute("CREATE SCHEMA IF NOT EXISTS arvo;")
        context.execute("SET search_path TO arvo;")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_version_kwargs(url),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.config import settings
    url = settings.database_url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    connectable = create_async_engine(url, poolclass=pool.NullPool)

    async def _run():
        async with connectable.connect() as connection:
            if "sqlite" not in url:
                await connection.execute(text("CREATE SCHEMA IF NOT EXISTS arvo"))
                await connection.execute(text("SET search_path TO arvo"))
                await connection.commit()
            # Use sync context via run_sync
            def do_migrate(conn):
                context.configure(connection=conn, target_metadata=target_metadata, **_version_kwargs(url))
                with context.begin_transaction():
                    context.run_migrations()
            await connection.run_sync(do_migrate)
        await connectable.dispose()

    # Fallback to sync engine if async fails (sqlite dev)
    try:
        asyncio.run(_run())
    except Exception:
        # sqlite sync fallback
        from sqlalchemy import create_engine
        sync_url = settings.database_url.replace("+asyncpg","").replace("+aiosqlite","")
        if "sqlite" in sync_url:
            sync_url = sync_url.replace("sqlite+aiosqlite","sqlite")
        eng = create_engine(sync_url, poolclass=pool.NullPool)
        with eng.connect() as conn:
            if "sqlite" not in sync_url:
                conn.execute(text("CREATE SCHEMA IF NOT EXISTS arvo"))
                conn.execute(text("SET search_path TO arvo"))
                conn.commit()
            context.configure(connection=conn, target_metadata=target_metadata, **_version_kwargs(sync_url))
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
