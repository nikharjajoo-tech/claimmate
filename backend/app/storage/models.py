"""Database tables. SQLite in M6; the same schema targets Turso (libSQL) or Postgres later (PRD D7)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ClaimRow(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="intake")
    # Routing: the pipeline's latest route, plus the adjuster-controlled freeze and override.
    pipeline_route: Mapped[str | None] = mapped_column(String(32))
    frozen_route: Mapped[str | None] = mapped_column(String(32))
    route_override: Mapped[str | None] = mapped_column(String(32))
    override_reason: Mapped[str | None] = mapped_column(Text)
    # Denormalized for the queue, so listing claims never parses result_json.
    claim_type: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str | None] = mapped_column(String(16))
    claimant_name: Mapped[str | None] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    result_revision: Mapped[int] = mapped_column(Integer, default=-1)
    result_json: Mapped[str | None] = mapped_column(Text)  # full PipelineResult, restores the view after restart
    escalations: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TurnRow(Base):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("claim_id", "turn_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str] = mapped_column(String(64))  # the id facts cite in fact_sources
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceRow(Base):
    __tablename__ = "evidence_captures"

    capture_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    file_path: Mapped[str] = mapped_column(String(500))  # empty when the image is stored in the database
    image: Mapped[bytes | None] = mapped_column(LargeBinary)  # used on hosts without a persistent disk
    caption: Mapped[str] = mapped_column(Text, default="")
    claimant_claim: Mapped[str] = mapped_column(Text, default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    document_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(16))
    captured_at: Mapped[str] = mapped_column(String(40), default="")


class FindingRow(Base):
    """The rule findings from the claim's latest pipeline run (replaced on each run)."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(20), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)


class AuditRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(16))  # system | agent | claimant | adjuster
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PipelineRunRow(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    route: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    models: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VoiceLatencyRow(Base):
    """Time from the end of a claimant turn to the agent's first audio (PRD target: p50 < 1.5 s).

    Measured on the server: from the finalized claimant transcript to the first audio chunk from the
    voice model, so it excludes the browser's network hop.
    """

    __tablename__ = "voice_latency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    first_audio_ms: Mapped[int] = mapped_column(Integer)
    turn_kind: Mapped[str] = mapped_column(String(8))  # spoken | typed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WordingReviewRow(Base):
    """The claim's wording review (F13). One row per claim, replaced when it is regenerated.

    Named for the panel, not for the `policy_review` route, which is a different thing entirely
    (POLICY-001: the declarations page did not check out).

    Per-generation history lives in the audit trail; this row carries the current result plus
    cumulative usage, so the operations panel can count what the feature costs.
    """

    __tablename__ = "wording_reviews"

    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending | ready | failed
    review_json: Mapped[str | None] = mapped_column(Text)  # the full PolicyReview once it is ready
    wording_ref: Mapped[str] = mapped_column(String(40), default="")  # e.g. "medical/v1"
    prompt_version: Mapped[str] = mapped_column(String(10), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    pipeline_revision: Mapped[int | None] = mapped_column(Integer)  # the pipeline result it read
    runs: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)  # cumulative across generations
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)  # the most recent generation
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
