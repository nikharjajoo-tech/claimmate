"""Claim sessions: the transcript of one intake plus its latest pipeline result.

In-memory for now; persistence arrives in M6 behind the same interface.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain.models import EvidenceCapture
from app.pipeline.graph import PipelineResult
from app.pipeline.prompts import Turn

MAX_TURNS = 300
MAX_TRANSCRIPT_CHARS = 64_000
MAX_TURN_CHARS = 4_000
SESSION_TTL_S = 30 * 60
MAX_SESSIONS = 64
MAX_SESSIONS_PER_OWNER = 4
PIPELINE_TIMEOUT_S = 90

GREETING = "I can start your claim while we talk. First, is everyone safe right now?"

PipelineRunner = Callable[..., Awaitable[PipelineResult]]


class SessionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ToolActivity:
    id: str
    name: str
    args: dict[str, Any]
    phase: str  # running | done | error | cancelled
    headline: str
    duration_ms: int | None = None
    scheduling: str | None = None


@dataclass
class ClaimSession:
    id: str
    owner: str
    turns: list[Turn] = field(default_factory=list)
    captures: list[EvidenceCapture] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    tool_activity: list[ToolActivity] = field(default_factory=list)
    revision: int = 0  # bumps on anything the pipeline reads
    result: PipelineResult | None = None
    result_revision: int = -1
    last_error: str = ""
    processing: bool = False
    live_connected: bool = False
    deleted: bool = False
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.updated_at = time.monotonic()

    def add_turn(self, speaker: str, text: str, turn_id: str | None = None) -> Turn | None:
        """Append a turn. Returns None for empty text or a duplicate id (idempotent retries)."""
        text = text.strip()[:MAX_TURN_CHARS]
        if not text:
            return None
        turn_id = turn_id or f"t{uuid.uuid4().hex[:10]}"
        if any(t.id == turn_id for t in self.turns):
            return None
        if len(self.turns) >= MAX_TURNS or sum(len(t.text) for t in self.turns) + len(text) > MAX_TRANSCRIPT_CHARS:
            raise SessionError(413, "This intake reached its conversation limit. Start a new intake.")
        turn = Turn(id=turn_id, speaker=speaker, text=text)
        self.turns.append(turn)
        if speaker == "claimant":
            self.revision += 1  # agent turns are context only; they never change extracted facts alone
        self.touch()
        return turn

    def escalate(self, reason: str) -> None:
        self.escalations.append(reason.strip()[:300] or "unspecified")
        self.revision += 1
        self.touch()

    def record_tool(self, activity: ToolActivity) -> None:
        self.tool_activity = [a for a in self.tool_activity if a.id != activity.id] + [activity]
        self.tool_activity = self.tool_activity[-30:]

    @property
    def has_claimant_speech(self) -> bool:
        return any(t.speaker == "claimant" for t in self.turns)


class ClaimService:
    """Runs the pipeline for a session, at most once per revision."""

    def __init__(self, runner: PipelineRunner, *, today: Callable[[], date] = date.today) -> None:
        self._runner = runner
        self._today = today

    async def refresh(self, session: ClaimSession) -> PipelineResult:
        async with session.lock:
            while True:
                if session.deleted:
                    raise asyncio.CancelledError()
                revision = session.revision
                if session.result is not None and session.result_revision == revision:
                    return session.result
                session.processing = True
                try:
                    result = await asyncio.wait_for(
                        self._runner(
                            list(session.turns),
                            observations=list(session.observations),
                            captures=list(session.captures),
                            escalations=list(session.escalations),
                            today=self._today(),
                        ),
                        PIPELINE_TIMEOUT_S,
                    )
                    session.last_error = ""
                except Exception as exc:
                    session.last_error = f"{type(exc).__name__}: {exc}"[:300]
                    raise
                finally:
                    session.processing = False
                if session.revision != revision:
                    continue  # the claimant spoke while we ran: this result is already stale
                session.result, session.result_revision = result, revision
                return result


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ClaimSession] = {}

    def create(self, owner: str) -> ClaimSession:
        self.sweep()
        if len(self._sessions) >= MAX_SESSIONS:
            raise SessionError(429, "The server is at capacity. Try again later.")
        if sum(s.owner == owner for s in self._sessions.values()) >= MAX_SESSIONS_PER_OWNER:
            raise SessionError(429, "Too many open intakes. Close one before starting another.")
        session = ClaimSession(id=uuid.uuid4().hex, owner=owner)
        session.add_turn("agent", GREETING, turn_id="t0")
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str, owner: str | None) -> ClaimSession:
        session = self._sessions.get(session_id)
        if session is None or session.deleted or not owner or not secrets.compare_digest(session.owner, owner):
            raise SessionError(404, "Intake not found or expired. Start a new intake.")
        if time.monotonic() - session.updated_at > SESSION_TTL_S:
            self.delete(session)
            raise SessionError(410, "Intake expired. Start a new intake.")
        session.touch()
        return session

    def delete(self, session: ClaimSession) -> None:
        session.deleted = True
        self._sessions.pop(session.id, None)

    def sweep(self) -> int:
        now = time.monotonic()
        expired = [s for s in self._sessions.values() if now - s.updated_at > SESSION_TTL_S and not s.live_connected]
        for session in expired:
            self.delete(session)
        return len(expired)

    def __len__(self) -> int:
        return len(self._sessions)
