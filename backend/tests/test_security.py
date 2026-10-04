from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.security import CONTENT_SECURITY_POLICY, RateLimiter
from app.config import get_settings
from app.services.sessions import ClaimService
from app.services.store import SessionStore
from tests.fakes import fake_runner


@pytest.fixture
def client():
    app = create_app(
        settings=replace(get_settings(), google_api_key="test"),
        store=SessionStore(),
        service=ClaimService(fake_runner(), today=lambda: date(2026, 9, 24)),
        live_connect=None,
    )
    with TestClient(app) as c:
        yield c


def test_security_headers_on_every_response(client):
    for response in (client.get("/api/health"), client.get("/api/claims/missing"), client.post("/api/claims")):
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "microphone=(self)" in response.headers["permissions-policy"]
        assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY and "unsafe-inline" not in CONTENT_SECURITY_POLICY


def test_oversized_bodies_are_rejected_before_parsing(client):
    claim_id = client.post("/api/claims").json()["id"]
    big_message = client.post(f"/api/claims/{claim_id}/messages", content=b"x" * 70_000,
                              headers={"content-type": "application/json"})
    assert big_message.status_code == 413
    # the evidence endpoint allows photo-sized bodies but not more
    big_photo = client.post(f"/api/claims/{claim_id}/evidence", content=b"x" * 2_300_000,
                            headers={"content-type": "application/json"})
    assert big_photo.status_code == 413
    # the batch endpoint allows several photos, still bounded
    batch = client.post(f"/api/claims/{claim_id}/evidence/batch", content=b"x" * 2_300_000,
                        headers={"content-type": "application/json"})
    assert batch.status_code == 422  # past the size gate, fails only as bad JSON
    big_batch = client.post(f"/api/claims/{claim_id}/evidence/batch", content=b"x" * 12_100_000,
                            headers={"content-type": "application/json"})
    assert big_batch.status_code == 413


def test_claim_creation_is_rate_limited_per_client(client):
    codes = []
    for _ in range(11):
        client.cookies.clear()  # a new "owner" each time, so only the per-client limit applies
        codes.append(client.post("/api/claims").status_code)
    assert codes == [201] * 10 + [429]


def test_rate_limiter_window():
    limiter = RateLimiter(limit=2, window_s=60)
    assert limiter.allow("ip", now=0) and limiter.allow("ip", now=1)
    assert not limiter.allow("ip", now=2)
    assert limiter.allow("other-ip", now=2)
    assert limiter.allow("ip", now=61)  # the first hit has left the window
