"""Eval for the wording review (F13, FR-10, spec section 6).

    python -m app.eval.review                    # every labelled scenario
    python -m app.eval.review --only hp-01,wr-03
    python -m app.eval.review --provider groq --models openai/gpt-oss-120b
    python -m app.eval.review --resume eval/results/review-20261002-1200
    python -m app.eval.review --check            # exit 1 if a target is missed

Scored against the scenarios' **gold** facts, not against what the pipeline extracted. That keeps
one variable in play: a clause this review misses is the review's fault, not fallout from an
extraction error, and it costs one model call per scenario instead of three (spec section 6's
quota plan).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain.models import Classification, Severity
from app.domain.policy_store import lookup_policy
from app.eval.runner import PacedLLM
from app.eval.scenario import REVIEW_SCENARIO_DIR, Scenario, load_scenarios
from app.llm.client import LLMCall, LLMError, StructuredLLM
from app.llm.factory import make_pipeline_llm
from app.review.checks import has_verdict_language
from app.review.models import PolicyReview
from app.review.service import generate_policy_review

RESULTS_DIR = Path(__file__).resolve().parents[2] / "eval" / "results"

# Clauses an adjuster checks on any claim, whatever the loss: how long they had to report it, the
# duty to limit the damage, the documents owed, and the deductible. Citing one of these is never
# wrong, so precision must not punish it; it is also never enough on its own, so recall still has
# to be earned on the clauses that bear on *this* loss.
#
# This is a uniform rule per product rather than a per-scenario label, so it cannot be tuned to
# flatter a particular run. It was added after run 1 of 2026-10-02, whose precision it changes
# from 73.9% to the figure in that run's report; both are recorded in docs/04-eval-results.md.
ALWAYS_ACCEPTABLE: dict[str, set[str]] = {
    "homeowners": {"4.1", "4.2", "5.1", "5.2", "5.3", "5.4"},
    "renters": {"4.1", "5.1", "5.2", "5.3", "5.4"},
    "auto": {"4.1", "4.2", "5.1", "5.2", "5.3", "5.4"},
    "travel": {"4.1", "5.1", "5.2", "5.3", "5.4"},
    "medical": {"3.1", "3.2", "5.1", "5.2", "5.3", "5.4"},  # medical keeps its deductible in part 3
}


def acceptable_sections(scenario: Scenario, wording_id: str) -> set[str]:
    """Everything a citation may name without counting as noise."""
    labels = scenario.expected.wording_review
    assert labels is not None
    allowed = set(labels.sections) | set(labels.also_relevant) | ALWAYS_ACCEPTABLE.get(wording_id, set())
    return allowed - set(labels.must_not_cite)  # a forbidden clause is never acceptable


class ReviewScenarioResult(BaseModel):
    id: str
    title: str
    category: str
    status: str  # ok | error
    error: str = ""
    wording_ref: str = ""
    expected_sections: list[str] = Field(default_factory=list)
    also_relevant: list[str] = Field(default_factory=list)
    cited_sections: list[str] = Field(default_factory=list)
    missed_sections: list[str] = Field(default_factory=list)
    noise_sections: list[str] = Field(default_factory=list)  # cited, neither required nor acceptable
    forbidden_cited: list[str] = Field(default_factory=list)
    expected_question_turns: list[str] = Field(default_factory=list)
    captured_question_turns: list[str] = Field(default_factory=list)
    summary: str = ""
    summary_had_verdict: bool = False  # a verdict that reached the adjuster: must always be false
    summary_replaced: bool = False  # the guard caught one and replaced it
    dropped: list[str] = Field(default_factory=list)
    # What was dropped, verbatim: a run that cannot say why a clause vanished cannot be diagnosed.
    dropped_detail: list[str] = Field(default_factory=list)
    llm_call: LLMCall | None = None
    total_ms: int = 0

    @property
    def section_recall(self) -> float | None:
        if not self.expected_sections:
            return None
        return 1 - len(self.missed_sections) / len(self.expected_sections)

    @property
    def question_recall(self) -> float | None:
        if not self.expected_question_turns:
            return None
        return len(self.captured_question_turns) / len(self.expected_question_turns)

    @property
    def passed(self) -> bool:
        return (
            self.status == "ok"
            and not self.missed_sections
            and not self.forbidden_cited
            and not self.summary_had_verdict
            and not set(self.expected_question_turns) - set(self.captured_question_turns)
        )


def score_review(scenario: Scenario, review: PolicyReview, elapsed_ms: int) -> ReviewScenarioResult:
    labels = scenario.expected.wording_review
    assert labels is not None
    cited = [c.section for c in review.clauses]
    acceptable = acceptable_sections(scenario, review.wording_ref.split("/")[0])
    captured = sorted({q.turn_id for q in review.questions} & set(labels.question_turns))
    return ReviewScenarioResult(
        id=scenario.id,
        title=scenario.title,
        category=scenario.category,
        status="ok",
        wording_ref=review.wording_ref,
        expected_sections=labels.sections,
        also_relevant=labels.also_relevant,
        cited_sections=cited,
        missed_sections=[s for s in labels.sections if s not in cited],
        noise_sections=[s for s in cited if s not in acceptable],
        forbidden_cited=[s for s in cited if s in labels.must_not_cite],
        expected_question_turns=labels.question_turns,
        captured_question_turns=captured,
        summary=review.summary,
        # The guard runs before an adjuster sees anything, so this is the number that matters.
        summary_had_verdict=has_verdict_language(review.summary),
        summary_replaced=review.summary_replaced,
        dropped=[f"{d.kind}/{d.reason}" for d in review.dropped],
        dropped_detail=[f"{d.kind}/{d.reason}: {d.detail}" for d in review.dropped],
        llm_call=review.llm_call,
        total_ms=elapsed_ms,
    )


def error_result(scenario: Scenario, exc: Exception) -> ReviewScenarioResult:
    labels = scenario.expected.wording_review
    return ReviewScenarioResult(
        id=scenario.id,
        title=scenario.title,
        category=scenario.category,
        status="error",
        error=f"{type(exc).__name__}: {exc}"[:500],
        expected_sections=labels.sections if labels else [],
        expected_question_turns=labels.question_turns if labels else [],
    )


async def run_review_eval(
    scenarios: list[Scenario],
    llm: StructuredLLM,
    *,
    concurrency: int = 2,
    prompt_version: str = "v1",
    on_result=None,
) -> list[ReviewScenarioResult]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(scenario: Scenario) -> ReviewScenarioResult:
        async with semaphore:
            started = datetime.now()
            try:
                facts = scenario.gold_claim_facts()
                review = await generate_policy_review(
                    llm,
                    policy=lookup_policy(facts.policy_number),
                    facts=facts,
                    classification=Classification(
                        claim_type=scenario.expected.claim_type,
                        severity=Severity.MEDIUM,
                        rationale="Gold label.",
                    ),
                    turns=scenario.transcript(),
                    captures=scenario.captures,
                    prompt_version=prompt_version,
                )
                result = score_review(scenario, review, int((datetime.now() - started).total_seconds() * 1000))
            except Exception as exc:  # one bad scenario must not sink the run
                result = error_result(scenario, exc)
            if on_result:
                on_result(result)
            return result

    results = await asyncio.gather(*(one(s) for s in scenarios))
    order = {s.id: i for i, s in enumerate(scenarios)}
    return sorted(results, key=lambda r: order[r.id])


def compute_metrics(results: list[ReviewScenarioResult]) -> dict:
    ok = [r for r in results if r.status == "ok"]
    expected_total = sum(len(r.expected_sections) for r in ok)
    found_total = sum(len(r.expected_sections) - len(r.missed_sections) for r in ok)
    cited_total = sum(len(r.cited_sections) for r in ok)
    noise_total = sum(len(r.noise_sections) for r in ok)
    questions_total = sum(len(r.expected_question_turns) for r in ok)
    questions_found = sum(len(r.captured_question_turns) for r in ok)
    tokens_in = sum(r.llm_call.input_tokens for r in ok if r.llm_call)
    tokens_out = sum(r.llm_call.output_tokens for r in ok if r.llm_call)
    drops: dict[str, int] = {}
    for r in ok:
        for d in r.dropped:
            drops[d] = drops.get(d, 0) + 1
    return {
        "scenarios": len(results),
        "completed": len(ok),
        "errors": sum(1 for r in results if r.status == "error"),
        "passed": sum(1 for r in results if r.passed),
        "clause_recall": found_total / expected_total if expected_total else None,
        "clause_precision": (cited_total - noise_total) / cited_total if cited_total else None,
        "question_recall": questions_found / questions_total if questions_total else None,
        "verdicts_reaching_the_adjuster": sum(1 for r in ok if r.summary_had_verdict),
        "summaries_replaced": sum(1 for r in ok if r.summary_replaced),
        "forbidden_citations": sum(len(r.forbidden_cited) for r in ok),
        "clauses_cited": cited_total,
        "dropped_by_checks": drops,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "latency_p50_ms": sorted(r.total_ms for r in ok)[len(ok) // 2] if ok else None,
    }


TARGETS = [
    ("Clause recall", "clause_recall", 0.85, "min"),
    ("Clause precision", "clause_precision", 0.80, "min"),
    ("Claimant question recall", "question_recall", 0.90, "min"),
    ("Verdicts reaching the adjuster", "verdicts_reaching_the_adjuster", 0, "max"),
    ("Forbidden citations", "forbidden_citations", 0, "max"),
    ("Errors", "errors", 0, "max"),
]


def check_targets(metrics: dict) -> list[tuple[str, str, bool]]:
    out = []
    for label, key, target, direction in TARGETS:
        value = metrics.get(key)
        if value is None:
            out.append((label, "not measured", True))
            continue
        met = value >= target if direction == "min" else value <= target
        shown = f"{value:.1%} (target {'≥' if direction == 'min' else '≤'} {target:.0%})" if direction == "min" else f"{value} (target {target})"
        out.append((label, shown, met))
    return out


def render_markdown(metrics: dict, results: list[ReviewScenarioResult], meta: dict) -> str:
    lines = ["# Wording review eval", ""]
    lines += [f"- **{k}**: {v}" for k, v in meta.items()]
    lines += ["", "## Targets", "", "| Metric | Result | Met |", "|---|---|---|"]
    for label, shown, met in check_targets(metrics):
        lines.append(f"| {label} | {shown} | {'yes' if met else '**no**'} |")
    lines += [
        "",
        f"Passed {metrics['passed']}/{metrics['completed']} scenarios · "
        f"{metrics['clauses_cited']} clauses cited · "
        f"{metrics['tokens_in'] + metrics['tokens_out']:,} tokens",
        "",
        "## Scenarios",
        "",
        "| ID | Wording | Expected | Cited | Missed | Noise | Questions |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.status == "error":
            lines.append(f"| {r.id} | — | — | error: {r.error[:60]} | | | |")
            continue
        questions = (
            f"{len(r.captured_question_turns)}/{len(r.expected_question_turns)}" if r.expected_question_turns else "—"
        )
        lines.append(
            f"| {r.id} | {r.wording_ref} | {' '.join(r.expected_sections)} | {' '.join(r.cited_sections)} | "
            f"{' '.join(r.missed_sections) or '—'} | {' '.join(r.noise_sections) or '—'} | {questions} |"
        )
    if metrics["dropped_by_checks"]:
        lines += ["", "## Dropped by the checks", "", "| Reason | Count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(metrics["dropped_by_checks"].items())]
    return "\n".join(lines) + "\n"


def load_results(path: Path) -> list[ReviewScenarioResult]:
    if not path.exists():
        return []
    return [ReviewScenarioResult.model_validate(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def labelled_scenarios() -> list[Scenario]:
    """Every scenario carrying wording-review labels: the intake set plus the review-only set."""
    scenarios = [s for s in load_scenarios() if s.expected.wording_review]
    scenarios += [s for s in load_scenarios(REVIEW_SCENARIO_DIR) if s.expected.wording_review]
    return scenarios


def rescore(out_dir: Path, *, check: bool = False) -> int:
    """Score a finished run again with today's labels. Free: the model's answers are on disk.

    Prints the old numbers beside the new ones, because a metric that moves because the labels
    moved should never be reported as if the model improved.
    """
    scenarios = {s.id: s for s in labelled_scenarios()}
    results = load_results(out_dir / "results.jsonl")
    if not results:
        print(f"error: no results in {out_dir}", file=sys.stderr)
        return 2
    before = compute_metrics(results)

    rescored = []
    for result in results:
        scenario = scenarios.get(result.id)
        if scenario is None or result.status != "ok":
            rescored.append(result)
            continue
        labels = scenario.expected.wording_review
        acceptable = acceptable_sections(scenario, result.wording_ref.split("/")[0])
        rescored.append(result.model_copy(update={
            "expected_sections": labels.sections,
            "also_relevant": labels.also_relevant,
            "missed_sections": [s for s in labels.sections if s not in result.cited_sections],
            "noise_sections": [s for s in result.cited_sections if s not in acceptable],
            "forbidden_cited": [s for s in result.cited_sections if s in labels.must_not_cite],
        }))
    after = compute_metrics(rescored)

    (out_dir / "results.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in rescored))
    meta = {"Run": out_dir.name, "Scored": "re-scored against the current labels and relevance rule"}
    (out_dir / "report.md").write_text(render_markdown(after, rescored, meta))
    (out_dir / "summary.json").write_text(json.dumps(after, indent=2, default=str))

    print(f"re-scored {len(results)} results in {out_dir}\n")
    print(f"{'metric':<28} {'before':>10} {'after':>10}")
    for key in ("clause_recall", "clause_precision", "question_recall"):
        fmt = lambda v: "—" if v is None else f"{v:.1%}"
        print(f"{key:<28} {fmt(before[key]):>10} {fmt(after[key]):>10}")
    print(f"{'passed':<28} {before['passed']:>10} {after['passed']:>10}")
    targets = check_targets(after)
    print()
    for label, shown, met in targets:
        print(f"  {'OK  ' if met else 'MISS'} {label}: {shown}")
    return 1 if check and not all(met for _, _, met in targets) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--limit", type=int, help="run only the first N")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--min-interval", type=float, default=4.0, help="seconds between LLM call starts")
    parser.add_argument("--resume", type=Path, help="existing results directory; skips scenarios already scored")
    parser.add_argument("--provider", choices=["groq", "gemini"])
    parser.add_argument("--models", help="comma-separated model chain")
    parser.add_argument("--prompt", default="v1", help="review prompt version")
    parser.add_argument("--check", action="store_true", help="exit 1 if a target is missed")
    parser.add_argument("--rescore", type=Path, help="re-score a finished run against the current "
                        "labels and relevance rule, without calling a model")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")

    if args.rescore:
        return rescore(args.rescore, check=args.check)

    scenarios = labelled_scenarios()
    if args.only:
        wanted = {v.strip() for v in args.only.split(",") if v.strip()}
        unknown = wanted - {s.id for s in scenarios}
        if unknown:
            parser.error(f"unknown or unlabelled scenario ids: {sorted(unknown)}")
        scenarios = [s for s in scenarios if s.id in wanted]
    if args.limit:
        scenarios = scenarios[: args.limit]

    out_dir = args.resume or RESULTS_DIR / datetime.now().strftime("review-%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    previous = {r.id: r for r in load_results(results_path) if r.status == "ok"}
    if previous:
        results_path.write_text("".join(r.model_dump_json() + "\n" for r in previous.values()))
    todo = [s for s in scenarios if s.id not in previous]

    settings = replace(get_settings(), llm_max_rate_limit_wait_s=30.0)
    if args.provider:
        settings = replace(settings, pipeline_provider=args.provider)
    if args.models:
        models = tuple(m.strip() for m in args.models.split(",") if m.strip())
        settings = (
            replace(settings, groq_models=models)
            if settings.pipeline_provider == "groq"
            else replace(settings, extract_model=models[0], fallback_models=models[1:])
        )
    print(f"{len(scenarios)} labelled scenarios ({len(previous)} already done, {len(todo)} to run) -> {out_dir}")

    done = 0

    def on_result(result: ReviewScenarioResult) -> None:
        nonlocal done
        done += 1
        with results_path.open("a") as f:
            f.write(result.model_dump_json() + "\n")
        mark = "PASS" if result.passed else ("ERR " if result.status == "error" else "FAIL")
        detail = result.error[:70] if result.status == "error" else (
            f"cited {' '.join(result.cited_sections) or 'nothing'}"
            + (f" | missed {' '.join(result.missed_sections)}" if result.missed_sections else "")
        )
        print(f"[{done:>2}/{len(todo)}] {mark} {result.id:<6} {detail}", flush=True)

    if todo:
        try:
            llm = PacedLLM(make_pipeline_llm(settings), args.min_interval)
        except LLMError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        asyncio.run(run_review_eval(todo, llm, concurrency=args.concurrency, prompt_version=args.prompt, on_result=on_result))

    wanted_ids = {s.id for s in scenarios}
    results = [r for r in load_results(results_path) if r.id in wanted_ids]
    order = {s.id: i for i, s in enumerate(scenarios)}
    results.sort(key=lambda r: order[r.id])

    metrics = compute_metrics(results)
    meta = {
        "Run": out_dir.name,
        "Provider": settings.pipeline_provider,
        "Models": ", ".join(settings.groq_models if settings.pipeline_provider == "groq" else settings.model_chain),
        "Review prompt": args.prompt,
        "Scored against": "gold facts (the pipeline is not re-run)",
    }
    (out_dir / "report.md").write_text(render_markdown(metrics, results, meta))
    (out_dir / "summary.json").write_text(json.dumps(metrics, indent=2, default=str))

    print(f"\npassed {metrics['passed']}/{metrics['completed']} scenarios ({metrics['errors']} errors)")
    targets = check_targets(metrics)
    for label, shown, met in targets:
        print(f"  {'OK  ' if met else 'MISS'} {label}: {shown}")
    print(f"tokens: {metrics['tokens_in']:,} in, {metrics['tokens_out']:,} out")
    print(f"report: {out_dir / 'report.md'}")
    if metrics["errors"]:
        print(f"resume with: python -m app.eval.review --resume {out_dir}")
    return 1 if args.check and not all(met for _, _, met in targets) else 0


if __name__ == "__main__":
    sys.exit(main())
