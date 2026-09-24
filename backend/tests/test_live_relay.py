"""LiveRelay tests with a fake browser socket and a fake Gemini Live session."""

import asyncio
import base64
import contextlib
import json
from datetime import date

import pytest
from google.genai import types

from app.live.relay import LiveRelay
from app.services.sessions import ClaimService, ClaimSession
from tests.fakes import INJURY_FACTS, FakeLLM, fake_runner


class FakeBrowser:
    def __init__(self):
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict] = []

    async def receive_text(self) -> str:
        return await self.inbox.get()

    async def send_json(self, data) -> None:
        self.sent.append(data)

    def push(self, **message):
        self.inbox.put_nowait(json.dumps(message))

    def of_type(self, kind):
        return [m for m in self.sent if m["type"] == kind]


class FakeLive:
    """Records what the relay sends and plays back server messages pushed by the test."""

    def __init__(self):
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.realtime, self.content, self.tool_responses = [], [], []

    async def send_realtime_input(self, **kwargs):
        self.realtime.append(kwargs)

    async def send_client_content(self, **kwargs):
        self.content.append(kwargs)

    async def send_tool_response(self, **kwargs):
        self.tool_responses.extend(kwargs["function_responses"])

    async def receive(self):
        while True:
            yield await self.outbox.get()

    def emit(self, **server_content):
        self.outbox.put_nowait(types.LiveServerMessage(server_content=types.LiveServerContent(**server_content)))

    def call_tool(self, call_id, name, **args):
        self.outbox.put_nowait(
            types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id=call_id, name=name, args=args)]))
        )


async def eventually(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@contextlib.asynccontextmanager
async def running_relay(session=None, llm=None, max_seconds=30):
    session = session or ClaimSession(id="s", owner="o")
    browser, live = FakeBrowser(), FakeLive()

    @contextlib.asynccontextmanager
    async def connect():
        yield live

    service = ClaimService(fake_runner(llm), today=lambda: date(2026, 9, 24))
    relay = LiveRelay(session, service, browser, connect, model_name="fake-live", max_seconds=max_seconds)
    task = asyncio.create_task(relay.run())
    await eventually(lambda: browser.of_type("ready"))
    try:
        yield session, browser, live, task
    finally:
        if not task.done():
            browser.push(type="close")
            await asyncio.wait_for(task, 2)


async def test_ready_then_audio_forwarded_as_pcm():
    async with running_relay() as (session, browser, live, _):
        assert browser.of_type("ready")[0]["model"] == "fake-live"
        pcm = b"\x01\x00" * 160
        browser.push(type="audio", data=base64.b64encode(pcm).decode())
        browser.push(type="audio_end")
        await eventually(lambda: len(live.realtime) == 2)
        assert live.realtime[0]["audio"].data == pcm
        assert live.realtime[0]["audio"].mime_type == "audio/pcm;rate=16000"
        assert live.realtime[1] == {"audio_stream_end": True}


async def test_typed_text_goes_to_model_transcript_and_pipeline():
    async with running_relay() as (session, browser, live, _):
        browser.push(type="text", text="Elena Brooks, HO-20417, basement flooded last night", id="c1")
        await eventually(lambda: any(m["state"]["route"] for m in browser.of_type("state")))
        assert live.content[-1]["turn_complete"] is True
        assert browser.of_type("transcript")[0] == {
            "type": "transcript", "speaker": "claimant", "id": "c1",
            "text": "Elena Brooks, HO-20417, basement flooded last night", "final": True,
        }
        assert browser.of_type("state")[-1]["state"]["route"] == "needs_docs"


async def test_spoken_turns_stream_then_finalize_and_audio_is_relayed():
    async with running_relay() as (session, browser, live, _):
        live.emit(input_transcription=types.Transcription(text="My basement "))
        live.emit(input_transcription=types.Transcription(text="flooded.", finished=True))
        live.emit(output_transcription=types.Transcription(text="I'm sorry to hear that."),
                  model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=b"\x00\x01", mime_type="audio/pcm"))]))
        live.emit(turn_complete=True)
        await eventually(lambda: [t.speaker for t in session.turns] == ["claimant", "agent"])

        partials = [m for m in browser.of_type("transcript") if not m["final"] and m["speaker"] == "claimant"]
        assert partials[-1]["text"] == "My basement flooded."
        assert [t.text for t in session.turns] == ["My basement flooded.", "I'm sorry to hear that."]
        assert browser.of_type("audio")[0]["data"] == base64.b64encode(b"\x00\x01").decode()


