import asyncio
from datetime import date

import pytest

from app.services import sessions as sessions_module
from app.services import store as store_module
from app.services.sessions import ClaimService, ClaimSession, SessionError
from app.services.store import SessionStore
from app.services.view import session_view
from tests.fakes import fake_runner

TODAY = lambda: date(2026, 9, 24)  # noqa: E731


def test_claimant_turns_bump_revision_agent_turns_do_not():
    session = ClaimSession(id="s", owner="o")
    session.add_turn("agent", "Hello")
    assert session.revision == 0
    session.add_turn("claimant", "My basement flooded")
    assert session.revision == 1


def test_duplicate_and_empty_turns_are_ignored():
    session = ClaimSession(id="s", owner="o")
    assert session.add_turn("claimant", "Hi", turn_id="a") is not None
    assert session.add_turn("claimant", "Hi again", turn_id="a") is None
    assert session.add_turn("claimant", "   ") is None
    assert len(session.turns) == 1


def test_transcript_limit(monkeypatch):
    monkeypatch.setattr(sessions_module, "MAX_TURNS", 2)
    session = ClaimSession(id="s", owner="o")
    session.add_turn("claimant", "one")
    session.add_turn("claimant", "two")
    with pytest.raises(SessionError) as exc:
        session.add_turn("claimant", "three")
    assert exc.value.status == 413


async def test_refresh_runs_once_per_revision():
    runner = fake_runner()
    service = ClaimService(runner, today=TODAY)
    session = ClaimSession(id="s", owner="o")
    session.add_turn("claimant", "Elena Brooks, HO-20417, basement flooded")

    first = await service.refresh(session)
    second = await service.refresh(session)
    assert first is second
    assert runner.llm.calls == 2  # extract + classify, once

    session.add_turn("agent", "Thanks")  # agent turns don't invalidate
    await service.refresh(session)
    assert runner.llm.calls == 2

    session.add_turn("claimant", "Phone is 720-555-0148")
    await service.refresh(session)
    assert runner.llm.calls == 4


async def test_new_speech_during_a_run_discards_the_stale_result():
    calls = []
    session = ClaimSession(id="s", owner="o")
    session.add_turn("claimant", "first")
    inner = fake_runner()

    async def slow_runner(turns, **kwargs):
        calls.append([t.text for t in turns])
        if len(calls) == 1:
            session.add_turn("claimant", "second")  # claimant speaks mid-run
        return await inner(turns, **kwargs)

    await ClaimService(slow_runner, today=TODAY).refresh(session)
    assert calls == [["first"], ["first", "second"]]
    assert session.result_revision == session.revision == 2


async def test_concurrent_refreshes_share_one_run():
    runner = fake_runner()
    service = ClaimService(runner, today=TODAY)
    session = ClaimSession(id="s", owner="o")
    session.add_turn("claimant", "flooded")
    await asyncio.gather(service.refresh(session), service.refresh(session), service.refresh(session))
    assert runner.llm.calls == 2


async def test_escalation_reaches_the_rules_engine():
    service = ClaimService(fake_runner(), today=TODAY)
    session = ClaimSession(id="s", owner="o")
    session.add_turn("claimant", "flooded")
    session.escalate("ceiling is sagging over the kids' room")
    result = await service.refresh(session)
    assert result.decision.route == "emergency_escalation"
    assert any(f.rule_id == "AGENT-001" for f in result.decision.findings)


async def test_pipeline_error_is_recorded():
    async def broken(turns, **kwargs):
        raise RuntimeError("model down")

    session = ClaimSession(id="s", owner="o")
    session.add_turn("claimant", "flooded")
    with pytest.raises(RuntimeError):
        await ClaimService(broken, today=TODAY).refresh(session)
    assert "model down" in session.last_error
    assert not session.processing


async def test_store_enforces_ownership_and_limits(monkeypatch):
    store = SessionStore()
    session = await store.create("alice")
    assert session.turns[0].speaker == "agent"  # greeting
    assert await store.get(session.id, "alice") is session
    for owner in ("bob", None, ""):
        with pytest.raises(SessionError) as exc:
            await store.get(session.id, owner)
        assert exc.value.status == 404

    monkeypatch.setattr(store_module, "MAX_SESSIONS_PER_OWNER", 2)
    await store.create("alice")
    with pytest.raises(SessionError) as exc:
        await store.create("alice")
    assert exc.value.status == 429


async def test_idle_intakes_are_submitted_or_discarded():
    store = SessionStore()
    talked = await store.create("alice")
    talked.add_turn("claimant", "My basement flooded")
    silent = await store.create("alice")
    for s in (talked, silent):
        s.updated_at -= sessions_module.SESSION_TTL_S + 1

    with pytest.raises(SessionError) as exc:
        await store.get(talked.id, "alice")
    assert exc.value.status == 410
    assert talked.status == "submitted"  # handed to the adjuster, not lost

    assert await store.sweep() == 1
    assert silent.deleted  # nothing was said: nothing to keep
    assert len(store) == 0


async def test_view_before_and_after_pipeline():
    session = ClaimSession(id="s", owner="o")
    session.add_turn("claimant", "flooded")
    view = session_view(session)
    assert view["route"] is None and not view["up_to_date"]
    assert [f["value"] for f in view["fields"]] == [None] * 6

    await ClaimService(fake_runner(), today=TODAY).refresh(session)
    view = session_view(session)
    assert view["up_to_date"] and view["route"] == "needs_docs"
    assert view["policy"] == {"found": True, "number": "HO-20417", "holder": "Elena Brooks",
                              "line": "Homeowners (HO-3)", "status": "active"}
    assert view["fields"][0] == {"key": "policyholder_name", "label": "Name", "value": "Elena Brooks"}
    assert view["next_question"].startswith("Do you have the")
