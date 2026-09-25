"""LangGraph claim pipeline, in one of two modes.

split (two LLM calls):
    START -> extract_facts --+--> classify (LLM) ----+--> evaluate_rules -> build_packet -> END
                             +--> lookup_policy -----+
single (one LLM call returning facts and classification together):
    START -> analyze (LLM) -> lookup_policy -> evaluate_rules -> build_packet -> END

LLM steps perceive; everything that decides is deterministic code.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.domain.models import ClaimFacts, Classification, Decision, EvidenceCapture, PolicyLookup
from app.domain.policy_store import lookup_policy
from app.llm.client import LLMCall, StructuredLLM
from app.pipeline import prompts
from app.pipeline.packet import ClaimPacket, build_packet
from app.pipeline.prompts import Turn
from app.rules.engine import evaluate, next_question


class ClaimState(TypedDict, total=False):
    # Inputs
    turns: list[Turn]
    observations: list[str]
    captures: list[EvidenceCapture]
    escalations: list[str]
    today: date
    # Node outputs
    facts: ClaimFacts
    classification: Classification
    policy: PolicyLookup
    decision: Decision
    packet: ClaimPacket
    # Accumulated across nodes (parallel branches append safely)
    llm_calls: Annotated[list[LLMCall], operator.add]
    node_ms: Annotated[list[tuple[str, int]], operator.add]


class PipelineResult(BaseModel):
    facts: ClaimFacts
    classification: Classification
    policy: PolicyLookup
    decision: Decision
    packet: ClaimPacket
    llm_calls: list[LLMCall] = Field(default_factory=list)
    node_ms: dict[str, int] = Field(default_factory=dict)
    total_ms: int = 0

    @property
    def tokens(self) -> tuple[int, int]:
        return sum(c.input_tokens for c in self.llm_calls), sum(c.output_tokens for c in self.llm_calls)


Node = Callable[[ClaimState], Awaitable[dict[str, Any]]]
PipelineMode = Literal["split", "single"]


def _timed(name: str, fn: Node) -> Node:
    async def wrapper(state: ClaimState) -> dict[str, Any]:
        started = time.monotonic()
        update = await fn(state)
        return {**update, "node_ms": [(name, int((time.monotonic() - started) * 1000))]}

    return wrapper


def _has_claimant_speech(state: ClaimState) -> bool:
    return any(t.speaker == "claimant" and t.text.strip() for t in state["turns"])


def build_graph(llm: StructuredLLM, mode: PipelineMode = "split", prompt_version: str = "v1"):
    if prompt_version not in prompts.PROMPT_VERSIONS:
        raise ValueError(f"Unknown prompt version {prompt_version!r}")

    async def analyze(state: ClaimState) -> dict[str, Any]:
        if not _has_claimant_speech(state):
            return {"facts": ClaimFacts(), "classification": Classification(), "llm_calls": []}
        result = await llm.generate(
            step="analyze",
            system=prompts.analyze_system(prompt_version),
            prompt=prompts.extract_prompt(state["turns"], state.get("observations", []), state["today"]),
            schema=prompts.analysis_schema(prompt_version),
        )
        return {
            "facts": prompts.to_claim_facts(result.value.facts),
            "classification": result.value.classification,
            "llm_calls": [result.call],
        }

    async def extract_facts(state: ClaimState) -> dict[str, Any]:
        if not _has_claimant_speech(state):
            return {"facts": ClaimFacts(), "llm_calls": []}
        result = await llm.generate(
            step="extract_facts",
            system=prompts.extract_system(prompt_version),
            prompt=prompts.extract_prompt(state["turns"], state.get("observations", []), state["today"]),
            schema=prompts.extraction_schema(prompt_version),
        )
        return {"facts": prompts.to_claim_facts(result.value), "llm_calls": [result.call]}

    async def classify(state: ClaimState) -> dict[str, Any]:
        if not _has_claimant_speech(state):
            return {"classification": Classification(), "llm_calls": []}
        result = await llm.generate(
            step="classify",
            system=prompts.CLASSIFY_SYSTEM,
            prompt=prompts.classify_prompt(state["facts"]),
            schema=Classification,
        )
        return {"classification": result.value, "llm_calls": [result.call]}

    async def lookup(state: ClaimState) -> dict[str, Any]:
        return {"policy": lookup_policy(state["facts"].policy_number)}

    async def evaluate_rules(state: ClaimState) -> dict[str, Any]:
        decision = evaluate(
            state["facts"],
            state["classification"],
            state["policy"],
            state.get("captures", []),
            escalations=state.get("escalations", []),
            today=state["today"],
        )
        return {"decision": decision}

    async def packet(state: ClaimState) -> dict[str, Any]:
        decision = state["decision"]
        return {
            "packet": build_packet(
                state["facts"],
                state["classification"],
                state["policy"],
                decision,
                next_question(decision),
                state.get("captures", []),
            )
        }

    graph = StateGraph(ClaimState)
    perceive = [("analyze", analyze)] if mode == "single" else [("extract_facts", extract_facts), ("classify", classify)]
    for name, fn in [
        *perceive,
        ("lookup_policy", lookup),
        ("evaluate_rules", evaluate_rules),
        ("build_packet", packet),
    ]:
        graph.add_node(name, _timed(name, fn))

    if mode == "single":
        graph.add_edge(START, "analyze")
        graph.add_edge("analyze", "lookup_policy")
        graph.add_edge("lookup_policy", "evaluate_rules")
    else:
        graph.add_edge(START, "extract_facts")
        graph.add_edge("extract_facts", "classify")
        graph.add_edge("extract_facts", "lookup_policy")
        graph.add_edge(["classify", "lookup_policy"], "evaluate_rules")  # join: waits for both branches
    graph.add_edge("evaluate_rules", "build_packet")
    graph.add_edge("build_packet", END)
    return graph.compile()


async def run_pipeline(
    llm: StructuredLLM,
    turns: list[Turn],
    *,
    observations: list[str] | None = None,
    captures: list[EvidenceCapture] | None = None,
    escalations: list[str] | None = None,
    today: date | None = None,
    graph: Any = None,
) -> PipelineResult:
    started = time.monotonic()
    graph = graph or build_graph(llm)
    state = await graph.ainvoke(
        {
            "turns": turns,
            "observations": observations or [],
            "captures": captures or [],
            "escalations": escalations or [],
            "today": today or date.today(),
            "llm_calls": [],
            "node_ms": [],
        }
    )
    return PipelineResult(
        facts=state["facts"],
        classification=state["classification"],
        policy=state["policy"],
        decision=state["decision"],
        packet=state["packet"],
        llm_calls=state["llm_calls"],
        node_ms=dict(state["node_ms"]),
        total_ms=int((time.monotonic() - started) * 1000),
    )