async def test_barge_in_is_forwarded_to_browser():
    async with running_relay() as (session, browser, live, _):
        live.emit(output_transcription=types.Transcription(text="Let me explain the"))
        live.emit(interrupted=True)
        await eventually(lambda: browser.of_type("interrupted"))
        assert session.turns[-1].text == "Let me explain the"  # partial agent turn is kept


async def test_lookup_policy_tool_schedules_by_policy_status():
    async with running_relay() as (session, browser, live, _):
        live.call_tool("c1", "lookup_policy", policy_number="H O 2 0 4 1 7")
        live.call_tool("c2", "lookup_policy", policy_number="AU-10001")
        await eventually(lambda: len(live.tool_responses) == 2)
        by_id = {r.id: r for r in live.tool_responses}
        assert by_id["c1"].response["holder"] == "Elena Brooks"
        assert by_id["c1"].scheduling == types.FunctionResponseScheduling.WHEN_IDLE
        assert by_id["c2"].response["status"] == "lapsed"
        assert by_id["c2"].scheduling == types.FunctionResponseScheduling.INTERRUPT
        phases = [m["phase"] for m in browser.of_type("tool") if m["id"] == "c1"]
        assert phases == ["running", "done"]


async def test_update_claim_tool_returns_summary_and_interrupts_on_emergency():
    async with running_relay(llm=FakeLLM(facts=INJURY_FACTS)) as (session, browser, live, _):
        live.emit(input_transcription=types.Transcription(text="I slipped on the wet stairs and hurt my back."))
        live.call_tool("u1", "update_claim", reason="injury mentioned")
        await eventually(lambda: live.tool_responses)
        response = live.tool_responses[0]
        assert session.turns[-1].text == "I slipped on the wet stairs and hurt my back."  # finalized before running
        assert response.response["urgent"] is True
        assert response.response["status"] == "emergency escalation"
        assert response.scheduling == types.FunctionResponseScheduling.INTERRUPT


async def test_escalate_tool_routes_through_rules():
    async with running_relay() as (session, browser, live, _):
        browser.push(type="text", text="The ceiling above the kids' room is sagging")
        live.call_tool("e1", "escalate_to_human", reason="ceiling sagging above children")
        await eventually(lambda: any(m["state"]["route"] == "emergency_escalation" for m in browser.of_type("state")))
        assert session.escalations == ["ceiling sagging above children"]
        assert live.tool_responses[0].response["flagged"] is True


async def test_bad_client_messages_report_errors_without_dropping_the_call():
    async with running_relay() as (session, browser, live, task):
        for bad in ["not json", json.dumps([1]), json.dumps({"type": "video"}),
                    json.dumps({"type": "audio", "data": "***"}),
                    json.dumps({"type": "audio", "data": base64.b64encode(b"\x00").decode()}),
                    json.dumps({"type": "text", "text": "   "})]:
            browser.inbox.put_nowait(bad)
        await eventually(lambda: len(browser.of_type("error")) == 6)
        assert not task.done()


async def test_rate_limit_on_text():
    async with running_relay() as (session, browser, live, _):
        for i in range(21):
            browser.push(type="text", text=f"message {i}", id=f"m{i}")
        await eventually(lambda: browser.of_type("error"))
        assert "slow down" in browser.of_type("error")[0]["message"]


async def test_reconnect_restores_history_without_prompting_a_reply():
    session = ClaimSession(id="s", owner="o")
    session.add_turn("agent", "Is everyone safe?")
    session.add_turn("claimant", "Yes. Basement flooded.")
    async with running_relay(session=session) as (_, browser, live, _task):
        restored = live.content[0]
        assert restored["turn_complete"] is False
        assert [c.role for c in restored["turns"]] == ["model", "user"]


async def test_call_time_limit_and_cleanup():
    session = ClaimSession(id="s", owner="o")
    async with running_relay(session=session, max_seconds=0.2) as (_, browser, live, task):
        await asyncio.wait_for(task, 2)
        assert "time limit" in browser.of_type("error")[-1]["message"]
    assert session.live_connected is False


async def test_non_speech_markers_are_not_stored_as_turns():
    async with running_relay() as (session, browser, live, _):
        live.emit(output_transcription=types.Transcription(text="<no speech>{pause}"))
        live.emit(turn_complete=True)
        live.emit(output_transcription=types.Transcription(text="Thanks, {pause} noted."))
        live.emit(turn_complete=True)
        await eventually(lambda: len(session.turns) == 1)
        assert session.turns[0].text == "Thanks, noted."
