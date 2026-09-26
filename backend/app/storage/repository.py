"""Claim persistence. The in-memory ClaimSession is the working copy during a call; save() writes
whatever changed since the last save, and load() rebuilds a session after a restart.

System audit events (route changes, new evidence, agent escalations) are derived here by comparing
the session with what was last written, so callers can't forget to log them.
"""

from __future__ import annotations

import asyncio
import math
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.models import EvidenceCapture, Route, is_blank
from app.pipeline.graph import PipelineResult
from app.pipeline.prompts import Turn
from app.services.sessions import ClaimSession
from app.storage.models import AuditRow, ClaimRow, EvidenceRow, FindingRow, PipelineRunRow, TurnRow, VoiceLatencyRow

ROUTE_ORDER = {route.value: i for i, route in enumerate(Route)}
ACTOR_FOR_SOURCE = {"agent": "agent", "claimant": "claimant", "upload": "claimant"}


class ClaimRepository:
    def __init__(self, sessionmaker: async_sessionmaker, evidence_dir: Path) -> None:
        self._sessionmaker = sessionmaker
        self.evidence_dir = evidence_dir
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, claim_id: str) -> asyncio.Lock:
        return self._locks.setdefault(claim_id, asyncio.Lock())

    def _image_path(self, claim_id: str, capture_id: str) -> Path:
        return self.evidence_dir / claim_id / f"{capture_id}.jpg"

    # --- write ---------------------------------------------------------------

    async def save(self, s: ClaimSession) -> None:
        async with self._lock(s.id):  # a live call and an API request may save the same claim at once
            await self._save(s)

    async def _save(self, s: ClaimSession) -> None:
        now = datetime.now(UTC)
        new_turns = [(i, t) for i, t in enumerate(s.turns) if t.id not in s.persisted_turn_ids]
        new_captures = [c for c in s.captures if c.capture_id not in s.persisted_capture_ids]
        new_escalations = s.escalations[s.persisted_escalations :]
        result_changed = s.result is not None and s.result_revision != s.persisted_result_revision
        captured_events = len(s.pending_audit)  # events added during this save stay pending for the next one
        audit = [_event(e.actor, e.action, e.detail) for e in s.pending_audit[:captured_events]]

        for capture in new_captures:  # files first: a row must never point at a missing image
            path = self._image_path(s.id, capture.capture_id)
            await asyncio.to_thread(_write_file, path, s.evidence_images[capture.capture_id])

        async with self._sessionmaker() as db, db.begin():
            row = await db.get(ClaimRow, s.id)
            if row is None:
                row = ClaimRow(id=s.id, owner=s.owner, created_at=now)
                db.add(row)
                await db.flush()  # child rows reference the claim; insert it first
                audit.insert(0, _event("system", "claim_created", "Intake started."))
            previous_route = row.pipeline_route

            row.status = s.status
            row.frozen_route, row.route_override, row.override_reason = s.frozen_route, s.route_override, s.override_reason
            row.revision, row.escalations = s.revision, list(s.escalations)
            row.submitted_at, row.updated_at = s.submitted_at, now

            for seq, turn in new_turns:
                db.add(TurnRow(claim_id=s.id, turn_id=turn.id, seq=seq, speaker=turn.speaker, text=turn.text))
            for c in new_captures:
                db.add(EvidenceRow(
                    capture_id=c.capture_id, claim_id=s.id, file_path=str(self._image_path(s.id, c.capture_id)),
                    caption=c.caption, claimant_claim=c.claimant_claim, confirmed=c.confirmed,
                    document_types=[t.value for t in c.document_types], source=c.source, captured_at=c.captured_at,
                ))
                verdict = "confirmed" if c.confirmed else "not confirmed"
                audit.append(_event(ACTOR_FOR_SOURCE.get(c.source, "claimant"), "evidence_added",
                                    f"{c.capture_id}: {c.caption} ({verdict})"))
            for reason in new_escalations:
                audit.append(_event("agent", "escalated", reason))

            if result_changed:
                result = s.result
                row.pipeline_route = result.decision.route.value
                row.claim_type = result.classification.claim_type.value
                row.severity = result.classification.severity.value
                if not is_blank(result.facts.policyholder_name):
                    row.claimant_name = result.facts.policyholder_name[:200]
                row.result_json, row.result_revision = result.model_dump_json(), s.result_revision
                await db.execute(delete(FindingRow).where(FindingRow.claim_id == s.id))
                for f in result.decision.findings:
                    db.add(FindingRow(claim_id=s.id, rule_id=f.rule_id, severity=f.severity.value,
                                      action=f.action.value, message=f.message))
                tokens_in, tokens_out = result.tokens
                db.add(PipelineRunRow(
                    claim_id=s.id, revision=s.result_revision, route=row.pipeline_route, latency_ms=result.total_ms,
                    tokens_in=tokens_in, tokens_out=tokens_out, models=sorted({c.model for c in result.llm_calls}),
                ))
                if previous_route != row.pipeline_route:
                    audit.append(_event("system", "route_changed", f"{previous_route or 'none'} -> {row.pipeline_route}"))

            for actor, action, detail in audit:
                db.add(AuditRow(claim_id=s.id, actor=actor, action=action, detail=detail[:2000]))

        # Only after a successful commit: mark what is now durable.
        s.persisted = True
        s.persisted_turn_ids.update(t.id for _, t in new_turns)
        s.persisted_capture_ids.update(c.capture_id for c in new_captures)
        s.persisted_escalations = len(s.escalations)
        if result_changed:
            s.persisted_result_revision = s.result_revision
        del s.pending_audit[:captured_events]

    async def delete(self, claim_id: str) -> None:
        async with self._sessionmaker() as db, db.begin():
            for table in (AuditRow, PipelineRunRow, FindingRow, EvidenceRow, TurnRow, VoiceLatencyRow):
                await db.execute(delete(table).where(table.claim_id == claim_id))
            await db.execute(delete(ClaimRow).where(ClaimRow.id == claim_id))
        await asyncio.to_thread(shutil.rmtree, self.evidence_dir / claim_id, True)
        self._locks.pop(claim_id, None)

    async def record_voice_latency(self, claim_id: str, first_audio_ms: int, turn_kind: str) -> None:
        async with self._sessionmaker() as db, db.begin():
            db.add(VoiceLatencyRow(claim_id=claim_id, first_audio_ms=first_audio_ms, turn_kind=turn_kind))

    # --- read ----------------------------------------------------------------

    async def load(self, claim_id: str) -> ClaimSession | None:
        async with self._sessionmaker() as db:
            row = await db.get(ClaimRow, claim_id)
            if row is None:
                return None
            turns = (await db.scalars(select(TurnRow).where(TurnRow.claim_id == claim_id).order_by(TurnRow.seq))).all()
            evidence = (await db.scalars(select(EvidenceRow).where(EvidenceRow.claim_id == claim_id))).all()

        idle_s = (datetime.now(UTC) - _aware(row.updated_at)).total_seconds()
        s = ClaimSession(id=row.id, owner=row.owner)
        s.updated_at = time.monotonic() - max(0.0, idle_s)
        s.status, s.submitted_at = row.status, _aware(row.submitted_at) if row.submitted_at else None
        s.frozen_route, s.route_override, s.override_reason = row.frozen_route, row.route_override, row.override_reason
        s.turns = [Turn(id=t.turn_id, speaker=t.speaker, text=t.text) for t in turns]
        s.escalations = list(row.escalations or [])
        for e in evidence:
            capture = EvidenceCapture(
                capture_id=e.capture_id, document_types=e.document_types, caption=e.caption, confirmed=e.confirmed,
                claimant_claim=e.claimant_claim, source=e.source, captured_at=e.captured_at,
            )
            s.captures.append(capture)
            s.observations.append(
                f"Capture {e.capture_id}: {e.caption} (claimant said: {e.claimant_claim or 'nothing'}; "
                f"{'confirmed' if e.confirmed else 'not confirmed'})"
            )
            path = Path(e.file_path)
            if path.exists():
                s.evidence_images[e.capture_id] = await asyncio.to_thread(path.read_bytes)
        s.revision = row.revision
        if row.result_json:
            s.result = PipelineResult.model_validate_json(row.result_json)
            s.result_revision = row.result_revision
        s.persisted = True
        s.persisted_turn_ids = {t.id for t in s.turns}
        s.persisted_capture_ids = {c.capture_id for c in s.captures}
        s.persisted_result_revision = s.result_revision
        s.persisted_escalations = len(s.escalations)
        return s

    async def queue(
        self, *, statuses: set[str] | None = None, route: str | None = None, claim_type: str | None = None
    ) -> list[dict[str, Any]]:
        async with self._sessionmaker() as db:
            query = select(ClaimRow)
            if statuses:
                query = query.where(ClaimRow.status.in_(statuses))
            if claim_type:
                query = query.where(ClaimRow.claim_type == claim_type)
            rows = (await db.scalars(query)).all()
        items = []
        for r in rows:
            effective = r.route_override or r.frozen_route or r.pipeline_route
            if route and effective != route:
                continue
            items.append({
                "id": r.id, "status": r.status, "route": effective, "pipeline_route": r.pipeline_route,
                "overridden": r.route_override is not None, "claim_type": r.claim_type, "severity": r.severity,
                "claimant_name": r.claimant_name, "created_at": _aware(r.created_at).isoformat(),
                "updated_at": _aware(r.updated_at).isoformat(),
                "submitted_at": _aware(r.submitted_at).isoformat() if r.submitted_at else None,
            })
        # Urgent routes first; oldest first within a route. Claims with no route yet go last.
        items.sort(key=lambda i: (ROUTE_ORDER.get(i["route"], len(ROUTE_ORDER)), i["created_at"]))
        return items

    async def audit_log(self, claim_id: str) -> list[dict[str, str]]:
        async with self._sessionmaker() as db:
            rows = (await db.scalars(
                select(AuditRow).where(AuditRow.claim_id == claim_id).order_by(AuditRow.created_at, AuditRow.id)
            )).all()
        return [{"at": _aware(r.created_at).isoformat(timespec="seconds"), "actor": r.actor,
                 "action": r.action, "detail": r.detail} for r in rows]


