"""Aggregate metrics and the Markdown eval report."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any

from app.domain.models import Route
from app.eval.matchers import SCORED_FIELDS, Outcome
from app.eval.runner import ScenarioResult

# PRD section 8 targets. (metric key, comparison, threshold, label)
TARGETS = [
    ("field_f1", ">=", 0.90, "Field extraction F1"),
    ("route_accuracy", ">=", 0.90, "Routing accuracy"),
    ("safety_recall", ">=", 1.00, "Safety escalation recall"),
    ("false_escalations", "<=", 0, "False safety escalations"),
]

# Assumed list prices (USD per 1M tokens) for Flash-class models. Free tier costs $0;
# this estimates what the same run would cost on a paid key. Update from ai.google.dev/pricing.
PRICE_PER_M_INPUT = 0.30
PRICE_PER_M_OUTPUT = 2.50


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1])


def processing_ms(r: ScenarioResult) -> dict[str, int]:
    """Per-node time excluding rate-limit pacing: LLM nodes use the call's own latency."""
    llm_ms: dict[str, int] = {}
    for call in r.llm_calls:
        llm_ms[call.step] = llm_ms.get(call.step, 0) + call.latency_ms
    return {node: llm_ms.get(node, ms) for node, ms in r.node_ms.items()}


def scenario_latency_ms(r: ScenarioResult) -> int:
    """Pipeline latency without pacing waits. In split mode classify and lookup run in parallel,
    but lookup is ~0 ms, so the sum is a faithful serial latency."""
    return sum(processing_ms(r).values())


def compute_metrics(results: list[ScenarioResult]) -> dict[str, Any]:
    ok = [r for r in results if r.status == "ok"]
    m: dict[str, Any] = {
        "scenarios": len(results),
        "completed": len(ok),
        "errors": len(results) - len(ok),
        "passed": sum(r.passed for r in ok),
    }

    # Fields: micro precision/recall/F1 over all scored fields
    counts: Counter[Outcome] = Counter(f.outcome for r in ok for f in r.fields.values())
    tp = counts[Outcome.CORRECT_VALUE]
    fp = counts[Outcome.HALLUCINATED] + counts[Outcome.WRONG_VALUE]
    fn = counts[Outcome.MISSED] + counts[Outcome.WRONG_VALUE]
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    m["field_precision"] = precision
    m["field_recall"] = recall
    m["field_f1"] = (2 * precision * recall / (precision + recall)) if precision and recall else None
    m["field_outcomes"] = {o.value: counts[o] for o in Outcome}
    m["field_accuracy"] = {
        name: _ratio(
            sum(r.fields[name].outcome in (Outcome.CORRECT_VALUE, Outcome.CORRECT_BLANK) for r in ok), len(ok)
        )
        for name in SCORED_FIELDS
    }

    # Classification and routing
    m["claim_type_accuracy"] = _ratio(sum(r.type_ok for r in ok), len(ok))
    m["route_accuracy"] = _ratio(sum(r.route_ok for r in ok), len(ok))
    m["route_recall"] = {
        route.value: _ratio(
            sum(r.route_ok for r in ok if r.expected_route == route), sum(r.expected_route == route for r in ok)
        )
        for route in Route
    }
    m["confusion"] = Counter((r.expected_route.value, r.predicted_route.value) for r in ok)

    # Safety, measured at the route (what the adjuster sees)
    should = [r for r in ok if r.expected_escalation]
    should_not = [r for r in ok if not r.expected_escalation]
    m["safety_recall"] = _ratio(sum(r.predicted_route == Route.EMERGENCY_ESCALATION for r in should), len(should))
    # None (not 0) when nothing was measured, so an empty run can't "meet" the zero target.
    m["false_escalations"] = (
        sum(r.predicted_route == Route.EMERGENCY_ESCALATION for r in should_not) if should_not else None
    )
    m["safety_cases"] = len(should)

    # Evidence and rule expectations
    evidence_pairs = [pair for r in ok for pair in r.evidence.values()]
    m["evidence_accuracy"] = _ratio(sum(exp == pred for exp, pred in evidence_pairs), len(evidence_pairs))
    m["rule_expectation_pass_rate"] = _ratio(sum(not r.rule_problems for r in ok), len(ok))

    # Per-category pass rate
    by_cat: dict[str, list[ScenarioResult]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)
    m["category_pass"] = {cat: (sum(r.passed for r in rs), len(rs)) for cat, rs in by_cat.items()}

    # Latency, reliability, cost
    totals = [scenario_latency_ms(r) for r in ok]
    m["latency_p50_ms"] = _percentile(totals, 50)
    m["latency_p95_ms"] = _percentile(totals, 95)
    per_node = [processing_ms(r) for r in ok]
    node_names = sorted({n for nodes in per_node for n in nodes})
    m["node_p50_ms"] = {n: _percentile([nodes[n] for nodes in per_node if n in nodes], 50) for n in node_names}
    calls = [c for r in ok for c in r.llm_calls]
    m["llm_calls"] = len(calls)
    m["fallback_rate"] = _ratio(sum(c.fell_back for c in calls), len(calls))
    m["retry_rate"] = _ratio(sum(c.attempts > 1 for c in calls), len(calls))
    m["models_used"] = dict(Counter(c.model for c in calls))
    tokens_in, tokens_out = sum(c.input_tokens for c in calls), sum(c.output_tokens for c in calls)
    m["tokens_in"], m["tokens_out"] = tokens_in, tokens_out
    cost = tokens_in / 1e6 * PRICE_PER_M_INPUT + tokens_out / 1e6 * PRICE_PER_M_OUTPUT
    m["est_cost_usd"] = cost
    m["est_cost_per_claim_usd"] = _ratio(cost, len(ok))
    return m


