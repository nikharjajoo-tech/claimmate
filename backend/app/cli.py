"""Run the claim pipeline on a transcript file.

    python -m app.cli examples/basement_flood.txt
    python -m app.cli examples/basement_flood.txt --markdown

File format: one turn per line, prefixed "CLAIMANT:" or "AGENT:". A file with no prefixes is
treated as a single claimant turn.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

from app.llm.client import GeminiLLM, LLMError
from app.pipeline.graph import PipelineResult, run_pipeline
from app.pipeline.prompts import Turn


def parse_transcript(text: str) -> list[Turn]:
    turns: list[Turn] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        speaker, sep, rest = line.partition(":")
        if sep and speaker.strip().lower() in {"claimant", "agent"}:
            turns.append(Turn(id=f"t{len(turns) + 1}", speaker=speaker.strip().lower(), text=rest.strip()))
        elif turns:
            turns[-1] = turns[-1].model_copy(update={"text": f"{turns[-1].text} {line}"})
        else:
            turns.append(Turn(id="t1", speaker="claimant", text=line))
    return turns


def print_summary(result: PipelineResult) -> None:
    f, c, d = result.facts, result.classification, result.decision
    print(f"\nROUTE: {d.route.value}    TYPE: {c.claim_type.value}    SEVERITY: {c.severity.value}")
    print(f"  {c.rationale}\n")
    for name in ("policyholder_name", "policy_number", "contact", "date_of_loss", "loss_location", "loss_description"):
        print(f"  {name:<18} {getattr(f, name)}")
    print(f"  {'estimated_loss':<18} {f.estimated_loss_usd}")
    for s in f.safety_facts:
        print(f"  {'safety':<18} {s.category}={s.status}: {s.description}")
    print("\nDocuments:")
    for item in d.checklist:
        print(f"  [{'x' if item.satisfied else ' '}] {item.label} ({item.status.value})")
    print("\nRules fired:")
    for finding in d.findings:
        print(f"  {finding.rule_id:<11} {finding.message}")
    if not d.findings:
        print("  none")
    print(f"\nNext question: {result.packet.next_question}")
    tokens_in, tokens_out = result.tokens
    print(f"\nTiming: total {result.total_ms} ms | " + " | ".join(f"{k} {v}" for k, v in result.node_ms.items()))
    for call in result.llm_calls:
        fb = " (fallback)" if call.fell_back else ""
        print(f"  {call.step}: {call.model}{fb}, {call.attempts} attempt(s), {call.latency_ms} ms")
    print(f"Tokens: {tokens_in} in / {tokens_out} out")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--markdown", action="store_true", help="print the full adjuster packet")
    parser.add_argument("--today", type=date.fromisoformat, help="reference date (YYYY-MM-DD)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    turns = parse_transcript(args.transcript.read_text())
    try:
        result = asyncio.run(run_pipeline(GeminiLLM(), turns, today=args.today))
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.packet.markdown if args.markdown else "", end="")
    print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
