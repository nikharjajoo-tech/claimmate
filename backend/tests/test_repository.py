import asyncio
from datetime import date

import pytest
from sqlalchemy import func, select

from app.domain.models import DocumentType, EvidenceCapture
from app.services import lifecycle
from app.services.sessions import ClaimService, ClaimSession, SessionError
from app.storage.db import make_sessionmaker
from tests.dbutil import fresh_schema, make_test_engine
from app.storage.models import AuditRow, FindingRow, PipelineRunRow, TurnRow
from app.storage.repository import ClaimRepository
from tests.fakes import INJURY_FACTS, FakeLLM, fake_runner

JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 100
TODAY = lambda: date(2026, 9, 24)  # noqa: E731


@pytest.fixture
async def repo(tmp_path):
    engine = make_test_engine(tmp_path)
    await fresh_schema(engine)
    repository = ClaimRepository(make_sessionmaker(engine), tmp_path / "evidence")
    yield repository
    await engine.dispose()


async def count(repo, table, claim_id):
    async with repo._sessionmaker() as db:
        return await db.scalar(select(func.count()).select_from(table).where(table.claim_id == claim_id))


async def actions(repo, claim_id):
    return [e["action"] for e in await repo.audit_log(claim_id)]


def new_session(claim_id="c1", owner="alice"):
    s = ClaimSession(id=claim_id, owner=owner)
    s.add_turn("agent", "Is everyone safe?", turn_id="t0")
    return s


async def test_saves_are_incremental(repo):
    s = new_session()
    await repo.save(s)
    s.add_turn("claimant", "Basement flooded")
    await repo.save(s)
    await repo.save(s)  # nothing new: must not duplicate
    assert await count(repo, TurnRow, "c1") == 2
    assert await actions(repo, "c1") == ["claim_created"]


async def test_pipeline_results_findings_runs_and_route_changes(repo):
    s = new_session()
    s.add_turn("claimant", "Elena Brooks, HO-20417, basement flooded")
    service = ClaimService(fake_runner(), today=TODAY)
    await service.refresh(s)
    await repo.save(s)
    first_findings = await count(repo, FindingRow, "c1")
    assert first_findings > 0

    s.add_turn("claimant", "Phone is 720-555-0148")  # new revision, same route
    await service.refresh(s)
    await repo.save(s)
    assert await count(repo, PipelineRunRow, "c1") == 2
    assert await count(repo, FindingRow, "c1") == first_findings  # replaced, not appended
    assert (await actions(repo, "c1")).count("route_changed") == 1  # none -> needs_docs only

    s.escalate("caller says the ceiling is sagging")
    await service.refresh(s)
    await repo.save(s)
    log = await repo.audit_log("c1")
    assert [e["action"] for e in log][-2:] == ["escalated", "route_changed"]
    assert log[-1]["detail"] == "needs_docs -> emergency_escalation"


async def test_evidence_files_rows_and_audit(repo, tmp_path):
    s = new_session()
    capture = EvidenceCapture(capture_id="cap1", document_types=[DocumentType.DAMAGE_PHOTO], caption="Wet drywall.",
                              confirmed=True, claimant_claim="water damage", source="agent")
    s.captures.append(capture)
    s.evidence_images["cap1"] = JPEG
    await repo.save(s)
    assert (tmp_path / "evidence" / "c1" / "cap1.jpg").read_bytes() == JPEG
    log = await repo.audit_log("c1")
    assert log[-1] == {**log[-1], "actor": "agent", "action": "evidence_added", "detail": "cap1: Wet drywall. (confirmed)"}


async def test_load_round_trip_restores_everything(repo):
    s = new_session()
    s.add_turn("claimant", "Elena Brooks, HO-20417, basement flooded")
    s.captures.append(EvidenceCapture(capture_id="cap1", document_types=[DocumentType.DAMAGE_PHOTO], caption="Wet floor.",
                                      source="upload"))
    s.evidence_images["cap1"] = JPEG
    await ClaimService(fake_runner(), today=TODAY).refresh(s)
    lifecycle.submit(s, "call ended")
    await repo.save(s)

    loaded = await repo.load("c1")
    assert [t.text for t in loaded.turns] == [t.text for t in s.turns]
    assert loaded.status == "submitted" and loaded.submitted_at is not None
    assert loaded.result.decision.route == s.result.decision.route
    assert loaded.result_revision == loaded.revision == s.revision
    assert loaded.evidence_images["cap1"] == JPEG
    assert loaded.observations and "Wet floor." in loaded.observations[0]

    await repo.save(loaded)  # a loaded session has nothing new to write
    assert await count(repo, TurnRow, "c1") == 2
    assert await count(repo, PipelineRunRow, "c1") == 1
    assert await repo.load("missing") is None


