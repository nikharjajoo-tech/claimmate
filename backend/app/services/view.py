"""JSON snapshot of a session for the UI. The frontend renders this; it holds no claim logic."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.domain.models import is_blank
from app.domain.policy_store import names_match
from app.services.sessions import ClaimSession

FIELD_LABELS = {
    "policyholder_name": "Name",
    "policy_number": "Policy",
    "contact": "Contact",
    "date_of_loss": "Date of loss",
    "loss_location": "Location",
    "loss_description": "What happened",
}


def _field(key: str, label: str, facts: Any, policy: Any) -> dict[str, Any]:
    """One row of the claim notebook.

    The policyholder's name is shown as the declarations page spells it, once the policy has been
    verified and the spoken name matches it. Speech-to-text hears "Grace Lee" for "Grace Liu", and
    the record is authoritative for spelling. What the claimant actually said is kept beside it
    rather than overwritten: only their words are facts, and a reviewer should see both.
    """
    value = None if is_blank(getattr(facts, key)) else getattr(facts, key)
    row: dict[str, Any] = {"key": key, "label": label, "value": value}
    if key == "policyholder_name" and value and policy is not None and policy.found and policy.record:
        recorded = policy.record.policyholder_name
        if recorded != value and names_match(value, recorded):
            row["value"] = recorded
            row["note"] = f"heard “{value}”"
    return row


def session_view(session: ClaimSession, *, evidence_url_prefix: str | None = None) -> dict[str, Any]:
    evidence_url_prefix = evidence_url_prefix or f"/api/claims/{session.id}/evidence"
    result = session.result
    view: dict[str, Any] = {
        "id": session.id,
        "revision": session.revision,
        "up_to_date": result is not None and session.result_revision == session.revision,
        "processing": session.processing,
        "live_connected": session.live_connected,
        "error": session.last_error,
        "transcript": [t.model_dump() for t in session.turns],
        "tool_activity": [asdict(a) for a in session.tool_activity[-12:]],
        "escalations": list(session.escalations),
        "camera_on": session.camera_on,
        "status": session.status,
        "pipeline_route": session.pipeline_route,
        "route_frozen": session.frozen_route is not None,
        "route_override": session.route_override,
        "override_reason": session.override_reason,
        "evidence": [
            {
                "capture_id": c.capture_id,
                "caption": c.caption,
                "confirmed": c.confirmed,
                "claimant_claim": c.claimant_claim,
                "document_types": [t.value for t in c.document_types],
                "source": c.source,
                "url": f"{evidence_url_prefix}/{c.capture_id}",
            }
            for c in session.captures
        ],
    }
    if result is None:
        view.update(
            route=None,
            fact_sources={},
            claim_type=None,
            severity=None,
            rationale="",
            fields=[{"key": k, "label": label, "value": None} for k, label in FIELD_LABELS.items()],
            estimated_loss_usd=None,
            safety=[],
            checklist=[],
            findings=[],
            policy=None,
            next_question="",
            packet_markdown="",
        )
        return view

    facts, decision, policy = result.facts, result.decision, result.policy
    view.update(
        route=session.effective_route,
        fact_sources={src.field: src.source_turn_ids for src in facts.fact_sources},
        claim_type=result.classification.claim_type.value,
        severity=result.classification.severity.value,
        rationale=result.classification.rationale,
        fields=[_field(k, label, facts, policy) for k, label in FIELD_LABELS.items()],
        estimated_loss_usd=facts.estimated_loss_usd,
        safety=[s.model_dump() for s in facts.safety_facts],
        checklist=[
            {"label": i.label, "status": i.status.value, "satisfied": i.satisfied, "reason": i.reason}
            for i in decision.checklist
        ],
        findings=[
            {"rule_id": f.rule_id, "severity": f.severity.value, "message": f.message} for f in decision.findings
        ],
        policy=(
            {
                "found": True,
                "number": policy.record.policy_number,
                "holder": policy.record.policyholder_name,
                "line": policy.record.policy_line,
                "status": policy.record.status,
            }
            if policy.found and policy.record
            else {"found": False, "message": policy.message}
        ),
        next_question=result.packet.next_question,
        packet_markdown=result.packet.markdown,
    )
    return view
