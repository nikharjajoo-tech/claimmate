"""Adjuster workflow (workflow 6), persistence across restarts, and call-end semantics."""

import asyncio
import contextlib
import io
import zipfile
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.adjuster import ADJUSTER_COOKIE, make_token, token_valid
from app.api.main import create_app
from app.config import get_settings
from app.live.relay import LiveRelay
from app.services.sessions import ClaimService
from app.services.store import SessionStore
from app.storage.db import create_all, make_engine, make_sessionmaker
from app.storage.repository import ClaimRepository
from tests.fakes import INJURY_FACTS, FakeLLM, fake_runner
from tests.test_live_relay import FakeBrowser, FakeLive, eventually

PASSCODE = "correct horse battery"
TODAY = lambda: date(2026, 9, 24)  # noqa: E731


def build_app(tmp_path, *, passcode=PASSCODE, llm=None, secret="s3cret"):
    settings = replace(get_settings(), google_api_key="test", adjuster_passcode=passcode, session_secret=secret)
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
    asyncio.run(create_all(engine))
    store = SessionStore(ClaimRepository(make_sessionmaker(engine), tmp_path / "evidence"))
    return create_app(settings=settings, store=store, service=ClaimService(fake_runner(llm), today=TODAY), live_connect=None)


@pytest.fixture
def app(tmp_path):
    return build_app(tmp_path)


def claimant_files_claim(app, text="Elena Brooks, HO-20417, basement flooded last night") -> str:
    with TestClient(app) as claimant:
        claim_id = claimant.post("/api/claims").json()["id"]
        claimant.post(f"/api/claims/{claim_id}/messages", json={"text": text})
        claimant.delete(f"/api/claims/{claim_id}")  # "New claim": submits this one
    return claim_id


def signed_in(app) -> TestClient:
    client = TestClient(app)
    assert client.post("/api/adjuster/login", json={"passcode": PASSCODE}).status_code == 204
    return client


# --- sign-in -----------------------------------------------------------------


def test_sign_in_flow(app):
    with TestClient(app) as client:
        assert client.get("/api/adjuster/me").json() == {"enabled": True, "signed_in": False}
        assert client.get("/api/adjuster/claims").status_code == 401
        assert client.post("/api/adjuster/login", json={"passcode": "wrong"}).status_code == 401
        assert client.post("/api/adjuster/login", json={"passcode": PASSCODE}).status_code == 204
        assert client.get("/api/adjuster/me").json()["signed_in"] is True
        assert client.get("/api/adjuster/claims").status_code == 200
        client.post("/api/adjuster/logout")
        assert client.get("/api/adjuster/claims").status_code == 401


def test_sign_in_is_rate_limited(app):
    with TestClient(app) as client:
        codes = [client.post("/api/adjuster/login", json={"passcode": "guess"}).status_code for _ in range(6)]
        assert codes == [401] * 5 + [429]
        assert client.post("/api/adjuster/login", json={"passcode": PASSCODE}).status_code == 429


def test_adjuster_view_disabled_without_passcode(tmp_path):
    with TestClient(build_app(tmp_path, passcode="")) as client:
        assert client.get("/api/adjuster/me").json()["enabled"] is False
        assert client.post("/api/adjuster/login", json={"passcode": ""}).status_code == 503


def test_tokens_expire_and_resist_tampering():
    token = make_token("k", now=1_000)
    assert token_valid("k", token, now=1_001)
    assert not token_valid("k", token, now=1_000 + 9 * 3600)  # past the 8-hour session
    assert not token_valid("other-secret", token, now=1_001)
    expires, sig = token.split(".")
    assert not token_valid("k", f"{int(expires) + 999}.{sig}", now=1_001)  # extended expiry, old signature
    assert not token_valid("k", "garbage", now=1_001)


def test_claimant_cookie_cannot_reach_adjuster_api(app):
    with TestClient(app) as claimant:
        claimant.post("/api/claims")
        claimant.cookies.set(ADJUSTER_COOKIE, "123.forged", path="/api/adjuster")
        assert claimant.get("/api/adjuster/claims").status_code == 401


# --- the review workflow -----------------------------------------------------


