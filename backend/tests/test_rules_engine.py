from datetime import date

import pytest

from app.domain.models import (
    Action,
    ClaimFacts,
    ClaimType,
    Classification,
    DocumentType,
    EvidenceCapture,
    EvidenceRecord,
    EvidenceStatus,
    Finding,
    Route,
    SafetyFact,
    Severity,
)
from app.domain.policy_store import lookup_policy
from app.rules.engine import apply_captures, evaluate, load_config, next_question, select_route

TODAY = date(2026, 9, 24)
HOME = Classification(claim_type=ClaimType.HOME_WATER_DAMAGE, severity=Severity.MEDIUM)
ALL_HOME_DOCS = [DocumentType.DAMAGE_PHOTO, DocumentType.MITIGATION_INVOICE, DocumentType.REPAIR_ESTIMATE]


def home_facts(**overrides) -> ClaimFacts:
    base = dict(
        policyholder_name="Elena Brooks",
        policy_number="HO-20417",
        contact="elena@example.com",
        date_of_loss="2026-09-20",
        loss_location="Denver, CO",
        loss_description="Sump pump failed and flooded the finished basement.",
        estimated_loss_usd=8000,
        safety_facts=[SafetyFact(category="injury", status="absent", description="Nobody was hurt.")],
    )
    return ClaimFacts(**(base | overrides))


def captures_for(*doc_types) -> list[EvidenceCapture]:
    return [EvidenceCapture(capture_id=f"cap-{t}", document_types=[t], confirmed=True) for t in doc_types]


def run(facts, classification=HOME, captures=None):
    return evaluate(facts, classification, lookup_policy(facts.policy_number), captures, today=TODAY)


def rule_ids(decision) -> set[str]:
    return {f.rule_id for f in decision.findings}


# --- Happy path --------------------------------------------------------------


def test_complete_claim_with_received_evidence_is_ready():
    decision = run(home_facts(), captures=captures_for(*ALL_HOME_DOCS))
    assert decision.route == Route.READY_FOR_ADJUSTER
    assert decision.findings == []
    assert all(item.satisfied for item in decision.checklist)
    assert "Route: ready_for_adjuster." in decision.audit_trail


def test_config_covers_every_claim_type():
    config = load_config()
    for claim_type in ClaimType:
        assert config.documents[claim_type], claim_type
        assert config.coverage_notes[claim_type], claim_type


# --- Evidence trust ladder ---------------------------------------------------


def test_llm_cannot_mint_received_evidence():
    claimed = [EvidenceRecord(document_type=t, status=EvidenceStatus.RECEIVED, capture_ids=["fake"]) for t in ALL_HOME_DOCS]
    decision = run(home_facts(evidence=claimed))
    assert decision.route == Route.NEEDS_DOCS
    assert {item.status for item in decision.checklist} == {EvidenceStatus.AVAILABLE}
    assert all(item.capture_ids == [] for item in decision.checklist)


def test_apply_captures_promotes_only_captured_types():
    facts = apply_captures(home_facts(), captures_for(DocumentType.DAMAGE_PHOTO))
    assert [(r.document_type, r.status) for r in facts.evidence] == [(DocumentType.DAMAGE_PHOTO, EvidenceStatus.RECEIVED)]


def test_latest_evidence_statement_wins():
    evidence = [
        EvidenceRecord(document_type=DocumentType.DAMAGE_PHOTO, status=EvidenceStatus.MISSING),
        EvidenceRecord(document_type=DocumentType.DAMAGE_PHOTO, status=EvidenceStatus.AVAILABLE),
    ]
    photo = run(home_facts(evidence=evidence)).checklist[0]
    assert photo.document_type == DocumentType.DAMAGE_PHOTO
    assert photo.status == EvidenceStatus.AVAILABLE


def test_received_capture_beats_later_claim_of_missing():
    evidence = [EvidenceRecord(document_type=DocumentType.DAMAGE_PHOTO, status=EvidenceStatus.MISSING)]
    photo = run(home_facts(evidence=evidence), captures=captures_for(DocumentType.DAMAGE_PHOTO)).checklist[0]
    assert photo.status == EvidenceStatus.RECEIVED
    assert photo.capture_ids == ["cap-damage_photo"]


# --- Safety ------------------------------------------------------------------


def test_negated_injury_does_not_escalate():
    decision = run(home_facts(), captures=captures_for(*ALL_HOME_DOCS))
    assert "SAFE-001" not in rule_ids(decision)


@pytest.mark.parametrize("status", ["present", "uncertain"])
def test_present_or_uncertain_hazard_escalates(status):
    facts = home_facts(safety_facts=[SafetyFact(category="electrical", status=status, description="Water near the panel.")])
    decision = run(facts, captures=captures_for(*ALL_HOME_DOCS))
    assert decision.route == Route.EMERGENCY_ESCALATION
    assert next_question(decision).startswith("If anyone is in danger")


def test_emergency_outranks_siu_and_policy_review():
    facts = home_facts(
        policyholder_name="Wrong Name",
        estimated_loss_usd=50000,
        safety_facts=[SafetyFact(category="injury", status="present", description="Slipped and hurt her back.")],
    )
    decision = run(facts)
    assert {"SAFE-001", "POLICY-001", "EVID-001", "LOSS-001"} <= rule_ids(decision)
    assert decision.route == Route.EMERGENCY_ESCALATION


# --- Intake completeness -----------------------------------------------------


