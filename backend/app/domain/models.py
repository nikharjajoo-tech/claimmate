"""Typed contracts shared by the extraction pipeline, rules engine, API, and eval harness."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

NOT_SPECIFIED = "not specified"
_BLANK = {"", "unknown", NOT_SPECIFIED, "unspecified", "n/a", "none", "not provided"}


def is_blank(value: object) -> bool:
    """True for values the extractor uses to mean 'not collected yet'."""
    return str(value or "").strip().lower() in _BLANK


class ClaimType(StrEnum):
    HOME_WATER_DAMAGE = "home_water_damage"
    AUTO_COLLISION = "auto_collision"
    THEFT_PROPERTY_LOSS = "theft_property_loss"
    TRAVEL_DISRUPTION = "travel_disruption"
    MEDICAL_REIMBURSEMENT = "medical_reimbursement"
    OTHER = "other"


class Product(StrEnum):
    """What the customer bought. Selects the policy wording a claim is reviewed against (D14c)."""

    HOMEOWNERS = "homeowners"
    RENTERS = "renters"
    AUTO = "auto"
    TRAVEL = "travel"
    MEDICAL = "medical"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Route(StrEnum):
    """Routing outcomes, listed from highest to lowest precedence."""

    EMERGENCY_ESCALATION = "emergency_escalation"
    SPECIAL_INVESTIGATION = "special_investigation"
    POLICY_REVIEW = "policy_review"
    NEEDS_DOCS = "needs_docs"
    READY_FOR_ADJUSTER = "ready_for_adjuster"


class Action(StrEnum):
    COLLECT_INFO = "collect_info"
    COLLECT_DOCUMENT = "collect_document"
    POLICY_REVIEW = "policy_review"
    ADJUSTER_REVIEW = "adjuster_review"
    SIU_REVIEW = "siu_review"
    EMERGENCY_ESCALATION = "emergency_escalation"


class EvidenceStatus(StrEnum):
    """Trust ladder. Only a server-side capture may set RECEIVED."""

    UNKNOWN = "unknown"
    MISSING = "missing"
    PLANNED = "planned"
    AVAILABLE = "available"
    RECEIVED = "received"


class DocumentType(StrEnum):
    DAMAGE_PHOTO = "damage_photo"
    MITIGATION_INVOICE = "mitigation_invoice"
    REPAIR_ESTIMATE = "repair_estimate"
    OWNERSHIP_PROOF = "ownership_proof"
    POLICE_REPORT = "police_report"
    MEDICAL_BILL = "medical_bill"
    EXPLANATION_OF_BENEFITS = "explanation_of_benefits"
    PAYMENT_PROOF = "payment_proof"
    WITNESS_DETAILS = "witness_details"
    CARRIER_NOTICE = "carrier_notice"
    ITINERARY = "itinerary"
    EXPENSE_RECEIPT = "expense_receipt"
    REFUND_DOCUMENT = "refund_document"
    OTHER_PROOF = "other_proof"


# --- Extraction output -------------------------------------------------------


class EvidenceRecord(BaseModel):
    document_type: DocumentType
    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    source_turn_ids: list[str] = Field(default_factory=list)
    capture_ids: list[str] = Field(default_factory=list)


class SafetyFact(BaseModel):
    category: Literal["injury", "unsafe_housing", "electrical", "sewage", "mold", "fire", "other"]
    status: Literal["present", "absent", "uncertain"]
    description: str
    source_turn_ids: list[str] = Field(default_factory=list)


class FactSource(BaseModel):
    field: str
    source_turn_ids: list[str] = Field(default_factory=list)


class ClaimFacts(BaseModel):
    """Structured facts extracted from the conversation. Unknown text fields stay 'not specified'."""

    policyholder_name: str = NOT_SPECIFIED
    policy_number: str = NOT_SPECIFIED
    contact: str = NOT_SPECIFIED
    date_of_loss: str = Field(default=NOT_SPECIFIED, description="YYYY-MM-DD or 'not specified'.")
    reported_date: str = Field(default=NOT_SPECIFIED, description="YYYY-MM-DD or 'not specified'.")
    loss_location: str = NOT_SPECIFIED
    loss_description: str = NOT_SPECIFIED
    estimated_loss_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    parties_involved: list[str] = Field(default_factory=list)
    safety_facts: list[SafetyFact] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    uncertain_facts: list[str] = Field(default_factory=list)
    fact_sources: list[FactSource] = Field(default_factory=list)


class Classification(BaseModel):
    claim_type: ClaimType = ClaimType.OTHER
    severity: Severity = Severity.MEDIUM
    rationale: str = "Waiting for claimant facts."


class EvidenceCapture(BaseModel):
    """A server-verified camera capture. The only source of RECEIVED evidence."""

    capture_id: str
    document_types: list[DocumentType] = Field(default_factory=list)
    caption: str = ""
    confirmed: bool = False
    claimant_claim: str = ""
    source: Literal["agent", "claimant", "upload"] = "agent"
    captured_at: str = ""


# --- Policy ------------------------------------------------------------------

# Every policy number starts with its product's code, e.g. HO-20417 is a homeowners policy.
_PRODUCT_BY_PREFIX = {
    "HO": Product.HOMEOWNERS,
    "RN": Product.RENTERS,
    "AU": Product.AUTO,
    "TR": Product.TRAVEL,
    "MD": Product.MEDICAL,
}


class PolicyRecord(BaseModel):
    policy_number: str
    policyholder_name: str
    policy_line: str  # the human label shown in the UI, e.g. "Homeowners (HO-3)"
    product: Product  # the machine key that selects the policy wording (PRD D14c)
    status: Literal["active", "lapsed", "cancelled"]
    effective_start: date
    effective_end: date
    deductibles: dict[str, int] = Field(default_factory=dict)
    coverages: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _product_for_older_records(cls, data: Any) -> Any:
        """Claims stored before records carried a product (2026-09-29) have only the policy number.
        Without this the whole claim fails to load, and the adjuster cannot open it."""
        if isinstance(data, dict) and "product" not in data:
            product = _PRODUCT_BY_PREFIX.get(str(data.get("policy_number", "")).split("-", 1)[0].upper())
            if product is not None:
                data = {**data, "product": product}
        return data


class PolicyLookup(BaseModel):
    found: bool
    query: str
    record: PolicyRecord | None = None
    message: str = ""


# --- Rules engine output -----------------------------------------------------


class Validation(BaseModel):
    missing_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing_fields and not self.invalid_fields


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    action: Action
    message: str
    document_type: DocumentType | None = None


class ChecklistItem(BaseModel):
    document_type: DocumentType
    label: str
    reason: str
    status: EvidenceStatus
    capture_ids: list[str] = Field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return self.status == EvidenceStatus.RECEIVED


class Decision(BaseModel):
    route: Route
    validation: Validation
    findings: list[Finding] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)
    audit_trail: list[str] = Field(default_factory=list)
