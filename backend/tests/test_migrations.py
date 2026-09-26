"""Migrations must build exactly the schema the models describe, or a deploy would drift.
Runs on SQLite by default and on CLAIMVOICE_TEST_DATABASE_URL (PostgreSQL in CI) when set."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app.storage.db import make_engine
from app.storage.models import Base
from tests.dbutil import REMOTE_URL

BACKEND = Path(__file__).resolve().parents[1]
APP_TABLES = {"claims", "turns", "evidence_captures", "findings", "audit_events", "pipeline_runs", "voice_latency"}


async def _inspect(url: str) -> tuple[list, set[str]]:
    engine = make_engine(url, pooled=False)
    async with engine.connect() as conn:
        diff = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata))
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    await engine.dispose()
    return diff, tables


async def _drop_everything(url: str) -> None:
    engine = make_engine(url, pooled=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


def test_migrations_match_models(tmp_path):
    url = REMOTE_URL or f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}"
    if REMOTE_URL:
        asyncio.run(_drop_everything(url))  # start from an empty database
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    diff, tables = asyncio.run(_inspect(url))
    assert diff == [], f"models changed without a migration: {diff}"
    assert APP_TABLES <= tables

    command.downgrade(config, "base")  # the migrations are reversible
    _, tables = asyncio.run(_inspect(url))
    assert not APP_TABLES & tables
