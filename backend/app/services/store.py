"""Where claim sessions live: in memory while active, in the database always (when configured).

Claimant access follows decision D5: one session per intake. Once a claim is submitted, the
claimant can no longer continue it; it belongs to the adjuster queue.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid

from app.services import lifecycle
from app.services.sessions import (
    GREETING,
    MAX_SESSIONS,
    MAX_SESSIONS_PER_OWNER,
    SESSION_TTL_S,
    ClaimSession,
    SessionError,
)
from app.services.review_runner import ReviewRunner
from app.storage.repository import ClaimRepository

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, repo: ClaimRepository | None = None, reviews: ReviewRunner | None = None) -> None:
        self.repo = repo
        self.reviews = reviews  # wording reviews, generated in the background after submission
        self._sessions: dict[str, ClaimSession] = {}

    async def save(self, session: ClaimSession) -> None:
        if self.repo is not None and not session.deleted:
            await self.repo.save(session)

    async def create(self, owner: str) -> ClaimSession:
        await self.sweep()
        if len(self._sessions) >= MAX_SESSIONS:
            raise SessionError(429, "The server is at capacity. Try again later.")
        if sum(s.owner == owner and s.status == "intake" for s in self._sessions.values()) >= MAX_SESSIONS_PER_OWNER:
            raise SessionError(429, "Too many open intakes. Close one before starting another.")
        session = ClaimSession(id=uuid.uuid4().hex, owner=owner)
        session.add_turn("agent", GREETING, turn_id="t0")
        self._sessions[session.id] = session
        await self.save(session)
        return session

    async def _lookup(self, claim_id: str) -> ClaimSession | None:
        session = self._sessions.get(claim_id)
        if session is None and self.repo is not None:
            session = await self.repo.load(claim_id)  # e.g. after a server restart
            if session is not None:
                self._sessions[claim_id] = session
        return None if session is None or session.deleted else session

    async def get(self, claim_id: str, owner: str | None, *, readonly: bool = False) -> ClaimSession:
        """Claimant access: owner cookie required. Changes need intake status (D5); reading the
        claimant's own claim (notebook, photos, packet) stays allowed after submission."""
        session = await self._lookup(claim_id)
        if session is None or not owner or not secrets.compare_digest(session.owner, owner):
            raise SessionError(404, "Intake not found or expired. Start a new intake.")
        if session.status == "intake" and not session.live_connected and time.monotonic() - session.updated_at > SESSION_TTL_S:
            await self._retire(session, "idle for 30 minutes")
            raise SessionError(410, "Intake expired. Start a new intake.")
        if session.status != "intake" and not readonly:
            raise SessionError(410, "This claim was submitted for review. Start a new intake to report another loss.")
        session.touch()
        return session

    async def get_for_adjuster(self, claim_id: str) -> ClaimSession:
        session = await self._lookup(claim_id)
        if session is None:
            raise SessionError(404, "Claim not found.")
        return session

    async def finish(self, session: ClaimSession, reason: str) -> None:
        """The claimant is done with this intake: submit it if they said anything, else discard it."""
        await self._retire(session, reason)

    async def _retire(self, session: ClaimSession, reason: str) -> None:
        if session.has_claimant_speech or session.captures:
            lifecycle.submit(session, reason)
            await self.save(session)
            if self.reviews is not None:  # one wording review per claim, in the background (FR-10.2)
                self.reviews.schedule(session)
            self._sessions.pop(session.id, None)
        else:
            await self.delete(session)

    async def delete(self, session: ClaimSession) -> None:
        session.deleted = True
        self._sessions.pop(session.id, None)
        if self.repo is not None:
            await self.repo.delete(session.id)

    async def sweep(self) -> int:
        """Retire idle intakes (submit or discard) and drop idle reviewed claims from memory."""
        now = time.monotonic()
        idle = [s for s in list(self._sessions.values()) if now - s.updated_at > SESSION_TTL_S and not s.live_connected]
        for session in idle:
            try:
                if session.status == "intake":
                    await self._retire(session, "idle for 30 minutes")
                else:
                    await self.save(session)
                    self._sessions.pop(session.id, None)
            except Exception:
                logger.exception("could not retire claim %s", session.id)
        return len(idle)

    def live_claim_ids(self) -> set[str]:
        return {s.id for s in self._sessions.values() if s.live_connected}

    def __len__(self) -> int:
        return len(self._sessions)
