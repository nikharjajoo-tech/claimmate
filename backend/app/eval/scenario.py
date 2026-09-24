"""Labeled eval scenarios: a conversation plus the gold-standard answer.

Gold facts are complete enough to run through the rules engine on their own, so every label
can be checked for internal consistency without calling a model (see tests/test_eval_labels.py).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from app.domain.models import (
    NOT_SPECIFIED,
    ClaimFacts,
    ClaimType,
    DocumentType,
    EvidenceCapture,
    EvidenceRecord,
    EvidenceStatus,
    Route,
    SafetyFact,
)
from app.pipeline.prompts import Turn

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "eval" / "scenarios"
DEFAULT_TODAY = date(2026, 9, 24)


class GoldFacts(BaseModel):
    """Expected extraction. 'not specified' means the model must leave the field blank."""

    policyholder_name: str = NOT_SPECIFIED
    policy_number: str = NOT_SPECIFIED
    contact: str = Field(default=NOT_SPECIFIED, description="Phone or email that must appear in the output.")
    date_of_loss: str = NOT_SPECIFIED
    reported_date: str = NOT_SPECIFIED
    loss_location: str = Field(default=NOT_SPECIFIED, description="Keyword that must appear in the output.")
    loss_description: str = Field(default=NOT_SPECIFIED, description="Only blank vs non-blank is scored.")
    estimated_loss_usd: float | None = None


class GoldSafety(BaseModel):
    category: Literal["injury", "unsafe_housing", "electrical", "sewage", "mold", "fire", "other"]
    status: Literal["present", "absent", "uncertain"]


class Expected(BaseModel):
    claim_type: ClaimType
    route: Route
    facts: GoldFacts
    safety: list[GoldSafety] = Field(default_factory=list)
    evidence: dict[DocumentType, EvidenceStatus] = Field(default_factory=dict)
    rules_fired: list[str] = Field(default_factory=list)
    rules_not_fired: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_received_in_gold(self) -> Expected:
        if EvidenceStatus.RECEIVED in self.evidence.values():
            raise ValueError("Gold evidence is what the claimant says; 'received' only comes from captures.")
        return self

    @property
    def escalates(self) -> bool:
        return any(s.status in ("present", "uncertain") for s in self.safety)


class Scenario(BaseModel):
    id: str
    title: str
    category: str
    tags: list[str] = Field(default_factory=list)
    today: date = DEFAULT_TODAY
    turns: list[dict[Literal["claimant", "agent"], str]]
    captures: list[EvidenceCapture] = Field(default_factory=list)
    expected: Expected

    def transcript(self) -> list[Turn]:
        result = []
        for i, entry in enumerate(self.turns, start=1):
            ((speaker, text),) = entry.items()
            result.append(Turn(id=f"t{i}", speaker=speaker, text=text))
        return result

    def observations(self) -> list[str]:
        return [f"Capture {c.capture_id}: {c.caption}" for c in self.captures if c.caption]

    def gold_claim_facts(self) -> ClaimFacts:
        """The gold labels as a ClaimFacts, for label consistency checks and the oracle test."""
        return ClaimFacts(
            **self.expected.facts.model_dump(),
            safety_facts=[SafetyFact(category=s.category, status=s.status, description="gold") for s in self.expected.safety],
            evidence=[EvidenceRecord(document_type=t, status=s) for t, s in self.expected.evidence.items()],
        )


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        for raw in data["scenarios"]:
            raw.setdefault("category", data["category"])
            scenarios.append(Scenario.model_validate(raw))
    ids = [s.id for s in scenarios]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate scenario ids: {sorted(duplicates)}")
    return scenarios
