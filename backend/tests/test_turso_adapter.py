"""Offline tests for the Turso adapter (no network). The live compatibility run is manual:
CLAIMVOICE_TEST_TURSO=1 pytest tests/test_repository.py tests/test_api.py tests/test_adjuster.py"""

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
