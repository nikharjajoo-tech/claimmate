"""Test databases. Default: a temporary SQLite file per test. Set CLAIMVOICE_TEST_DATABASE_URL to run
the database tests against another server (CI uses a PostgreSQL service container), or
CLAIMVOICE_TEST_TURSO=1 for the Turso compatibility test. Remote databases are wiped between tests."""

import os
from pathlib import Path

from dotenv import dotenv_values

from app.storage.db import create_all, make_engine
from app.storage.models import Base

REMOTE_URL = os.getenv("CLAIMVOICE_TEST_DATABASE_URL", "")
USE_TURSO = os.getenv("CLAIMVOICE_TEST_TURSO") == "1"
USE_REMOTE = bool(REMOTE_URL) or USE_TURSO


def make_test_engine(tmp_path: Path, name: str = "test.db"):
    if REMOTE_URL:
        return make_engine(REMOTE_URL, pooled=False)
    if USE_TURSO:
        env = {**dotenv_values(Path(__file__).resolve().parents[2] / ".env"), **os.environ}
        return make_engine(env["TURSO_DATABASE_URL"], turso_auth_token=env["TURSO_AUTH_TOKEN"])
    return make_engine(f"sqlite+aiosqlite:///{tmp_path / name}")


_remote_schema_ready = False


async def fresh_schema(engine) -> None:
    """Local: a new SQLite file per test. Remote: build the schema once per run, then empty the
    tables between tests (rebuilding costs many network round trips)."""
    global _remote_schema_ready
    if not USE_REMOTE:
        await create_all(engine)
        return
    if not _remote_schema_ready:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await create_all(engine)
        _remote_schema_ready = True
        return
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):  # children before parents
            await conn.execute(table.delete())
