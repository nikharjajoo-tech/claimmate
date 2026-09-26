"""Test databases: a temporary SQLite file by default, or the real Turso database when
CLAIMVOICE_TEST_TURSO=1 (the D7 compatibility test). Turso mode wipes app tables per test."""

import os
from pathlib import Path

from dotenv import dotenv_values

from app.storage.db import create_all, make_engine
from app.storage.models import Base

USE_TURSO = os.getenv("CLAIMVOICE_TEST_TURSO") == "1"


def make_test_engine(tmp_path: Path, name: str = "test.db"):
    if not USE_TURSO:
        return make_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    env = {**dotenv_values(Path(__file__).resolve().parents[2] / ".env"), **os.environ}
    return make_engine(env["TURSO_DATABASE_URL"], turso_auth_token=env["TURSO_AUTH_TOKEN"])


_turso_schema_ready = False


async def fresh_schema(engine) -> None:
    """Local: a new SQLite file per test. Turso: build the schema once per run, then empty the
    tables between tests (each DDL statement is a network round trip, so rebuilding is slow)."""
    global _turso_schema_ready
    if not USE_TURSO:
        await create_all(engine)
        return
    if not _turso_schema_ready:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await create_all(engine)
        _turso_schema_ready = True
        return
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):  # children before parents
            await conn.execute(table.delete())