def test_full_review_workflow(app):
    claim_id = claimant_files_claim(app)
    with signed_in(app) as adjuster:
        queue = adjuster.get("/api/adjuster/claims").json()["claims"]
        assert [(c["id"], c["status"], c["route"]) for c in queue] == [(claim_id, "submitted", "needs_docs")]
        assert queue[0]["claimant_name"] == "Elena Brooks"

        opened = adjuster.post(f"/api/adjuster/claims/{claim_id}/open").json()
        assert opened["state"]["status"] == "in_review" and opened["state"]["route_frozen"] is True
        assert opened["state"]["fact_sources"] == {}  # fake extractor supplies no sources

        url = f"/api/adjuster/claims/{claim_id}"
        assert adjuster.post(f"{url}/override", json={"route": "policy_review", "reason": "short"}).status_code == 422
        assert adjuster.post(f"{url}/override", json={"route": "not_a_route", "reason": "a long enough reason"}).status_code == 422
        overridden = adjuster.post(f"{url}/override", json={"route": "policy_review", "reason": "Deductible dispute on file."}).json()
        assert overridden["state"]["route"] == "policy_review" and overridden["state"]["pipeline_route"] == "needs_docs"

        assert adjuster.post(f"{url}/status", json={"status": "awaiting_docs", "note": "Need the invoice"}).status_code == 200
        assert adjuster.post(f"{url}/status", json={"status": "submitted"}).status_code == 422  # not an adjuster status
        closed = adjuster.post(f"{url}/status", json={"status": "closed"}).json()
        assert closed["state"]["status"] == "closed"
        assert adjuster.post(f"{url}/status", json={"status": "in_review"}).status_code == 409

        actions = [(e["actor"], e["action"]) for e in closed["audit"]]
        assert actions[0] == ("system", "claim_created")
        assert ("system", "route_changed") in actions
        assert [a for a in actions if a[0] == "adjuster"] == [
            ("adjuster", "route_frozen"),
            ("adjuster", "status_changed"),  # -> in_review
            ("adjuster", "route_overridden"),
            ("adjuster", "status_changed"),  # -> awaiting_docs
            ("adjuster", "status_changed"),  # -> closed
        ]
        assert [e["detail"] for e in closed["audit"] if e["action"] == "status_changed"] == [
            "intake -> submitted: claimant started a new claim",
            "submitted -> in_review",
            "in_review -> awaiting_docs: Need the invoice",
            "awaiting_docs -> closed",
        ]

        assert adjuster.get("/api/adjuster/claims").json()["claims"] == []  # closed claims leave the open queue
        assert len(adjuster.get("/api/adjuster/claims?status=all").json()["claims"]) == 1

        packet = adjuster.get(f"{url}/packet.zip")
        claim_md = zipfile.ZipFile(io.BytesIO(packet.content)).read("claim.md").decode()
        assert "- Status: closed" in claim_md and "overridden from needs_docs: Deductible dispute on file." in claim_md


def test_emergencies_lead_the_queue_even_mid_intake(tmp_path):
    app = build_app(tmp_path, llm=FakeLLM(facts=INJURY_FACTS))
    routine = claimant_files_claim(build_app(tmp_path))  # same database file, routine claim first
    with TestClient(app) as claimant:
        urgent = claimant.post("/api/claims").json()["id"]
        claimant.post(f"/api/claims/{urgent}/messages", json={"text": "I slipped and hurt my back"})
        with signed_in(app) as adjuster:
            queue = adjuster.get("/api/adjuster/claims").json()["claims"]
            assert [(c["id"], c["status"]) for c in queue] == [(urgent, "intake"), (routine, "submitted")]
            assert adjuster.post(f"/api/adjuster/claims/{urgent}/status", json={"status": "in_review"}).status_code == 409


def test_claims_survive_a_server_restart(tmp_path):
    claim_id = claimant_files_claim(build_app(tmp_path))
    restarted = build_app(tmp_path)  # new process: empty memory, same database
    with signed_in(restarted) as adjuster:
        detail = adjuster.get(f"/api/adjuster/claims/{claim_id}").json()
        assert detail["state"]["status"] == "submitted"
        assert [t["speaker"] for t in detail["state"]["transcript"]] == ["agent", "claimant", "agent"]
        assert detail["state"]["route"] == "needs_docs" and detail["state"]["policy"]["holder"] == "Elena Brooks"


# --- call end semantics --------------------------------------------------------


async def run_call(store, session, script):
    browser, live = FakeBrowser(), FakeLive()

    @contextlib.asynccontextmanager
    async def connect():
        yield live

    relay = LiveRelay(session, ClaimService(fake_runner(), today=TODAY), browser, connect, store=store)
    task = asyncio.create_task(relay.run())
    await eventually(lambda: browser.of_type("ready"))
    await script(browser)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, 2)


async def test_end_call_submits_but_a_dropped_connection_does_not():
    store = SessionStore()

    ended = await store.create("alice")
    async def hang_up(browser):
        browser.push(type="text", text="Basement flooded")
        await eventually(lambda: len(ended.turns) == 2)
        browser.push(type="close")
    await run_call(store, ended, hang_up)
    assert ended.status == "submitted"

    dropped = await store.create("alice")
    async def lose_connection(browser):
        browser.push(type="text", text="Basement flooded")
        await eventually(lambda: len(dropped.turns) == 2)
        async def broken():
            raise ConnectionError("network gone")
        browser.receive_text = broken
        browser.push(type="text", text="unblock the pending receive")
    await run_call(store, dropped, lose_connection)
    assert dropped.status == "intake"  # the claimant can reconnect and continue
    assert (await store.get(dropped.id, "alice")) is dropped