async def test_concurrent_saves_do_not_duplicate(repo):
    s = new_session()
    for i in range(5):
        s.add_turn("claimant", f"turn {i}")
    await asyncio.gather(*(repo.save(s) for _ in range(4)))
    assert await count(repo, TurnRow, "c1") == 6


async def test_queue_orders_by_urgency_and_uses_effective_route(repo):
    service = ClaimService(fake_runner(), today=TODAY)
    urgent_service = ClaimService(fake_runner(FakeLLM(facts=INJURY_FACTS)), today=TODAY)
    sessions = {}
    for claim_id, svc in [("a-routine", service), ("b-urgent", urgent_service), ("c-routine", service)]:
        s = new_session(claim_id)
        s.add_turn("claimant", "details")
        await svc.refresh(s)
        await repo.save(s)
        sessions[claim_id] = s
    s = new_session("d-empty")
    await repo.save(s)

    queue = await repo.queue()
    assert [i["id"] for i in queue] == ["b-urgent", "a-routine", "c-routine", "d-empty"]
    assert queue[0]["route"] == "emergency_escalation" and queue[0]["claimant_name"] == "Elena Brooks"

    routine = sessions["c-routine"]
    lifecycle.submit(routine, "done")
    lifecycle.transition(routine, "in_review", actor="adjuster")
    lifecycle.override_route(routine, "special_investigation", "Photos appear to predate the policy.")
    await repo.save(routine)
    queue = await repo.queue()
    assert [i["id"] for i in queue][:2] == ["b-urgent", "c-routine"]
    assert queue[1]["overridden"] and queue[1]["pipeline_route"] == "needs_docs"
    assert [i["id"] for i in await repo.queue(statuses={"in_review"})] == ["c-routine"]
    assert [i["id"] for i in await repo.queue(route="emergency_escalation")] == ["b-urgent"]


async def test_delete_removes_rows_and_files(repo, tmp_path):
    s = new_session()
    s.captures.append(EvidenceCapture(capture_id="cap1", source="agent"))
    s.evidence_images["cap1"] = JPEG
    await repo.save(s)
    await repo.delete("c1")
    assert await repo.load("c1") is None
    assert await count(repo, AuditRow, "c1") == 0
    assert not (tmp_path / "evidence" / "c1").exists()


# --- lifecycle rules (pure) --------------------------------------------------


async def test_lifecycle_freezes_route_on_review_and_requires_override_reason():
    s = new_session()
    assert lifecycle.submit(s, "call ended") is False  # nothing said yet: nothing to submit
    s.add_turn("claimant", "Basement flooded")
    await ClaimService(fake_runner(), today=TODAY).refresh(s)
    assert lifecycle.submit(s, "call ended") is True

    with pytest.raises(SessionError) as exc:
        lifecycle.override_route(s, "policy_review", "Needs underwriting check")
    assert exc.value.status == 409  # must open for review first

    lifecycle.transition(s, "in_review", actor="adjuster")
    assert s.frozen_route == "needs_docs"
    s.escalate("late-arriving escalation")  # the pipeline may change, the reviewed route may not
    await ClaimService(fake_runner(), today=TODAY).refresh(s)
    assert s.pipeline_route == "emergency_escalation" and s.effective_route == "needs_docs"

    with pytest.raises(SessionError) as exc:
        lifecycle.override_route(s, "policy_review", "short")
    assert exc.value.status == 422
    lifecycle.override_route(s, "policy_review", "Policy was reinstated late; verify dates.")
    assert s.effective_route == "policy_review"
    assert [e.action for e in s.pending_audit] == ["status_changed", "route_frozen", "status_changed", "route_overridden"]


def test_lifecycle_rejects_invalid_transitions():
    s = new_session()
    for bad in ["in_review", "closed", "unknown"]:
        with pytest.raises(SessionError):
            lifecycle.transition(s, bad, actor="adjuster")
    s.add_turn("claimant", "x")
    lifecycle.submit(s, "done")
    lifecycle.transition(s, "in_review", actor="adjuster")
    lifecycle.transition(s, "awaiting_docs", actor="adjuster", note="Need the invoice")
    lifecycle.transition(s, "in_review", actor="adjuster")
    lifecycle.transition(s, "closed", actor="adjuster")
    with pytest.raises(SessionError):
        lifecycle.transition(s, "in_review", actor="adjuster")
    assert "awaiting_docs: Need the invoice" in s.pending_audit[-4].detail