def test_missing_fields_route_to_needs_docs_and_ask_in_order():
    decision = run(home_facts(policyholder_name="not specified", contact="unknown"), captures=captures_for(*ALL_HOME_DOCS))
    assert decision.validation.missing_fields == ["policyholder_name", "contact"]
    assert decision.route == Route.NEEDS_DOCS
    assert next_question(decision) == "What is your full name as it appears on the policy?"


def test_missing_policy_number_does_not_double_flag_policy_review():
    decision = run(home_facts(policy_number="not specified"), captures=captures_for(*ALL_HOME_DOCS))
    assert "INTAKE-001" in rule_ids(decision)
    assert "POLICY-001" not in rule_ids(decision)
    assert decision.route == Route.NEEDS_DOCS


@pytest.mark.parametrize(("value", "problem"), [("2026-13-40", "not a valid calendar date"), ("2026-12-25", "in the future")])
def test_invalid_dates_are_flagged(value, problem):
    decision = run(home_facts(date_of_loss=value), captures=captures_for(*ALL_HOME_DOCS))
    assert f"date_of_loss: {problem}" in decision.validation.invalid_fields
    assert "INTAKE-002" in rule_ids(decision)
    assert next_question(decision) == "Could you confirm the exact date this happened?"


# --- Policy ------------------------------------------------------------------


def test_lapsed_policy_routes_to_policy_review():
    facts = home_facts(policyholder_name="Tom Fischer", policy_number="AU-10001", date_of_loss="2026-09-01")
    auto = Classification(claim_type=ClaimType.AUTO_COLLISION)
    decision = evaluate(
        facts, auto, lookup_policy("AU-10001"), captures_for(DocumentType.DAMAGE_PHOTO, DocumentType.POLICE_REPORT,
                                                              DocumentType.REPAIR_ESTIMATE, DocumentType.WITNESS_DETAILS),
        today=TODAY,
    )
    assert decision.route == Route.POLICY_REVIEW
    messages = [f.message for f in decision.findings if f.rule_id == "POLICY-001"]
    assert "Policy status is lapsed." in messages
    assert "Loss date falls outside the policy period." in messages


def test_unknown_policy_routes_to_policy_review():
    decision = run(home_facts(policy_number="ZZ-12345"), captures=captures_for(*ALL_HOME_DOCS))
    assert decision.route == Route.POLICY_REVIEW
    assert next_question(decision) == "Could you confirm your policy number and the name on the policy?"


# --- Loss size, evidence, and timing (SIU) -----------------------------------


def test_high_loss_flags_adjuster_but_does_not_change_route():
    decision = run(home_facts(estimated_loss_usd=30000), captures=captures_for(*ALL_HOME_DOCS))
    assert "LOSS-001" in rule_ids(decision)
    assert decision.route == Route.READY_FOR_ADJUSTER


def test_large_loss_without_any_evidence_goes_to_siu():
    decision = run(home_facts(estimated_loss_usd=12000))
    assert "EVID-001" in rule_ids(decision)
    assert decision.route == Route.SPECIAL_INVESTIGATION


def test_large_loss_with_evidence_available_is_not_siu():
    evidence = [EvidenceRecord(document_type=DocumentType.DAMAGE_PHOTO, status=EvidenceStatus.AVAILABLE)]
    decision = run(home_facts(estimated_loss_usd=12000, evidence=evidence))
    assert "EVID-001" not in rule_ids(decision)
    assert decision.route == Route.NEEDS_DOCS


def test_late_report_goes_to_siu():
    decision = run(home_facts(date_of_loss="2026-05-01"), captures=captures_for(*ALL_HOME_DOCS))
    assert "TIMING-002" in rule_ids(decision)
    assert decision.route == Route.SPECIAL_INVESTIGATION


def test_report_before_loss_goes_to_siu():
    decision = run(home_facts(reported_date="2026-09-10"), captures=captures_for(*ALL_HOME_DOCS))
    assert "TIMING-001" in rule_ids(decision)
    assert decision.route == Route.SPECIAL_INVESTIGATION


# --- Routing and next question ----------------------------------------------


def test_route_precedence():
    def finding(action):
        return Finding(rule_id="X", severity=Severity.LOW, action=action, message="")

    assert select_route([]) == Route.READY_FOR_ADJUSTER
    assert select_route([finding(Action.ADJUSTER_REVIEW)]) == Route.READY_FOR_ADJUSTER
    assert select_route([finding(Action.COLLECT_DOCUMENT), finding(Action.POLICY_REVIEW)]) == Route.POLICY_REVIEW
    assert select_route([finding(Action.POLICY_REVIEW), finding(Action.SIU_REVIEW)]) == Route.SPECIAL_INVESTIGATION
    assert select_route([finding(Action.SIU_REVIEW), finding(Action.EMERGENCY_ESCALATION)]) == Route.EMERGENCY_ESCALATION


def test_next_question_asks_for_first_pending_document():
    decision = run(home_facts(), captures=captures_for(DocumentType.DAMAGE_PHOTO))
    assert next_question(decision) == "Do you have the water mitigation or drying invoice? You can show it on camera."


def test_next_question_when_everything_is_collected():
    decision = run(home_facts(), captures=captures_for(*ALL_HOME_DOCS))
    assert next_question(decision).startswith("I have what I need")


def test_agent_escalation_goes_through_rules_and_audit():
    decision = evaluate(
        home_facts(), HOME, lookup_policy("HO-20417"), captures_for(*ALL_HOME_DOCS),
        escalations=["claimant says the ceiling is sagging"], today=TODAY,
    )
    assert decision.route == Route.EMERGENCY_ESCALATION
    assert "AGENT-001" in rule_ids(decision)
    assert any("AGENT-001" in line for line in decision.audit_trail)
