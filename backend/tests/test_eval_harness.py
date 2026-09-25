import pytest

from app.domain.models import ClaimFacts, Classification, EvidenceRecord, EvidenceStatus, SafetyFact
from app.eval.matchers import Outcome, score_field
from app.eval.report import check_targets, compute_metrics, render_markdown
from app.eval.runner import PacedLLM, run_eval
from app.eval.scenario import load_scenarios
from app.llm.client import LLMCall, LLMResult
from app.pipeline.prompts import ClaimAnalysis, ClaimAnalysisV2, ClaimFactsV2, render_transcript

SCENARIOS = load_scenarios()


class OracleLLM:
    """Answers every scenario with its gold labels, optionally corrupted by `sabotage`."""

    def __init__(self, sabotage=None):
        self.sabotage = sabotage or {}
        self.current = None

    async def generate(self, *, step, system, prompt, schema):
        if schema in (ClaimFacts, ClaimAnalysis, ClaimFactsV2, ClaimAnalysisV2):
            self.current = next(s for s in SCENARIOS if render_transcript(s.transcript()) in prompt)
            facts = self.current.gold_claim_facts()
            if self.current.id in self.sabotage:
                facts = self.sabotage[self.current.id](facts)
        classification = Classification(claim_type=self.current.expected.claim_type)
        if schema is ClaimFacts:
            value = facts
        elif schema is ClaimAnalysis:
            value = ClaimAnalysis(facts=facts, classification=classification)
        elif schema is ClaimFactsV2:
            value = ClaimFactsV2.model_validate(facts.model_dump())
        elif schema is ClaimAnalysisV2:
            value = ClaimAnalysisV2(facts=ClaimFactsV2.model_validate(facts.model_dump()), classification=classification)
        else:
            value = classification
        return LLMResult[schema](
            value=value, call=LLMCall(step=step, model="oracle", attempts=1, latency_ms=1, input_tokens=1000, output_tokens=200)
        )


@pytest.mark.parametrize(("mode", "prompt"), [("split", "v1"), ("single", "v1"), ("split", "v2"), ("single", "v2")])
async def test_oracle_scores_perfectly_on_all_scenarios(mode, prompt):
    results = await run_eval(SCENARIOS, OracleLLM(), concurrency=1, mode=mode, prompt_version=prompt)
    failures = [(r.id, r.error or r.rule_problems) for r in results if not r.passed]
    assert failures == []

    m = compute_metrics(results)
    assert m["passed"] == len(SCENARIOS)
    assert m["field_f1"] == 1.0
    assert m["route_accuracy"] == 1.0
    assert m["safety_recall"] == 1.0 and m["false_escalations"] == 0
    assert all(met for _, _, met in check_targets(m))
    assert m["tokens_in"] == 1000 * m["llm_calls"]


async def test_sabotaged_answers_are_caught():
    def hallucinate_date(facts):
        return facts.model_copy(update={"date_of_loss": "2026-09-01"})

    def miss_injury(facts):
        return facts.model_copy(update={"safety_facts": []})

    def invent_injury(facts):
        return facts.model_copy(update={"safety_facts": [SafetyFact(category="injury", status="present", description="x")]})

    def claim_received(facts):
        return facts.model_copy(update={"evidence": [EvidenceRecord(document_type="damage_photo", status=EvidenceStatus.RECEIVED)]})

    sabotage = {"in-01": hallucinate_date, "sf-01": miss_injury, "ng-01": invent_injury, "ev-01": claim_received}
    subset = [s for s in SCENARIOS if s.id in sabotage]
    results = {r.id: r for r in await run_eval(subset, OracleLLM(sabotage), concurrency=1)}

    assert results["in-01"].fields["date_of_loss"].outcome == Outcome.HALLUCINATED
    assert not results["sf-01"].route_ok
    assert results["ng-01"].predicted_route == "emergency_escalation"
    assert not results["ev-01"].evidence_ok

    m = compute_metrics(list(results.values()))
    assert m["safety_recall"] == 0.0 and m["false_escalations"] == 1
    report = render_markdown(m, list(results.values()), {"Run": "test"})
    assert "## Failures (4)" in report
    assert "`date_of_loss` hallucinated: expected `not specified`, got `2026-09-01`" in report
    assert "❌" in report


async def test_errors_are_recorded_not_raised():
    class Broken:
        async def generate(self, **_):
            raise RuntimeError("model down")

    results = await run_eval(SCENARIOS[:2], Broken(), concurrency=2)
    assert [r.status for r in results] == ["error", "error"]
    assert "model down" in results[0].error
    m = compute_metrics(results)
    assert m["errors"] == 2
    assert m["false_escalations"] is None
    assert not any(met for _, _, met in check_targets(m)), "no data must never count as meeting a target"


async def test_paced_llm_spaces_calls(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.eval.runner.asyncio.sleep", fake_sleep)
    paced = PacedLLM(OracleLLM(), min_interval_s=5)
    paced._last_start = 1e12  # pretend a call just started far in the "future"
    scenario = SCENARIOS[0]
    await paced.generate(step="x", system="", prompt=render_transcript(scenario.transcript()), schema=ClaimFacts)
    assert sleeps and sleeps[0] > 0


@pytest.mark.parametrize(
    ("field", "expected", "predicted", "outcome"),
    [
        ("policyholder_name", "Omar Haddad", "omar haddad", Outcome.CORRECT_VALUE),
        ("policyholder_name", "Omar Haddad", "Omar Hadad", Outcome.WRONG_VALUE),
        ("policy_number", "HO-20417", "H0 2O417", Outcome.CORRECT_VALUE),
        ("contact", "720-555-0148", "Cell: (720) 555-0148, email x@y.com", Outcome.CORRECT_VALUE),
        ("contact", "sofia.m@example.com", "SOFIA.M@EXAMPLE.COM", Outcome.CORRECT_VALUE),
        ("loss_location", "Mesa", "Mesa, AZ", Outcome.CORRECT_VALUE),
        ("loss_location", "Mesa", "Tempe, AZ", Outcome.WRONG_VALUE),
        ("estimated_loss_usd", 7500, 7500.0, Outcome.CORRECT_VALUE),
        ("estimated_loss_usd", 7500, 5000.0, Outcome.WRONG_VALUE),
        ("estimated_loss_usd", None, None, Outcome.CORRECT_BLANK),
        ("estimated_loss_usd", None, 100.0, Outcome.HALLUCINATED),
        ("date_of_loss", "2026-09-23", "not specified", Outcome.MISSED),
        ("date_of_loss", "not specified", "unknown", Outcome.CORRECT_BLANK),
        ("loss_description", "anything", "different words", Outcome.CORRECT_VALUE),
    ],
)
def test_field_matching(field, expected, predicted, outcome):
    assert score_field(field, expected, predicted) == outcome


def test_latency_excludes_pacing_waits():
    from app.eval.report import scenario_latency_ms
    from app.eval.runner import ScenarioResult
    from app.llm.client import LLMCall

    r = ScenarioResult(
        id="x", title="", category="", tags=[], status="ok",
        expected_route="needs_docs", expected_type="other", expected_escalation=False,
        node_ms={"extract_facts": 12_000, "classify": 11_000, "lookup_policy": 1, "evaluate_rules": 3, "build_packet": 1},
        llm_calls=[LLMCall(step="extract_facts", model="m", attempts=1, latency_ms=2_000),
                   LLMCall(step="classify", model="m", attempts=1, latency_ms=800)],
        total_ms=23_010,
    )
    assert scenario_latency_ms(r) == 2_000 + 800 + 1 + 3 + 1
