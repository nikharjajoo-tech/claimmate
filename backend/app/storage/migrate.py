"""Apply Alembic migrations programmatically (the server runs this at startup)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[2]


def upgrade_to_head(database_url: str, turso_auth_token: str = "") -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["turso_auth_token"] = turso_auth_token
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    if database_url.startswith("sqlite"):
        db_path = database_url.split(":///", 1)[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(config, "head")
