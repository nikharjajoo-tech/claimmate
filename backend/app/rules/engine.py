"""Deterministic intake rules engine.

LLMs extract and classify; this module alone decides routing. It is pure (no I/O besides
loading the YAML config once), so every routing outcome is reproducible and unit-testable.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.domain.models import (
    Action,
    ChecklistItem,
    ClaimFacts,
    ClaimType,
    Classification,
    Decision,
    DocumentType,
    EvidenceCapture,
    EvidenceRecord,
    EvidenceStatus,
    Finding,
    PolicyLookup,
    Route,
    Severity,
    Validation,
    is_blank,
)
from app.domain.policy_store import policy_issues

class DocumentRule(BaseModel):
    type: DocumentType
    label: str
    reason: str


class Thresholds(BaseModel):
    high_loss_usd: float
    unsupported_loss_usd: float
    late_report_days: int


class RulesConfig(BaseModel):
    thresholds: Thresholds
    required_fields: dict[str, str]
    documents: dict[ClaimType, list[DocumentRule]]
    coverage_notes: dict[ClaimType, list[str]]


@lru_cache
def load_config(path: Path | None = None) -> RulesConfig:
    path = path or Path(__file__).with_name("rules.yaml")
    return RulesConfig.model_validate(yaml.safe_load(path.read_text()))


def _parse_iso(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


# --- Evidence ----------------------------------------------------------------


def apply_captures(facts: ClaimFacts, captures: list[EvidenceCapture]) -> ClaimFacts:
    """Enforce the evidence trust ladder.

    The LLM may report missing/planned/available, never received. Any RECEIVED it emits is
    downgraded, and only server-verified captures are promoted to RECEIVED.
    """
    records = [
        record.model_copy(
            update={
                "status": EvidenceStatus.AVAILABLE if record.status == EvidenceStatus.RECEIVED else record.status,
                "capture_ids": [],
            }
        )
        for record in facts.evidence
    ]
    for capture in captures:
        for doc_type in capture.document_types:
            records.append(
                EvidenceRecord(document_type=doc_type, status=EvidenceStatus.RECEIVED, capture_ids=[capture.capture_id])
            )
    return facts.model_copy(update={"evidence": records})


def _document_status(facts: ClaimFacts, doc_type: DocumentType) -> tuple[EvidenceStatus, list[str]]:
    records = [r for r in facts.evidence if r.document_type == doc_type]
    received = [cid for r in records if r.status == EvidenceStatus.RECEIVED for cid in r.capture_ids]
    if received:
        return EvidenceStatus.RECEIVED, received
    # Latest statement wins: "no photos" followed by "actually I found some" -> available.
    return (records[-1].status if records else EvidenceStatus.UNKNOWN), []


def build_checklist(facts: ClaimFacts, claim_type: ClaimType, config: RulesConfig) -> list[ChecklistItem]:
    items = []
    for rule in config.documents[claim_type]:
        status, capture_ids = _document_status(facts, rule.type)
        items.append(
            ChecklistItem(document_type=rule.type, label=rule.label, reason=rule.reason, status=status, capture_ids=capture_ids)
        )
    return items


# --- Validation --------------------------------------------------------------


def validate_fields(facts: ClaimFacts, today: date, config: RulesConfig) -> Validation:
    missing = [name for name in config.required_fields if is_blank(getattr(facts, name))]
    invalid = []
    for name in ("date_of_loss", "reported_date"):
        value = getattr(facts, name)
        if is_blank(value):
            continue
        parsed = _parse_iso(value)
        if parsed is None:
            invalid.append(f"{name}: not a valid calendar date")
        elif parsed > today:
            invalid.append(f"{name}: in the future")
    return Validation(missing_fields=missing, invalid_fields=invalid)


# --- Evaluation --------------------------------------------------------------

_ROUTE_FOR_ACTION = {
    Action.EMERGENCY_ESCALATION: Route.EMERGENCY_ESCALATION,
    Action.SIU_REVIEW: Route.SPECIAL_INVESTIGATION,
    Action.POLICY_REVIEW: Route.POLICY_REVIEW,
    Action.COLLECT_INFO: Route.NEEDS_DOCS,
    Action.COLLECT_DOCUMENT: Route.NEEDS_DOCS,
}
_ROUTE_PRECEDENCE = list(Route)


def select_route(findings: list[Finding]) -> Route:
    candidates = {_ROUTE_FOR_ACTION[f.action] for f in findings if f.action in _ROUTE_FOR_ACTION}
    return next((route for route in _ROUTE_PRECEDENCE if route in candidates), Route.READY_FOR_ADJUSTER)


def evaluate(
    facts: ClaimFacts,
    classification: Classification,
    policy: PolicyLookup,
    captures: list[EvidenceCapture] | None = None,
    *,
    today: date | None = None,
    config: RulesConfig | None = None,
) -> Decision:
    config = config or load_config()
    today = today or date.today()
    facts = apply_captures(facts, captures or [])
    t = config.thresholds
    findings: list[Finding] = []

    def add(rule_id: str, severity: Severity, action: Action, message: str, doc: DocumentType | None = None) -> None:
        findings.append(Finding(rule_id=rule_id, severity=severity, action=action, message=message, document_type=doc))

    # Intake completeness
    validation = validate_fields(facts, today, config)
    if validation.missing_fields:
        add("INTAKE-001", Severity.MEDIUM, Action.COLLECT_INFO, f"Missing: {', '.join(validation.missing_fields)}.")
    for problem in validation.invalid_fields:
        add("INTAKE-002", Severity.MEDIUM, Action.COLLECT_INFO, f"Invalid date: {problem}.")

    # Documents
    checklist = build_checklist(facts, classification.claim_type, config)
    for item in checklist:
        if not item.satisfied:
            add("DOC-001", Severity.MEDIUM, Action.COLLECT_DOCUMENT, f"{item.label} not yet received ({item.status}).", item.document_type)

    # Safety: structured facts only. "Nobody was hurt" is extracted as status=absent and never fires.
    for fact in facts.safety_facts:
        if fact.status in ("present", "uncertain"):
            add("SAFE-001", Severity.URGENT, Action.EMERGENCY_ESCALATION, f"{fact.category} ({fact.status}): {fact.description}")

    # Policy verification (skipped while the number is simply missing; INTAKE-001 covers that)
    if not is_blank(facts.policy_number):
        for issue in policy_issues(facts, policy):
            add("POLICY-001", Severity.HIGH, Action.POLICY_REVIEW, issue)

    # Loss size and supporting evidence
    amount = facts.estimated_loss_usd
    if amount is not None and amount >= t.high_loss_usd:
        add("LOSS-001", Severity.HIGH, Action.ADJUSTER_REVIEW, f"Estimated loss ${amount:,.0f} needs prompt adjuster review.")
    has_evidence = any(r.status in (EvidenceStatus.AVAILABLE, EvidenceStatus.RECEIVED) for r in facts.evidence)
    if amount is not None and amount >= t.unsupported_loss_usd and not has_evidence:
        add("EVID-001", Severity.HIGH, Action.SIU_REVIEW, f"Estimated loss ${amount:,.0f} reported with no supporting evidence.")

    # Timing. When no report date is stated, the intake itself (today) is the report.
    loss_date = _parse_iso(facts.date_of_loss)
    report_date = _parse_iso(facts.reported_date) or today
    if loss_date and loss_date <= today:
        if report_date < loss_date:
            add("TIMING-001", Severity.HIGH, Action.SIU_REVIEW, "Reported date is before the loss date.")
        elif (report_date - loss_date).days > t.late_report_days:
            add("TIMING-002", Severity.MEDIUM, Action.SIU_REVIEW, f"Reported {(report_date - loss_date).days} days after the loss.")

    route = select_route(findings)
    return Decision(
        route=route,
        validation=validation,
        findings=findings,
        checklist=checklist,
        coverage_notes=config.coverage_notes[classification.claim_type],
        audit_trail=[
            f"Classified as {classification.claim_type} ({classification.severity}).",
            f"Policy lookup: {'found' if policy.found else 'not found'}.",
            f"Rules fired: {', '.join(sorted({f.rule_id for f in findings})) or 'none'}.",
            f"Route: {route}.",
        ],
    )


def next_question(decision: Decision, config: RulesConfig | None = None) -> str:
    """The single most useful thing to ask the claimant next."""
    config = config or load_config()
    if decision.route == Route.EMERGENCY_ESCALATION:
        return (
            "If anyone is in danger or hurt, please contact emergency services first. "
            "I'm flagging this claim for a human representative to review right away."
        )
    for name in decision.validation.missing_fields:
        return config.required_fields[name]
    if decision.validation.invalid_fields:
        return "Could you confirm the exact date this happened?"
    if decision.route == Route.POLICY_REVIEW:
        return "Could you confirm your policy number and the name on the policy?"
    pending = [item for item in decision.checklist if item.status in (EvidenceStatus.UNKNOWN, EvidenceStatus.MISSING)]
    if pending:
        return f"Do you have the {pending[0].label.lower()}? You can show it on camera."
    return "I have what I need to start your claim. A human adjuster will review it and follow up."
