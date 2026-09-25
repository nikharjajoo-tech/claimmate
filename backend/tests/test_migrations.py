"""Migrations must build exactly the schema the models describe, or a deploy would drift."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.storage.models import Base

BACKEND = Path(__file__).resolve().parents[1]


def test_migrations_match_models(tmp_path):
    db = tmp_path / "migrated.db"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db}")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        tables = set(inspect(conn).get_table_names())
    engine.dispose()
    assert diff == [], f"models changed without a migration: {diff}"
    assert {"claims", "turns", "evidence_captures", "findings", "audit_events", "pipeline_runs"} <= tables

    command.downgrade(config, "base")  # the migration is reversible
