"""Wording review end to end (M8 step 3): when it runs, what survives a restart, who may read it.

The model is scripted, so these tests exercise scheduling, persistence, and isolation rather than
the quality of a review (that is tests/test_policy_review.py and the step 5 eval).
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import get_settings
from app.llm.client import LLMCall, LLMError, LLMResult
from app.review.models import DraftClause, DraftQuestion, ReviewDraft
from app.services.review_runner import ReviewRunner
from app.services.sessions import ClaimService
from app.services.store import SessionStore
from app.storage.db import make_sessionmaker
from app.storage.models import WordingReviewRow
from app.storage.repository import ClaimRepository, operations_metrics
from tests.dbutil import fresh_schema, make_test_engine
from tests.fakes import fake_runner
from tests.test_adjuster import PASSCODE, TODAY, signed_in

ANCHOR = "We cover sudden and accidental direct physical loss to the residence"
DRAFT = ReviewDraft(
    summary="Elena Brooks reports a basement flood on 2026-09-23 after a sump pump failure.",
    clauses=[DraftClause(section="2.1", anchor=ANCHOR, why_it_matters="Sets out what the cover responds to.")],
    points_to_check=["Whether the water came from the sump or from outside (3.1)."],
    questions=[DraftQuestion(turn_id="t1", anchor="will my deductible apply")],
)


class ScriptedLLM:
    def __init__(self, draft=DRAFT, fail=False, delay=0.0):
        self.draft, self.fail, self.delay, self.calls = draft, fail, delay, 0

    async def generate(self, *, step, system, prompt, schema, image=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise LLMError("every model in the chain failed")
        return LLMResult[schema](
            value=self.draft,
            call=LLMCall(step=step, model="fake-120b", attempts=1, latency_ms=20, input_tokens=4200, output_tokens=260),
        )


def build(tmp_path, *, review_llm=None, reviews=True):
    settings = replace(get_settings(), google_api_key="test", adjuster_passcode=PASSCODE, session_secret="s3cret")
    engine = make_test_engine(tmp_path, "claims.db")
    asyncio.run(fresh_schema(engine))
    repo = ClaimRepository(make_sessionmaker(engine), tmp_path / "evidence")
    llm = review_llm or ScriptedLLM()
    runner = ReviewRunner(repo, lambda: llm) if reviews else None
    store = SessionStore(repo, runner)
    app = create_app(
        settings=settings, store=store, service=ClaimService(fake_runner(), today=TODAY), live_connect=None
    )
    return app, store, llm


def file_claim(app, text="Elena Brooks, HO-20417, the basement flooded last night, will my deductible apply?") -> str:
    with TestClient(app) as claimant:
        claim_id = claimant.post("/api/claims").json()["id"]
        claimant.post(f"/api/claims/{claim_id}/messages", json={"text": text, "id": "t1"})
        claimant.delete(f"/api/claims/{claim_id}")  # "New claim" submits this one
    return claim_id


async def settle(store) -> None:
    await store.reviews.drain()


def adjuster_session(app):
    """A signed-in client inside the app's lifespan.

    Background generations belong to the event loop that created them, and TestClient only keeps a
    loop alive for the duration of its context manager, draining on exit. Anything that starts a
    review over HTTP therefore has to run inside this block, and be asserted after it.
    """
    import contextlib

    @contextlib.contextmanager
    def _client():
        with TestClient(app) as client:
            assert client.post("/api/adjuster/login", json={"passcode": PASSCODE}).status_code == 204
            yield client

    return _client()


class NeverFinishes:
    """Stands in for a running generation, so the in-flight refusal can be tested without racing."""

    def done(self) -> bool:
        return False


# --- when it runs ------------------------------------------------------------


def test_submitting_a_claim_generates_a_review_in_the_background(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)
    asyncio.run(settle(store))

    assert llm.calls == 1
    body = signed_in(app).get(f"/api/adjuster/claims/{claim_id}").json()["wording_review"]
    assert body["status"] == "ready"
    review = body["review"]
    assert review["wording_ref"] == "homeowners/v1"
    assert [c["section"] for c in review["clauses"]] == ["2.1"]
    assert review["clauses"][0]["quote"].startswith("We cover sudden and accidental")
    assert [q["quote"] for q in review["questions"]] == [
        "Elena Brooks, HO-20417, the basement flooded last night, will my deductible apply?"
    ]


def test_the_claim_is_submitted_even_when_the_model_is_down(tmp_path):
    """FR-10.8: a review failure never costs the claimant their claim."""
    app, store, llm = build(tmp_path, review_llm=ScriptedLLM(fail=True))
    claim_id = file_claim(app)
    asyncio.run(settle(store))

    client = signed_in(app)
    detail = client.get(f"/api/adjuster/claims/{claim_id}").json()
    assert detail["state"]["status"] == "submitted"  # the claim itself is fine
    assert detail["wording_review"]["status"] == "failed"
    assert "every model" in detail["wording_review"]["error"]
    assert any(e["action"] == "wording_review_failed" for e in detail["audit"])


def test_no_review_runs_while_the_claim_is_still_in_intake(tmp_path):
    """D14e: one call per claim, after submission, never during the conversation."""
    app, store, llm = build(tmp_path)
    with TestClient(app) as claimant:
        claim_id = claimant.post("/api/claims").json()["id"]
        claimant.post(f"/api/claims/{claim_id}/messages", json={"text": "my basement flooded"})
    asyncio.run(settle(store))
    assert llm.calls == 0

    refused = signed_in(app).post(f"/api/adjuster/claims/{claim_id}/wording-review/refresh")
    assert refused.status_code == 409


def test_a_generation_is_recorded_in_the_audit_trail(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)
    asyncio.run(settle(store))
    audit = signed_in(app).get(f"/api/adjuster/claims/{claim_id}").json()["audit"]
    entry = next(e for e in audit if e["action"] == "wording_review")
    assert "homeowners/v1" in entry["detail"] and "fake-120b" in entry["detail"]


# --- only the adjuster may read it (D14a, FR-10.6) ---------------------------


def test_the_claimant_never_sees_the_review_in_their_own_claim(tmp_path):
    app, store, llm = build(tmp_path)
    with TestClient(app) as claimant:
        claim_id = claimant.post("/api/claims").json()["id"]
        claimant.post(f"/api/claims/{claim_id}/messages", json={"text": "Elena Brooks, HO-20417, basement flooded"})
        claimant.delete(f"/api/claims/{claim_id}")
        asyncio.run(settle(store))

        body = claimant.get(f"/api/claims/{claim_id}")
        assert body.status_code == 200
        text = body.text
        assert "wording_review" not in text
        assert "We cover sudden and accidental" not in text  # no clause text, not even a quote
        assert "2.1" not in text


def test_the_claimant_packet_carries_no_review(tmp_path):
    app, store, llm = build(tmp_path)
    with TestClient(app) as claimant:
        claim_id = claimant.post("/api/claims").json()["id"]
        claimant.post(f"/api/claims/{claim_id}/messages", json={"text": "Elena Brooks, HO-20417, basement flooded"})
        claimant.delete(f"/api/claims/{claim_id}")
        asyncio.run(settle(store))
        packet = claimant.get(f"/api/claims/{claim_id}/packet")
        assert packet.status_code == 200
        assert b"We cover sudden and accidental" not in packet.content
        assert b"Wording review" not in packet.content


def test_a_signed_out_visitor_cannot_read_the_review(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)
    asyncio.run(settle(store))
    anonymous = TestClient(app)
    assert anonymous.get(f"/api/adjuster/claims/{claim_id}").status_code == 401
    assert anonymous.post(f"/api/adjuster/claims/{claim_id}/wording-review/refresh").status_code == 401


# --- refresh, in-flight guard, cooldown (FR-10.9) ----------------------------


def test_refresh_generates_again(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)

    with adjuster_session(app) as client:
        store.reviews._finished_at.clear()  # pretend the cooldown has passed
        assert client.post(f"/api/adjuster/claims/{claim_id}/wording-review/refresh").status_code == 200
    assert llm.calls == 2  # drained when the block exited

    async def row():
        return await store.repo.load_review(claim_id)

    assert asyncio.run(row())["runs"] == 2


def test_a_just_generated_review_cannot_be_refreshed_again_at_once(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)

    with adjuster_session(app) as client:
        store.reviews._finished_at[claim_id] = store.reviews._clock()  # as if it just finished
        refused = client.post(f"/api/adjuster/claims/{claim_id}/wording-review/refresh")
    assert refused.status_code == 429
    assert "Try again in" in refused.text
    assert llm.calls == 1  # the second press cost nothing


def test_a_refresh_while_one_is_running_is_refused_rather_than_queued(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)

    with adjuster_session(app) as client:
        store.reviews._running[claim_id] = NeverFinishes()
        refused = client.post(f"/api/adjuster/claims/{claim_id}/wording-review/refresh")
        store.reviews._running.clear()
    assert refused.status_code == 409
    assert "already running" in refused.text
    assert llm.calls == 1


def test_two_generations_never_run_at_once(tmp_path):
    app, store, llm = build(tmp_path, review_llm=ScriptedLLM(delay=0.05))
    claim_id = file_claim(app)

    async def press_twice():
        session = await store.get_for_adjuster(claim_id)
        store.reviews._finished_at.clear()
        first = await store.reviews.refresh(session)
        second = await store.reviews.refresh(session)  # while the first is still running
        await store.reviews.drain()
        return first, second

    first, second = asyncio.run(press_twice())
    assert first is True and second is False
    assert llm.calls == 2  # one at submission, one for the refresh that was allowed


def test_usage_is_counted_separately_from_the_pipeline(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)
    asyncio.run(settle(store))
    metrics = signed_in(app).get("/api/adjuster/metrics").json()
    assert metrics["wording_review"] == {
        "claims": 1,
        "by_status": {"ready": 1},
        "generations": 1,
        "latency_p50_ms": 20,
        "tokens_in": 4200,
        "tokens_out": 260,
    }
    assert metrics["pipeline"]["tokens_in"] == 0  # the fake pipeline reports none; the review did not leak in


# --- surviving a restart (spec section 9.3) ----------------------------------


def test_a_review_left_pending_by_a_restart_is_retried_when_an_adjuster_opens_the_claim(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)

    async def strand_it():
        """Put the row back to how a crash mid-generation would leave it, long enough ago."""
        async with store.repo._sessionmaker() as db, db.begin():
            row = await db.get(WordingReviewRow, claim_id)
            row.status, row.review_json = "pending", None
            row.started_at = datetime.now(UTC) - timedelta(seconds=600)
        store.reviews._running.clear()
        store.reviews._finished_at.clear()

    asyncio.run(strand_it())
    with adjuster_session(app) as client:
        assert client.post(f"/api/adjuster/claims/{claim_id}/open").status_code == 200
    assert llm.calls == 2

    with adjuster_session(app) as client:
        assert client.get(f"/api/adjuster/claims/{claim_id}").json()["wording_review"]["status"] == "ready"


def test_a_review_that_is_merely_slow_is_not_started_again(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)
    asyncio.run(settle(store))

    async def just_started():
        async with store.repo._sessionmaker() as db, db.begin():
            row = await db.get(WordingReviewRow, claim_id)
            row.status, row.started_at = "pending", datetime.now(UTC)
        store.reviews._running.clear()
        session = await store.get_for_adjuster(claim_id)
        await store.reviews.ensure(session)
        await store.reviews.drain()

    asyncio.run(just_started())
    assert llm.calls == 1


def test_deleting_a_claim_takes_its_review_with_it(tmp_path):
    app, store, llm = build(tmp_path)
    claim_id = file_claim(app)
    asyncio.run(settle(store))

    async def delete_and_check():
        await store.repo.delete(claim_id)
        return await store.repo.load_review(claim_id)

    assert asyncio.run(delete_and_check()) is None


def test_without_a_runner_the_panel_says_so_instead_of_failing(tmp_path):
    app, store, _ = build(tmp_path, reviews=False)
    claim_id = file_claim(app)
    client = signed_in(app)
    assert client.get(f"/api/adjuster/claims/{claim_id}").json()["wording_review"]["status"] == "disabled"
    assert client.post(f"/api/adjuster/claims/{claim_id}/wording-review/refresh").status_code == 503
