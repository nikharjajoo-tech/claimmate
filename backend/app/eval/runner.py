"""Runs scenarios through the real pipeline and scores each one."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field

from app.domain.models import ClaimType, DocumentType, EvidenceStatus, Route
from app.eval.matchers import Outcome, predicted_escalation, predicted_evidence_status, score_facts
from app.eval.scenario import Scenario
from app.llm.client import LLMCall, LLMResult, StructuredLLM
from app.pipeline.graph import PipelineResult, build_graph, run_pipeline

T = TypeVar("T", bound=BaseModel)


class PacedLLM:
    """Spaces out LLM call starts to stay under free-tier requests-per-minute limits."""

    def __init__(self, inner: StructuredLLM, min_interval_s: float) -> None:
        self.inner = inner
        self.min_interval_s = min_interval_s
        self._lock = asyncio.Lock()
        self._last_start = 0.0

    async def generate(self, *, step: str, system: str, prompt: str, schema: type[T]) -> LLMResult[T]:
        async with self._lock:
            wait = self._last_start + self.min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()
        return await self.inner.generate(step=step, system=system, prompt=prompt, schema=schema)


class FieldResult(BaseModel):
    outcome: Outcome
    expected: str
    predicted: str


class ScenarioResult(BaseModel):
    id: str
    title: str
    category: str
    tags: list[str]
    status: str  # "ok" | "error"
    error: str = ""
    expected_route: Route
    predicted_route: Route | None = None
    expected_type: ClaimType
    predicted_type: ClaimType | None = None
    expected_escalation: bool
    predicted_escalation: bool | None = None  # from extracted safety facts
    fields: dict[str, FieldResult] = Field(default_factory=dict)
    evidence: dict[DocumentType, tuple[EvidenceStatus, EvidenceStatus]] = Field(default_factory=dict)
    rules_fired: list[str] = Field(default_factory=list)
    rule_problems: list[str] = Field(default_factory=list)
    llm_calls: list[LLMCall] = Field(default_factory=list)
    node_ms: dict[str, int] = Field(default_factory=dict)
    total_ms: int = 0

    @property
    def route_ok(self) -> bool:
        return self.predicted_route == self.expected_route

    @property
    def type_ok(self) -> bool:
        return self.predicted_type == self.expected_type

    @property
    def fields_ok(self) -> bool:
        return all(f.outcome in (Outcome.CORRECT_VALUE, Outcome.CORRECT_BLANK) for f in self.fields.values())

    @property
    def evidence_ok(self) -> bool:
        return all(exp == pred for exp, pred in self.evidence.values())

    @property
    def passed(self) -> bool:
        return (
            self.status == "ok"
            and self.route_ok
            and self.type_ok
            and self.fields_ok
            and self.evidence_ok
            and not self.rule_problems
        )


def score_scenario(scenario: Scenario, result: PipelineResult) -> ScenarioResult:
    expected = scenario.expected
    fired = sorted({f.rule_id for f in result.decision.findings})
    problems = [f"expected {r} to fire" for r in expected.rules_fired if r not in fired]
    problems += [f"expected {r} not to fire" for r in expected.rules_not_fired if r in fired]
    outcomes = score_facts(expected.facts, result.facts)
    return ScenarioResult(
        id=scenario.id,
        title=scenario.title,
        category=scenario.category,
        tags=scenario.tags,
        status="ok",
        expected_route=expected.route,
        predicted_route=result.decision.route,
        expected_type=expected.claim_type,
        predicted_type=result.classification.claim_type,
        expected_escalation=expected.escalates,
        predicted_escalation=predicted_escalation(result.facts),
        fields={
            name: FieldResult(
                outcome=outcome,
                expected=str(getattr(expected.facts, name)),
                predicted=str(getattr(result.facts, name)),
            )
            for name, outcome in outcomes.items()
        },
        evidence={
            doc: (status, predicted_evidence_status(result.facts, doc)) for doc, status in expected.evidence.items()
        },
        rules_fired=fired,
        rule_problems=problems,
        llm_calls=result.llm_calls,
        node_ms=result.node_ms,
        total_ms=result.total_ms,
    )


def error_result(scenario: Scenario, exc: Exception) -> ScenarioResult:
    return ScenarioResult(
        id=scenario.id,
        title=scenario.title,
        category=scenario.category,
        tags=scenario.tags,
        status="error",
        error=f"{type(exc).__name__}: {exc}"[:500],
        expected_route=scenario.expected.route,
        expected_type=scenario.expected.claim_type,
        expected_escalation=scenario.expected.escalates,
    )


async def run_eval(
    scenarios: list[Scenario],
    llm: StructuredLLM,
    *,
    concurrency: int = 2,
    on_result: Callable[[ScenarioResult], None] | None = None,
) -> list[ScenarioResult]:
    graph = build_graph(llm)
    semaphore = asyncio.Semaphore(concurrency)

    async def one(scenario: Scenario) -> ScenarioResult:
        async with semaphore:
            try:
                pipeline = await run_pipeline(
                    llm,
                    scenario.transcript(),
                    observations=scenario.observations(),
                    captures=scenario.captures,
                    today=scenario.today,
                    graph=graph,
                )
                result = score_scenario(scenario, pipeline)
            except Exception as exc:  # one bad scenario must not sink the run
                result = error_result(scenario, exc)
            if on_result:
                on_result(result)
            return result

    results = await asyncio.gather(*(one(s) for s in scenarios))
    order = {s.id: i for i, s in enumerate(scenarios)}
    return sorted(results, key=lambda r: order[r.id])


def load_results(path: Path) -> list[ScenarioResult]:
    if not path.exists():
        return []
    return [ScenarioResult.model_validate(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