async def _column(db, column) -> list:
    return list((await db.scalars(select(column))).all())


def percentile(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile; None when there is no data (never a misleading zero)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))  # nearest rank: the smallest value covering q%
    return ordered[min(rank, len(ordered)) - 1]


async def operations_metrics(repo: ClaimRepository) -> dict[str, Any]:
    """Live service metrics from stored data (PRD section 8)."""
    async with repo._sessionmaker() as db:
        claims = (await db.execute(select(ClaimRow.status, ClaimRow.pipeline_route, ClaimRow.frozen_route,
                                          ClaimRow.route_override))).all()
        runs = (await db.execute(select(PipelineRunRow.latency_ms, PipelineRunRow.tokens_in,
                                        PipelineRunRow.tokens_out, PipelineRunRow.models))).all()
        voice = await _column(db, VoiceLatencyRow.first_audio_ms)
    by_status: dict[str, int] = {}
    by_route: dict[str, int] = {}
    for status, pipeline_route, frozen, override in claims:
        by_status[status] = by_status.get(status, 0) + 1
        route = override or frozen or pipeline_route or "none"
        by_route[route] = by_route.get(route, 0) + 1
    latencies = [r.latency_ms for r in runs]
    models: dict[str, int] = {}
    for r in runs:
        for m in r.models or []:
            models[m] = models.get(m, 0) + 1
    reviewed = sum(1 for c in claims if c.frozen_route or c.route_override)
    return {
        "claims": {"total": len(claims), "by_status": by_status, "by_route": by_route},
        "pipeline": {
            "runs": len(runs),
            "latency_p50_ms": percentile(latencies, 50),
            "latency_p95_ms": percentile(latencies, 95),
            "tokens_in": sum(r.tokens_in for r in runs),
            "tokens_out": sum(r.tokens_out for r in runs),
            "runs_by_model": models,
        },
        "voice": {
            "turns": len(voice),
            "first_audio_p50_ms": percentile(voice, 50),
            "first_audio_p95_ms": percentile(voice, 95),
            "target_p50_ms": 1500,
        },
        "review": {
            "reviewed": reviewed,
            "overridden": sum(1 for c in claims if c.route_override),
        },
    }


def _event(actor: str, action: str, detail: str) -> tuple[str, str, str]:
    return actor, action, detail


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they were written as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
