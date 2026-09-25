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
from datetime import date, datetime
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
class AuditEvent:
    actor: str  # system | agent | claimant | adjuster
    action: str
    detail: str = ""


@dataclass
class ClaimSession:
    id: str
    owner: str
    turns: list[Turn] = field(default_factory=list)
    captures: list[EvidenceCapture] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    tool_activity: list[ToolActivity] = field(default_factory=list)
    evidence_images: dict[str, bytes] = field(default_factory=dict)
    camera_on: bool = False
    last_frame: bytes | None = None
    last_frame_at: float = 0.0
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
    # Lifecycle (see services/lifecycle.py)
    status: str = "intake"
    frozen_route: str | None = None
    route_override: str | None = None
    override_reason: str | None = None
    submitted_at: datetime | None = None
    pending_audit: list[AuditEvent] = field(default_factory=list)
    # What the repository has already written, so saves only add what's new
    persisted: bool = False
    persisted_turn_ids: set[str] = field(default_factory=set)
    persisted_capture_ids: set[str] = field(default_factory=set)
    persisted_result_revision: int = -1
    persisted_escalations: int = 0

    @property
    def pipeline_route(self) -> str | None:
        return self.result.decision.route.value if self.result else None

    @property
    def effective_route(self) -> str | None:
        """What the adjuster queue shows: an override wins, then a review freeze, then the pipeline."""
        return self.route_override or self.frozen_route or self.pipeline_route

    def audit(self, actor: str, action: str, detail: str = "") -> None:
        self.pending_audit.append(AuditEvent(actor, action, detail))

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

    def set_camera(self, on: bool) -> bool:
        """Returns True if the state changed. Turning the camera off discards the last frame."""
        changed = self.camera_on != on
        self.camera_on = on
        if not on:
            self.last_frame, self.last_frame_at = None, 0.0
        return changed

    def set_frame(self, jpeg: bytes) -> None:
        self.camera_on = True
        self.last_frame, self.last_frame_at = jpeg, time.monotonic()

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
