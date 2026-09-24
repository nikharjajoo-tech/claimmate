"""Per-field comparison of predicted facts against gold labels.

Every field lands in one of five outcomes:
    correct_value  gold has a value, prediction matches            (true positive)
    correct_blank  gold is blank, prediction is blank               (true negative)
    hallucinated   gold is blank, prediction invented a value       (false positive)
    missed         gold has a value, prediction is blank            (false negative)
    wrong_value    both have values but they disagree               (false positive + false negative)
"""

from __future__ import annotations

import re
from enum import StrEnum

from app.domain.models import ClaimFacts, DocumentType, EvidenceStatus, is_blank
from app.domain.policy_store import normalize_policy_number
from app.eval.scenario import GoldFacts


class Outcome(StrEnum):
    CORRECT_VALUE = "correct_value"
    CORRECT_BLANK = "correct_blank"
    HALLUCINATED = "hallucinated"
    MISSED = "missed"
    WRONG_VALUE = "wrong_value"


SCORED_FIELDS = [
    "policyholder_name",
    "policy_number",
    "contact",
    "date_of_loss",
    "reported_date",
    "loss_location",
    "loss_description",
    "estimated_loss_usd",
]


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def values_match(field: str, expected: object, predicted: object) -> bool:
    """Compare two non-blank values with a field-appropriate notion of equality."""
    exp, pred = str(expected), str(predicted)
    if field == "policyholder_name":
        return _letters(exp) == _letters(pred)
    if field == "policy_number":
        return normalize_policy_number(exp) == normalize_policy_number(pred)
    if field == "contact":
        if "@" in exp:
            return exp.lower() in pred.lower()
        return bool(_digits(exp)) and _digits(exp) in _digits(pred)
    if field == "loss_location":
        return exp.lower() in pred.lower()
    if field == "loss_description":
        return True  # free text: only blank vs non-blank is scored
    if field == "estimated_loss_usd":
        return abs(float(expected) - float(predicted)) <= max(1.0, 0.01 * float(expected))
    return exp.strip() == pred.strip()  # ISO dates


def _blank(value: object) -> bool:
    return value is None or is_blank(value)


def score_field(field: str, expected: object, predicted: object) -> Outcome:
    exp_blank, pred_blank = _blank(expected), _blank(predicted)
    if exp_blank and pred_blank:
        return Outcome.CORRECT_BLANK
    if exp_blank:
        return Outcome.HALLUCINATED
    if pred_blank:
        return Outcome.MISSED
    return Outcome.CORRECT_VALUE if values_match(field, expected, predicted) else Outcome.WRONG_VALUE


def score_facts(gold: GoldFacts, predicted: ClaimFacts) -> dict[str, Outcome]:
    return {f: score_field(f, getattr(gold, f), getattr(predicted, f)) for f in SCORED_FIELDS}


def predicted_evidence_status(facts: ClaimFacts, doc_type: DocumentType) -> EvidenceStatus:
    """Latest status the extractor reported for a document type (before captures are applied)."""
    statuses = [r.status for r in facts.evidence if r.document_type == doc_type]
    return statuses[-1] if statuses else EvidenceStatus.UNKNOWN


def predicted_escalation(facts: ClaimFacts) -> bool:
    return any(s.status in ("present", "uncertain") for s in facts.safety_facts)
