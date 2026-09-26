"""Engine and session factory."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.storage.models import Base
from app.storage.turso import is_turso_url, make_turso_engine


def normalize_postgres_url(url: str) -> tuple[str, dict]:
    """Accept the connection strings hosts hand out (postgres://..., ?sslmode=require) and turn them
    into an asyncpg URL plus connect args. asyncpg takes SSL as an argument, not a URL parameter."""
    parts = urlsplit(url)
    scheme = "postgresql+asyncpg"
    query = dict(parse_qsl(parts.query))
    connect_args: dict = {}
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)  # libpq-only option (Neon adds it); asyncpg negotiates itself
    if sslmode in {"require", "verify-ca", "verify-full"}:
        connect_args["ssl"] = True
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)), connect_args


def make_engine(url: str, *, turso_auth_token: str = "", pooled: bool = True) -> AsyncEngine:
    """pooled=False opens a connection per checkout, for tests that use several event loops
    (asyncpg connections belong to the loop that created them)."""
    if is_turso_url(url):
        return make_turso_engine(url, turso_auth_token)
    if url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
        pg_url, connect_args = normalize_postgres_url(url)
        if not pooled:
            return create_async_engine(pg_url, connect_args=connect_args, poolclass=NullPool)
        # Serverless Postgres (Neon) suspends idle databases and drops connections: check before use.
        return create_async_engine(pg_url, connect_args=connect_args, pool_pre_ping=True, pool_recycle=300)
    engine = create_async_engine(url)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            # WAL lets the adjuster read while a live call writes; SQLite still allows one writer at a time.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """For tests and first local runs. Real schema changes go through Alembic migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