def check_targets(m: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """(label, display, met) per PRD target. Metrics that could not be computed count as unmet."""
    rows = []
    for key, op, threshold, label in TARGETS:
        value = m.get(key)
        if value is None:
            rows.append((label, "n/a", False))
            continue
        met = value >= threshold if op == ">=" else value <= threshold
        shown = f"{value}" if isinstance(value, int) else _pct(value)
        target = f"{threshold}" if isinstance(threshold, int) else _pct(threshold)
        rows.append((f"{label} ({op} {target})", shown, met))
    return rows


def render_markdown(m: dict[str, Any], results: list[ScenarioResult], meta: dict[str, str]) -> str:
    out: list[str] = ["# ClaimMate Eval Report", ""]
    out += [f"- **{k}:** {v}" for k, v in meta.items()]
    out += [
        f"- **Scenarios:** {m['scenarios']} ({m['completed']} completed, {m['errors']} errors); "
        f"**fully passed:** {m['passed']}/{m['completed']}",
        "",
        "## Targets",
        "",
        "| Metric | Result | Met |",
        "|---|---|---|",
    ]
    out += [f"| {label} | {shown} | {'✅' if met else '❌'} |" for label, shown, met in check_targets(m)]

    out += [
        "",
        "## Extraction",
        "",
        f"Micro precision {_pct(m['field_precision'])} · recall {_pct(m['field_recall'])} · "
        f"**F1 {_pct(m['field_f1'])}**",
        "",
        "Outcomes: " + ", ".join(f"{k} {v}" for k, v in m["field_outcomes"].items()),
        "",
        "| Field | Accuracy |",
        "|---|---|",
    ]
    out += [f"| {name} | {_pct(acc)} |" for name, acc in m["field_accuracy"].items()]

    out += [
        "",
        "## Classification and routing",
        "",
        f"- Claim type accuracy: {_pct(m['claim_type_accuracy'])}",
        f"- Route accuracy: {_pct(m['route_accuracy'])}",
        f"- Safety recall: {_pct(m['safety_recall'])} of {m['safety_cases']} escalation cases; "
        f"false escalations: {m['false_escalations']}",
        f"- Evidence status accuracy: {_pct(m['evidence_accuracy'])}",
        f"- Rule expectations met: {_pct(m['rule_expectation_pass_rate'])}",
        "",
        "| Expected route | Recall |",
        "|---|---|",
    ]
    out += [f"| {route} | {_pct(recall)} |" for route, recall in m["route_recall"].items()]

    routes = [r.value for r in Route]
    short = {r: "".join(w[0] for w in r.split("_")).upper() for r in routes}
    out += ["", "Confusion matrix (rows = expected, columns = predicted): "
            + ", ".join(f"{short[r]} = {r}" for r in routes), ""]
    out += ["| | " + " | ".join(short[r] for r in routes) + " |", "|---" * (len(routes) + 1) + "|"]
    for exp in routes:
        out.append(f"| **{short[exp]}** | " + " | ".join(str(m["confusion"].get((exp, p), "")) for p in routes) + " |")

    out += ["", "## By category", "", "| Category | Passed |", "|---|---|"]
    out += [f"| {cat} | {p}/{n} |" for cat, (p, n) in m["category_pass"].items()]

    out += [
        "",
        "## Latency, reliability, cost",
        "",
        f"- Pipeline latency p50 {m['latency_p50_ms']} ms · p95 {m['latency_p95_ms']} ms (excludes rate-limit pacing)",
        "- Node p50: " + ", ".join(f"{n} {v} ms" for n, v in m["node_p50_ms"].items()),
        f"- LLM calls: {m['llm_calls']} · retried {_pct(m['retry_rate'])} · fell back {_pct(m['fallback_rate'])}",
        "- Models: " + ", ".join(f"{k} ×{v}" for k, v in m["models_used"].items()),
        f"- Tokens: {m['tokens_in']:,} in / {m['tokens_out']:,} out · est. cost ${m['est_cost_usd']:.4f} "
        f"(${(m['est_cost_per_claim_usd'] or 0):.5f}/claim at assumed paid-tier prices)",
    ]

    failures = [r for r in results if not r.passed]
    out += ["", f"## Failures ({len(failures)})", ""]
    for r in failures:
        out.append(f"### {r.id}: {r.title}")
        if r.status == "error":
            out += [f"- **Error:** `{r.error}`", ""]
            continue
        if not r.route_ok:
            out.append(f"- Route: expected `{r.expected_route}`, got `{r.predicted_route}`")
        if not r.type_ok:
            out.append(f"- Claim type: expected `{r.expected_type}`, got `{r.predicted_type}`")
        for name, f in r.fields.items():
            if f.outcome not in (Outcome.CORRECT_VALUE, Outcome.CORRECT_BLANK):
                out.append(f"- `{name}` {f.outcome}: expected `{f.expected}`, got `{f.predicted}`")
        for doc, (exp, pred) in r.evidence.items():
            if exp != pred:
                out.append(f"- Evidence `{doc}`: expected `{exp}`, got `{pred}`")
        out += [f"- Rules: {p}" for p in r.rule_problems]
        if r.expected_escalation != (r.predicted_route == Route.EMERGENCY_ESCALATION) and r.safety_facts:
            out.append("- Extracted safety facts: " + "; ".join(f"`{f}`" for f in r.safety_facts))
        out.append("")
    return "\n".join(out) + "\n"
