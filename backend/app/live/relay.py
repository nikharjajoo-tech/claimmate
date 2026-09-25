"""Bridges one browser WebSocket to one Gemini Live session.

Browser -> server messages (JSON):
    {"type": "audio", "data": <base64 PCM16 mono 16 kHz>}
    {"type": "audio_end"}                     microphone stopped
    {"type": "text", "text": "...", "id": "optional-client-id"}
    {"type": "camera", "enabled": true|false}
    {"type": "video", "data": <base64 JPEG>}  ~1 frame per second while the camera is on
    {"type": "capture", "claim": "optional: what the caller says it shows"}
    {"type": "close"}

Server -> browser messages (JSON):
    {"type": "ready", "model": ...}
    {"type": "transcript", "speaker": "claimant"|"agent", "id", "text", "final": bool}
    {"type": "audio", "data": <base64 PCM16 mono 24 kHz>}
    {"type": "interrupted"}                   claimant barged in: stop playback now
    {"type": "tool", ...ToolActivity}
    {"type": "state", "state": <session view>}
    {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, AsyncContextManager, Protocol

from google.genai import types

from app.domain.policy_store import lookup_policy
from app.live.tools import headline, scheduling, summarize_for_agent
from app.llm.client import StructuredLLM
from app.services.evidence import capture_summary, fresh_frame, is_jpeg, verify_and_record
from app.services.sessions import ClaimService, ClaimSession, SessionError, ToolActivity
from app.services.view import session_view

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 200_000
MAX_AUDIO_BYTES = 64_000
MAX_CONCURRENT_TOOLS = 4
MAX_CALL_SECONDS = 15 * 60
# Sliding-window limits: (window seconds, max messages)
RATE_LIMITS = {
    "audio": (1.0, 50),
    "text": (60.0, 20),
    "audio_end": (1.0, 5),
    "video": (1.0, 3),
    "camera": (1.0, 5),
    "capture": (10.0, 3),
}
MAX_FRAME_BYTES = 300_000


class BrowserSocket(Protocol):
    async def receive_text(self) -> str: ...
    async def send_json(self, data: Any) -> None: ...


class LiveSession(Protocol):
    async def send_realtime_input(self, **kwargs: Any) -> None: ...
    async def send_client_content(self, **kwargs: Any) -> None: ...
    async def send_tool_response(self, **kwargs: Any) -> None: ...
    def receive(self) -> Any: ...


# Live transcription sometimes carries non-speech markers such as "<no speech>" or "{pause}".
_NON_SPEECH = re.compile(r"<[^<>]{1,40}>|\{[a-zA-Z_ ]{1,30}\}")


def clean_transcript(text: str) -> str:
    return " ".join(_NON_SPEECH.sub(" ", text).split())


class ClientError(ValueError):
    """A bad message from the browser: reported back, connection stays open."""


class LiveRelay:
    def __init__(
        self,
        session: ClaimSession,
        service: ClaimService,
        browser: BrowserSocket,
        connect: Callable[[], AsyncContextManager[LiveSession]],
        *,
        model_name: str = "",
        max_seconds: float = MAX_CALL_SECONDS,
        vision: StructuredLLM | None = None,
    ) -> None:
        self.vision = vision
        self.session = session
        self.service = service
        self.browser = browser
        self.connect = connect
        self.model_name = model_name
        self.max_seconds = max_seconds
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._tool_tasks: dict[str, asyncio.Task] = {}
        self._update_task: asyncio.Task | None = None
        self._pending = {"claimant": self._new_pending(), "agent": self._new_pending()}
        self._windows: dict[str, deque[float]] = {kind: deque() for kind in RATE_LIMITS}

    # --- plumbing ------------------------------------------------------------

    @staticmethod
    def _new_pending() -> dict[str, str]:
        return {"id": f"t{uuid.uuid4().hex[:10]}", "text": ""}

    async def send(self, payload: dict[str, Any]) -> None:
        if self.session.deleted:
            return
        async with self._send_lock:
            await self.browser.send_json(payload)

    async def send_state(self) -> None:
        await self.send({"type": "state", "state": session_view(self.session)})

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.error("background task failed: %r", t.exception())

        task.add_done_callback(done)
        return task

    # --- transcripts and pipeline updates ------------------------------------

    async def finalize(self, speaker: str) -> None:
        pending = self._pending[speaker]
        self._pending[speaker] = self._new_pending()
        turn = self.session.add_turn(speaker, clean_transcript(pending["text"]), turn_id=pending["id"])
        if turn is None:
            return
        await self.send({"type": "transcript", "speaker": speaker, "id": turn.id, "text": turn.text, "final": True})
        if speaker == "claimant":
            self.request_update()

    def request_update(self) -> asyncio.Task:
        """Single-flight pipeline refresh. The service re-runs if new speech arrived meanwhile."""
        if self._update_task is None or self._update_task.done():
            self._update_task = self._spawn(self._update())
        return self._update_task

    async def _update(self):
        if not self.session.has_claimant_speech:
            return None
        await self.send_state()  # shows "processing"
        try:
            result = await self.service.refresh(self.session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("claim update failed")
            await self.send({"type": "error", "message": "The claim update failed. The conversation is saved; it will retry on the next turn."})
            await self.send_state()
            return None
        await self.send_state()
        return result

    # --- tools ---------------------------------------------------------------

    async def run_tool(self, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Execute one tool. Returns (response for the model, urgent)."""
        if name == "lookup_policy":
            found = lookup_policy(str(args.get("policy_number", "")))
            if found.found and found.record:
                r = found.record
                result = {"found": True, "holder": r.policyholder_name, "line": r.policy_line, "status": r.status}
                return result, r.status != "active"
            return {"found": False, "message": found.message}, True
        if name == "update_claim":
            await self.finalize("claimant")
            result = await asyncio.shield(self.request_update())
            if result is None:
                return {"error": "The claims team could not update right now. Continue the conversation."}, False
            summary = summarize_for_agent(result)
            return summary, summary["urgent"]
        if name == "capture_evidence":
            return await self.capture(str(args.get("caller_says_it_shows", "")), source="agent"), False
        if name == "escalate_to_human":
            self.session.escalate(str(args.get("reason", "")))
            self.request_update()
            return {
                "flagged": True,
                "tell_the_caller": "A human representative will review this right away. "
                "If anyone is in danger, contact emergency services now.",
            }, False
        return {"error": f"Unknown tool {name}"}, False

    async def capture(self, claim: str, *, source: str) -> dict[str, Any]:
        """Verify and record the current frame. Errors come back as data so the agent can react."""
        if self.vision is None:
            return {"captured": False, "error": "Photo verification is not configured on the server."}
        try:
            frame = fresh_frame(self.session)
            result = await verify_and_record(self.session, self.vision, frame, claimant_claim=claim, source=source)
        except SessionError as exc:
            return {"captured": False, "message": str(exc)}
        self.request_update()
        await self.send_state()
        return capture_summary(result.capture)

    async def _manual_capture(self, claim: str, live: LiveSession) -> None:
        result = await self.capture(claim, source="claimant")
        if not result.get("captured"):
            await self.send({"type": "error", "message": result.get("message") or result.get("error", "Capture failed.")})
            return
        # Let the agent know, as app state rather than caller speech, so it can talk about the photo.
        notice = f"[App notice, not the caller speaking] The caller saved a photo. Verified contents: {result['what_is_visible']}"
        await live.send_client_content(turns=types.Content(role="user", parts=[types.Part(text=notice)]), turn_complete=False)

    async def execute_tool(self, call: types.FunctionCall, live: LiveSession) -> None:
        call_id, name, args = str(call.id or uuid.uuid4().hex), str(call.name or ""), dict(call.args or {})
        activity = ToolActivity(id=call_id, name=name, args=args, phase="running", headline=headline(name, args, None))
        self.session.record_tool(activity)
        await self.send({"type": "tool", **asdict(activity)})
        started = time.monotonic()
        try:
            result, urgent = await self.run_tool(name, args)
            activity.phase = "error" if "error" in result else "done"
        except asyncio.CancelledError:
            activity.phase, activity.headline = "cancelled", "Cancelled"
            self.session.record_tool(activity)
            with contextlib.suppress(Exception):
                await self.send({"type": "tool", **asdict(activity)})
            raise
        except Exception:
            logger.exception("tool %s failed", name)
            result, urgent = {"error": f"{name} failed. Continue the conversation."}, False
            activity.phase = "error"
        sched = scheduling(urgent)
        activity.headline = result.get("error") or headline(name, args, result)
        activity.duration_ms = int((time.monotonic() - started) * 1000)
        activity.scheduling = sched.value
        self.session.record_tool(activity)
        await self.send({"type": "tool", **asdict(activity)})
        await live.send_tool_response(
            function_responses=[types.FunctionResponse(id=call_id, name=name, response=result, scheduling=sched)]
        )

    async def launch_tool(self, call: types.FunctionCall, live: LiveSession) -> None:
        key = str(call.id)
        if key in self._tool_tasks:
            return
        if len(self._tool_tasks) >= MAX_CONCURRENT_TOOLS:
            await live.send_tool_response(
                function_responses=[
                    types.FunctionResponse(id=call.id, name=call.name, response={"error": "Team is busy; try again shortly."})
                ]
            )
            return
        task = self._spawn(self.execute_tool(call, live))
        self._tool_tasks[key] = task
        task.add_done_callback(lambda _t, k=key: self._tool_tasks.pop(k, None))

    # --- browser -> Gemini ---------------------------------------------------

    def _check_rate(self, kind: str) -> None:
        window_s, limit = RATE_LIMITS[kind]
        now = time.monotonic()
        window = self._windows[kind]
        while window and now - window[0] >= window_s:
            window.popleft()
        if len(window) >= limit:
            raise ClientError("Too many messages; slow down.")
        window.append(now)

    async def handle_client_message(self, raw: str, live: LiveSession) -> bool:
        """Process one browser message. Returns False when the browser asked to close."""
        if len(raw) > MAX_MESSAGE_CHARS:
            raise ClientError("Message too large.")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ClientError("Invalid JSON.") from exc
        if not isinstance(message, dict):
            raise ClientError("Expected a JSON object.")
        kind = message.get("type")
        if kind == "close":
            return False
        if kind not in RATE_LIMITS:
            raise ClientError(f"Unknown message type: {kind!r}")
        self._check_rate(kind)
        self.session.touch()

        if kind == "audio":
            try:
                data = base64.b64decode(str(message.get("data", "")), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ClientError("Audio must be base64.") from exc
            if not data or len(data) > MAX_AUDIO_BYTES or len(data) % 2:
                raise ClientError("Audio must be non-empty 16-bit PCM under 64 KB per chunk.")
            await live.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))
        elif kind == "audio_end":
            await live.send_realtime_input(audio_stream_end=True)
        elif kind == "camera":
            enabled = message.get("enabled")
            if not isinstance(enabled, bool):
                raise ClientError("Camera state must be true or false.")
            if self.session.set_camera(enabled):
                notice = f"[App notice, not the caller speaking] The caller's camera is now {'ON' if enabled else 'OFF'}."
                await live.send_client_content(turns=types.Content(role="user", parts=[types.Part(text=notice)]), turn_complete=False)
                await self.send_state()
        elif kind == "video":
            try:
                frame = base64.b64decode(str(message.get("data", "")), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ClientError("Video frames must be base64.") from exc
            if not is_jpeg(frame) or len(frame) > MAX_FRAME_BYTES:
                raise ClientError("Video frames must be JPEG images under 300 KB.")
            if not self.session.camera_on:
                raise ClientError("Turn the camera on before sending frames.")
            self.session.set_frame(frame)
            await live.send_realtime_input(video=types.Blob(data=frame, mime_type="image/jpeg"))
        elif kind == "capture":
            claim = message.get("claim", "")
            if not isinstance(claim, str) or len(claim) > 500:
                raise ClientError("Capture description must be text under 500 characters.")
            self._spawn(self._manual_capture(claim, live))
        elif kind == "text":
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ClientError("Text must be a non-empty string.")
            turn_id = message.get("id")
            if turn_id is not None and (not isinstance(turn_id, str) or len(turn_id) > 64):
                raise ClientError("Invalid message id.")
            await self.finalize("claimant")  # close any half-spoken turn first
            turn = self.session.add_turn("claimant", text, turn_id=turn_id)
            if turn is None:
                return True  # duplicate id: already delivered
            await self.send({"type": "transcript", "speaker": "claimant", "id": turn.id, "text": turn.text, "final": True})
            self.request_update()
            await live.send_client_content(turns=types.Content(role="user", parts=[types.Part(text=turn.text)]), turn_complete=True)
        return True

    async def client_loop(self, live: LiveSession) -> None:
        while True:
            raw = await self.browser.receive_text()
            try:
                if not await self.handle_client_message(raw, live):
                    await self.finalize("claimant")
                    await self.finalize("agent")
                    return
            except (ClientError, SessionError) as exc:
                await self.send({"type": "error", "message": str(exc)})

    # --- Gemini -> browser ---------------------------------------------------

    async def handle_server_message(self, message: types.LiveServerMessage, live: LiveSession) -> None:
        if message.tool_call and message.tool_call.function_calls:
            await self.finalize("claimant")
            for call in message.tool_call.function_calls:
                await self.launch_tool(call, live)
        if message.tool_call_cancellation:
            for call_id in message.tool_call_cancellation.ids or []:
                task = self._tool_tasks.get(str(call_id))
                if task:
                    task.cancel()
        content = message.server_content
        if not content:
            return
        if content.input_transcription and content.input_transcription.text:
            pending = self._pending["claimant"]
            pending["text"] += content.input_transcription.text
            await self.send({"type": "transcript", "speaker": "claimant", "id": pending["id"],
                             "text": clean_transcript(pending["text"]), "final": False})
            if content.input_transcription.finished:
                await self.finalize("claimant")
        if content.output_transcription and content.output_transcription.text:
            await self.finalize("claimant")  # the agent is answering: the claimant's turn is over
            pending = self._pending["agent"]
            pending["text"] += content.output_transcription.text
            await self.send({"type": "transcript", "speaker": "agent", "id": pending["id"],
                             "text": clean_transcript(pending["text"]), "final": False})
        if content.model_turn:
            await self.finalize("claimant")
            for part in content.model_turn.parts or []:
                if part.inline_data and isinstance(part.inline_data.data, bytes):
                    await self.send({"type": "audio", "data": base64.b64encode(part.inline_data.data).decode("ascii")})
        if content.interrupted:
            await self.finalize("agent")
            await self.send({"type": "interrupted"})
        if content.turn_complete:
            await self.finalize("claimant")
            await self.finalize("agent")

    async def server_loop(self, live: LiveSession) -> None:
        while True:  # receive() ends after each model turn; keep listening
            async for message in live.receive():
                await self.handle_server_message(message, live)

    # --- lifecycle -----------------------------------------------------------

    async def run(self) -> None:
        self.session.live_connected = True
        try:
            async with self.connect() as live:
                prior = [
                    types.Content(role="user" if t.speaker == "claimant" else "model", parts=[types.Part(text=t.text)])
                    for t in self.session.turns
                ]
                if self.session.has_claimant_speech:  # reconnect: restore context without prompting a reply
                    await live.send_client_content(turns=prior, turn_complete=False)
                await self.send({"type": "ready", "model": self.model_name})
                await self.send_state()
                loops = [self._spawn(self.client_loop(live)), self._spawn(self.server_loop(live))]
                done, pending = await asyncio.wait(loops, timeout=self.max_seconds, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    await self.send({"type": "error", "message": "Call time limit reached. Reconnect to continue this claim."})
                # Stop the other loop before the Live connection closes; otherwise its receive()
                # sees the normal close (code 1000) and reports it as a failure.
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()  # surface errors from whichever loop ended
        finally:
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            for activity in self.session.tool_activity:
                if activity.phase == "running":
                    activity.phase, activity.headline = "cancelled", "Call ended"
            self.session.live_connected = False
            self.session.set_camera(False)
            self.session.touch()
