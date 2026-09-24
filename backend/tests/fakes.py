"""Shared fakes: a scripted structured-output LLM and a pipeline runner built on it."""

from app.domain.models import (
    ClaimFacts,
    ClaimType,
    Classification,
    DocumentType,
    EvidenceRecord,
    EvidenceStatus,
    SafetyFact,
    Severity,
)
from app.llm.client import LLMCall, LLMResult
from app.pipeline.graph import build_graph, run_pipeline

FLOOD_FACTS = ClaimFacts(
    policyholder_name="Elena Brooks",
    policy_number="HO-20417",
    contact="720-555-0148",
    date_of_loss="2026-09-23",
    loss_location="Denver, CO",
    loss_description="Sump pump failed and flooded the basement.",
    estimated_loss_usd=9000,
    safety_facts=[SafetyFact(category="injury", status="absent", description="Nobody was hurt.")],
    evidence=[EvidenceRecord(document_type=DocumentType.DAMAGE_PHOTO, status=EvidenceStatus.AVAILABLE)],
)
INJURY_FACTS = FLOOD_FACTS.model_copy(
    update={"safety_facts": [SafetyFact(category="injury", status="present", description="Slipped, hurt back")]}
)
HOME = Classification(claim_type=ClaimType.HOME_WATER_DAMAGE, severity=Severity.MEDIUM, rationale="Water loss.")


class FakeLLM:
    def __init__(self, facts=FLOOD_FACTS, classification=HOME):
        self.facts, self.classification = facts, classification
        self.calls = 0

    async def generate(self, *, step, system, prompt, schema):
        self.calls += 1
        value = self.facts if schema is ClaimFacts else self.classification
        return LLMResult[schema](value=value, call=LLMCall(step=step, model="fake", attempts=1, latency_ms=1))


def fake_runner(llm=None):
    llm = llm or FakeLLM()
    graph = build_graph(llm)

    async def runner(turns, **kwargs):
        return await run_pipeline(llm, turns, graph=graph, **kwargs)

    runner.llm = llm
    return runner
