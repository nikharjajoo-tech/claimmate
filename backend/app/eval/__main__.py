"""Run the eval harness.

    python -m app.eval                          # all scenarios
    python -m app.eval --limit 5                # quick smoke run
    python -m app.eval --category safety_escalation,negation_trap
    python -m app.eval --only hp-01,sf-01
    python -m app.eval --resume eval/results/20260924-1530   # finish an interrupted run
    python -m app.eval --provider groq --models openai/gpt-oss-120b   # benchmark one model
    python -m app.eval --provider gemini --models gemini-3-flash-preview
    python -m app.eval --check                  # exit 1 if a PRD target is missed (for CI)

Writes results.jsonl (one line per scenario, appended as each finishes), summary.json, and
report.md into eval/results/<timestamp>/.
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

from app.config import get_settings
from app.eval.report import check_targets, compute_metrics, render_markdown
from app.eval.runner import PacedLLM, ScenarioResult, load_results, run_eval
from app.eval.scenario import load_scenarios
from app.llm.client import LLMError
from app.llm.factory import make_pipeline_llm

RESULTS_DIR = Path(__file__).resolve().parents[2] / "eval" / "results"


def _csv(value: str | None) -> set[str]:
    return {v.strip() for v in value.split(",") if v.strip()} if value else set()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--category", help="comma-separated categories")
    parser.add_argument("--limit", type=int, help="run only the first N matching scenarios")
    parser.add_argument("--concurrency", type=int, default=2, help="scenarios in flight at once (default 2)")
    parser.add_argument("--min-interval", type=float, default=4.0, help="seconds between LLM call starts (default 4)")
    parser.add_argument("--resume", type=Path, help="existing results directory; skips scenarios already scored")
    parser.add_argument("--provider", choices=["groq", "gemini"], help="benchmark one provider (default: from .env)")
    parser.add_argument("--models", help="comma-separated model chain for the provider (default: from .env); "
                        "pin a single model so every scenario is scored against the same one")
    parser.add_argument("--pipeline", choices=["split", "single"], help="pipeline mode (default: from .env)")
    parser.add_argument("--prompt", choices=["v1", "v2", "v3"], help="extraction prompt version (default: from .env)")
    parser.add_argument("--check", action="store_true", help="exit 1 if any PRD target is missed")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")

    scenarios = load_scenarios()
    only, categories = _csv(args.only), _csv(args.category)
    if only:
        unknown = only - {s.id for s in scenarios}
        if unknown:
            parser.error(f"unknown scenario ids: {sorted(unknown)}")
        scenarios = [s for s in scenarios if s.id in only]
    if categories:
        scenarios = [s for s in scenarios if s.category in categories]
    if args.limit:
        scenarios = scenarios[: args.limit]

    out_dir = args.resume or RESULTS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    previous = {r.id: r for r in load_results(results_path) if r.status == "ok"}
    if previous:  # rewrite without failed attempts so reruns replace them
        results_path.write_text("".join(r.model_dump_json() + "\n" for r in previous.values()))
    todo = [s for s in scenarios if s.id not in previous]

    # A batch run can sit out per-minute rate limits that a live call could not afford to wait for.
    settings = replace(get_settings(), llm_max_rate_limit_wait_s=30.0)
    if args.provider:
        settings = replace(settings, pipeline_provider=args.provider)
    if args.pipeline:
        settings = replace(settings, pipeline_mode=args.pipeline)
    if args.prompt:
        settings = replace(settings, prompt_version=args.prompt)
    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        if settings.pipeline_provider == "groq":
            settings = replace(settings, groq_models=tuple(models))
        else:
            settings = replace(settings, extract_model=models[0], fallback_models=tuple(models[1:]))
    chain = {
        "groq": [f"groq:{m}" for m in settings.groq_models],
        "gemini": settings.model_chain,
    }
    if settings.pipeline_provider in chain:
        model_desc = " -> ".join(chain[settings.pipeline_provider])
    else:
        model_desc = " -> ".join((chain["groq"] if settings.groq_api_key else []) + (chain["gemini"] if settings.has_api_key else []))
    print(f"{len(scenarios)} scenarios ({len(previous)} already done, {len(todo)} to run) -> {out_dir}")
    print(f"models: {model_desc} | pipeline {settings.pipeline_mode} | prompt {settings.prompt_version} | concurrency {args.concurrency} | "
          f"min interval {args.min_interval}s")

    done = 0

    def on_result(result: ScenarioResult) -> None:
        nonlocal done
        done += 1
        with results_path.open("a") as f:
            f.write(result.model_dump_json() + "\n")
        mark = "PASS" if result.passed else ("ERR " if result.status == "error" else "FAIL")
        detail = result.error[:80] if result.status == "error" else f"{result.predicted_route} ({result.total_ms} ms)"
        print(f"[{done:>2}/{len(todo)}] {mark} {result.id:<6} {detail}", flush=True)

    if todo:
        try:
            llm = PacedLLM(make_pipeline_llm(settings), args.min_interval)
        except LLMError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        asyncio.run(run_eval(todo, llm, concurrency=args.concurrency, mode=settings.pipeline_mode,
                             prompt_version=settings.prompt_version, on_result=on_result))

    wanted = {s.id for s in scenarios}
    results = [r for r in load_results(results_path) if r.id in wanted]
    order = {s.id: i for i, s in enumerate(scenarios)}
    results.sort(key=lambda r: order[r.id])

    metrics = compute_metrics(results)
    meta = {
        "Run": out_dir.name,
        "Models": model_desc,
        "Pipeline": f"{settings.pipeline_mode} ({'1 LLM call' if settings.pipeline_mode == 'single' else '2 LLM calls'})",
        "Prompt": settings.prompt_version,
        "Reference date": str(scenarios[0].today) if scenarios else "n/a",
    }
    (out_dir / "report.md").write_text(render_markdown(metrics, results, meta))
    serializable = {**metrics, "confusion": {f"{e} -> {p}": n for (e, p), n in metrics["confusion"].items()}}
    (out_dir / "summary.json").write_text(json.dumps(serializable, indent=2, default=str))

    targets = check_targets(metrics)
    print(f"\npassed {metrics['passed']}/{metrics['completed']} scenarios ({metrics['errors']} errors)")
    for label, shown, met in targets:
        print(f"  {'OK  ' if met else 'MISS'} {label}: {shown}")
    print(f"report: {out_dir / 'report.md'}")
    if metrics["errors"]:
        print(f"resume with: python -m app.eval --resume {out_dir}")
    return 1 if args.check and not all(met for _, _, met in targets) else 0


if __name__ == "__main__":
    sys.exit(main())
