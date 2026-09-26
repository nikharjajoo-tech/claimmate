"""Turso (hosted libSQL) behind SQLAlchemy's async SQLite dialect (PRD decision D7).

Turso's Python driver (`turso_serverless`) is synchronous DB-API 2.0 with the same `?` parameter
style as sqlite3. aiosqlite already runs a sqlite3-style connection on a background thread and
accepts any connection factory, so the chain is:

    SQLAlchemy "sqlite+aiosqlite" dialect -> aiosqlite thread -> turso_serverless (HTTP to Turso)

The proxy below fills the few sqlite3 methods the dialect expects but Turso's driver lacks.

EXPERIMENTAL (M7 compatibility test, 2026-09-26). Functionally compatible: connects, runs Alembic
migrations, and saves claims. Not production-ready with turso_serverless 0.1.0, which opens a new
HTTPS connection for every statement (~0.45 s each from ap-south-1, so a claim save takes seconds)
and sets no request timeout (a stalled request blocks the connection indefinitely; seen twice).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiosqlite
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)


def is_turso_url(url: str) -> bool:
    return url.startswith(("libsql://", "https://")) and ".turso.io" in url


class _TursoConnection:
    """Presents a turso_serverless connection as the sqlite3 connection aiosqlite expects."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self.isolation_level: str | None = "DEFERRED"

    def create_function(self, *args: Any, **kwargs: Any) -> None:
        # SQLAlchemy registers a Python REGEXP function on connect; functions run server-side on
        # Turso, and this app never uses REGEXP, so registration is a no-op.
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def make_turso_engine(url: str, auth_token: str) -> AsyncEngine:
    import certifi
    import turso_serverless

    # turso_serverless uses urllib's default SSL context. Some Python installs (e.g. python.org on
    # macOS) ship without root certificates; point at certifi's bundle unless the deployment set one.
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())

    https_url = url.replace("libsql://", "https://", 1)
    logger.warning("Using the experimental Turso adapter: expect slow writes and no request timeouts.")

    async def connect() -> aiosqlite.Connection:
        connection = aiosqlite.Connection(
            lambda: _TursoConnection(turso_serverless.connect(https_url, auth_token=auth_token)),
            iter_chunk_size=64,
        )
        return await connection  # starts the worker thread and opens the connection

    return create_async_engine("sqlite+aiosqlite://", async_creator=connect)
