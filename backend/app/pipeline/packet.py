"""Adjuster handoff packet: a deterministic rendering of facts and the rules decision."""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.models import ClaimFacts, Classification, Decision, PolicyLookup, Route, is_blank

DISCLAIMER = (
    "This is an intake triage packet. It does not confirm coverage, benefits, liability, or payment. "
    "A licensed adjuster must review the policy, endorsements, exclusions, and documentation."
)


class ClaimPacket(BaseModel):
    route: Route
    handoff_summary: str
    next_question: str
    markdown: str


def _or(value: object, fallback: str) -> str:
    return fallback if is_blank(value) else str(value)


def handoff_summary(facts: ClaimFacts, classification: Classification) -> str:
    amount = f"${facts.estimated_loss_usd:,.0f}" if facts.estimated_loss_usd is not None else "not stated"
    return (
        f"{_or(facts.policyholder_name, 'Unknown claimant')} reported a "
        f"{classification.claim_type.replace('_', ' ')} loss at {_or(facts.loss_location, 'an unknown location')} "
        f"on {_or(facts.date_of_loss, 'an unknown date')}. {_or(facts.loss_description, 'No description yet.')} "
        f"Estimated loss: {amount}."
    )


def build_packet(
    facts: ClaimFacts,
    classification: Classification,
    policy: PolicyLookup,
    decision: Decision,
    next_question: str,
) -> ClaimPacket:
    summary = handoff_summary(facts, classification)
    policy_line = (
        f"{policy.record.policy_number}: {policy.record.policy_line} ({policy.record.status})"
        if policy.found and policy.record
        else f"Not verified. {policy.message}"
    )
    safety = [f"- {s.category}: {s.status} ({s.description})" for s in facts.safety_facts] or ["- None mentioned"]
    missing = [f"- {m}" for m in decision.validation.missing_fields + decision.validation.invalid_fields] or ["- None"]
    checklist = [f"- [{'x' if i.satisfied else ' '}] {i.label} ({i.status})" for i in decision.checklist]
    findings = [f"- `{f.rule_id}` [{f.severity}] {f.message}" for f in decision.findings] or ["- No rules fired"]
    notes = [f"- {n}" for n in decision.coverage_notes]
    audit = [f"{n}. {entry}" for n, entry in enumerate(decision.audit_trail, start=1)]

    markdown = "\n".join(
        [
            "# Claim Intake Packet",
            "",
            f"**Route:** {decision.route.replace('_', ' ').title()}  ",
            f"**Claim type:** {classification.claim_type.replace('_', ' ').title()}  ",
            f"**Severity:** {classification.severity.title()} ({classification.rationale})  ",
            f"**Policy:** {policy_line}",
            "",
            "## Handoff summary",
            summary,
            "",
            "## Safety",
            *safety,
            "",
            "## Missing or invalid information",
            *missing,
            "",
            "## Documents",
            *checklist,
            "",
            "## Rule findings",
            *findings,
            "",
            "## Coverage considerations",
            *notes,
            "",
            DISCLAIMER,
            "",
            "## Next question for the claimant",
            next_question,
            "",
            "## Audit trail",
            *audit,
            "",
        ]
    )
    return ClaimPacket(route=decision.route, handoff_summary=summary, next_question=next_question, markdown=markdown)
