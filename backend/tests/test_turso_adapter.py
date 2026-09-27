"""Offline tests for the Turso adapter (no network). The live compatibility run is manual:
CLAIMVOICE_TEST_TURSO=1 pytest tests/test_repository.py tests/test_api.py tests/test_adjuster.py"""

import ssl

from app.storage.db import make_engine
from app.storage.turso import _TursoConnection, is_turso_url


def test_turso_urls_are_detected():
    assert is_turso_url("libsql://db-org.aws-ap-south-1.turso.io")
    assert is_turso_url("https://db-org.turso.io")
    assert not is_turso_url("sqlite+aiosqlite:///local.db")
    assert not is_turso_url("https://example.com/db")


def test_proxy_fills_sqlite3_gaps_and_delegates_the_rest():
    class Driver:
        def cursor(self):
            return "cursor"

    proxy = _TursoConnection(Driver())
    assert proxy.create_function("regexp", 2, lambda *a: None) is None  # no-op, never used by the app
    assert proxy.isolation_level == "DEFERRED"
    assert proxy.cursor() == "cursor"


def test_make_engine_routes_turso_urls_without_connecting():
    engine = make_engine("libsql://db-org.turso.io", turso_auth_token="t")
    assert engine.dialect.name == "sqlite" and engine.dialect.driver == "aiosqlite"


def test_neon_urls_are_normalized_for_asyncpg():
    from app.storage.db import normalize_postgres_url

    url, args = normalize_postgres_url(
        "postgresql://u:p@ep-x-pooler.c-4.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    assert url == "postgresql+asyncpg://u:p@ep-x-pooler.c-4.ap-southeast-1.aws.neon.tech/neondb?prepared_statement_cache_size=0"
    assert args["statement_cache_size"] == 0
    context = args["ssl"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname  # full verification

    direct, direct_args = normalize_postgres_url("postgres://u:p@ep-x.aws.neon.tech/neondb?sslmode=require")
    assert direct == "postgresql+asyncpg://u:p@ep-x.aws.neon.tech/neondb" and set(direct_args) == {"ssl"}
