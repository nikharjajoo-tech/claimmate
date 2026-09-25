"""Claim lifecycle (PRD 6.1, decisions D2-D3).

    intake -> submitted -> in_review <-> awaiting_docs -> closed

The pipeline's route updates freely during intake. Once an adjuster opens a claim, the route is
frozen and only an explicit override (with a reason) changes it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import Route
from app.services.sessions import ClaimSession, SessionError

STATUSES = ("intake", "submitted", "in_review", "awaiting_docs", "closed")
TRANSITIONS: dict[str, set[str]] = {
    "intake": {"submitted"},
    "submitted": {"in_review"},
    "in_review": {"awaiting_docs", "closed"},
    "awaiting_docs": {"in_review", "closed"},
    "closed": set(),
}
ADJUSTER_STATUSES = {"in_review", "awaiting_docs", "closed"}
MIN_REASON_CHARS = 10


def transition(session: ClaimSession, new_status: str, *, actor: str, note: str = "") -> None:
    if new_status not in STATUSES:
        raise SessionError(422, f"Unknown status {new_status!r}.")
    if new_status not in TRANSITIONS[session.status]:
        raise SessionError(409, f"A claim cannot move from {session.status} to {new_status}.")
    previous = session.status
    session.status = new_status
    if new_status == "submitted":
        session.submitted_at = datetime.now(UTC)
    if new_status == "in_review" and session.frozen_route is None:
        session.frozen_route = session.pipeline_route
        session.audit(actor, "route_frozen", f"Route frozen at {session.frozen_route or 'none'} for review.")
    detail = f"{previous} -> {new_status}" + (f": {note.strip()}" if note.strip() else "")
    session.audit(actor, "status_changed", detail)
    session.touch()


def submit(session: ClaimSession, reason: str) -> bool:
    """Hand a finished intake to the adjuster queue. No-op unless the claimant actually said something."""
    if session.status != "intake" or not session.has_claimant_speech:
        return False
    transition(session, "submitted", actor="system", note=reason)
    return True


def override_route(session: ClaimSession, route: str, reason: str, *, actor: str = "adjuster") -> None:
    if session.status not in {"in_review", "awaiting_docs"}:
        raise SessionError(409, "Open the claim for review before overriding its route.")
    try:
        new_route = Route(route).value
    except ValueError as exc:
        raise SessionError(422, f"Unknown route {route!r}.") from exc
    if len(reason.strip()) < MIN_REASON_CHARS:
        raise SessionError(422, f"Give a reason of at least {MIN_REASON_CHARS} characters for the override.")
    previous = session.effective_route
    session.route_override, session.override_reason = new_route, reason.strip()
    session.audit(actor, "route_overridden", f"{previous} -> {new_route}: {reason.strip()}")
    session.touch()
