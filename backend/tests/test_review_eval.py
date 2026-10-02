"""The wording-review eval harness (M8 step 5): labels, scoring, and the report. No live model."""

import pytest

from app.domain.models import Product
from app.domain.policy_store import lookup_policy
from app.domain.wordings import wording_for
from app.eval.review import (
    check_targets,
    compute_metrics,
    labelled_scenarios,
    render_markdown,
    run_review_eval,
    score_review,
)
from app.eval.scenario import REVIEW_SCENARIO_DIR, load_scenarios
from app.llm.client import LLMCall, LLMResult
from app.review.models import DraftClause, DraftQuestion, PolicyReview, ReviewClause, ReviewDraft, ReviewQuestion

SCENARIOS = {s.id: s for s in labelled_scenarios()}


HOMEOWNERS = wording_for(Product.HOMEOWNERS)


def review(sections=("2.5", "3.1"), questions=(), summary="A basement flooded on 2026-09-23.", **kw) -> PolicyReview:
    """A review as the checks would have produced it: quotes taken from the wording itself."""
    wording = kw.pop("wording", HOMEOWNERS)
    return PolicyReview(
        wording_ref=wording.ref,
        summary=summary,
        clauses=[
            ReviewClause(
                section=s,
                heading=wording.clause(s).heading,
                part=wording.clause(s).part,
                quote=wording.clause(s).text.split(". ")[0],
                anchor="x",
                why_it_matters="Relevant.",
            )
            for s in sections
        ],
        questions=[ReviewQuestion(turn_id=t, quote="q", anchor="q") for t in questions],
        llm_call=LLMCall(step="wording_review", model="fake", attempts=1, latency_ms=10, input_tokens=4800, output_tokens=300),
        **kw,
    )


# --- the labels themselves ---------------------------------------------------


def test_the_labelled_set_covers_every_product_and_includes_questions():
    assert len(SCENARIOS) == 16
    wordings = set()
    for scenario in SCENARIOS.values():
        record = lookup_policy(scenario.expected.facts.policy_number).record
        assert record is not None, f"{scenario.id} has no resolvable policy to review"
        wordings.add(record.product.value)
    assert wordings == {"homeowners", "renters", "auto", "travel", "medical"}
    assert sum(len(s.expected.wording_review.question_turns) for s in SCENARIOS.values()) >= 6


def test_every_labelled_section_exists_in_the_wording_it_will_be_checked_against():
    """A label naming a clause that does not exist would make recall unreachable, silently."""
    for scenario in SCENARIOS.values():
        labels = scenario.expected.wording_review
        wording = wording_for(lookup_policy(scenario.expected.facts.policy_number).record.product)
        for section in labels.sections + labels.also_relevant:
            assert wording.clause(section) is not None, f"{scenario.id}: {wording.ref} has no §{section}"
        for section in labels.must_not_cite:
            assert wording.clause(section) is None, f"{scenario.id}: §{section} exists, so it cannot be forbidden"


def test_labelled_question_turns_are_claimant_turns_that_ask_something():
    for scenario in SCENARIOS.values():
        turns = {t.id: t for t in scenario.transcript()}
        for turn_id in scenario.expected.wording_review.question_turns:
            assert turn_id in turns, f"{scenario.id}: no turn {turn_id}"
            assert turns[turn_id].speaker == "claimant", f"{scenario.id}: {turn_id} is not a claimant turn"
            assert "?" in turns[turn_id].text, f"{scenario.id}: {turn_id} contains no question"


def test_the_intake_scenario_set_is_untouched():
    """Runs A-G were measured on these 50; labels may be added, transcripts may not change."""
    intake = load_scenarios()
    assert len(intake) == 50
    assert len(load_scenarios(REVIEW_SCENARIO_DIR)) == 4


# --- scoring -----------------------------------------------------------------


def test_a_perfect_review_passes():
    scenario = SCENARIOS["hp-01"]
    result = score_review(scenario, review(sections=("2.5", "3.1")), 1200)
    assert result.passed
    assert result.section_recall == 1.0
    assert result.missed_sections == [] and result.noise_sections == []


def test_a_missed_clause_is_counted_and_fails_the_scenario():
    result = score_review(SCENARIOS["hp-01"], review(sections=("2.5",)), 1200)
    assert result.missed_sections == ["3.1"]
    assert result.section_recall == 0.5
    assert not result.passed


def test_an_acceptable_clause_is_not_noise_but_an_unrelated_one_is():
    result = score_review(SCENARIOS["hp-01"], review(sections=("2.5", "3.1", "4.1", "3.6")), 1200)
    assert result.noise_sections == ["3.6"]  # 4.1 is listed as also_relevant
    assert result.passed  # noise costs precision, it does not fail the scenario


def test_a_forbidden_citation_fails_the_scenario():
    scenario = SCENARIOS["wr-03"]
    cited = review(sections=("2.1", "5.3"))
    cited.clauses.append(cited.clauses[0].model_copy(update={"section": "9"}))
    result = score_review(scenario, cited, 1200)
    assert result.forbidden_cited == ["9"]
    assert not result.passed


def test_question_recall_counts_only_the_labelled_turns():
    scenario = SCENARIOS["wr-01"]  # expects t2 and t6
    result = score_review(scenario, review(sections=("2.1", "3.1"), questions=("t2",)), 1200)
    assert result.question_recall == 0.5
    assert not result.passed

    full = score_review(scenario, review(sections=("2.1", "3.1"), questions=("t2", "t6")), 1200)
    assert full.question_recall == 1.0 and full.passed


def test_a_verdict_that_reached_the_adjuster_fails_however_good_the_clauses_are():
    result = score_review(
        SCENARIOS["hp-01"], review(sections=("2.5", "3.1"), summary="This loss is covered and we will pay $9,000."), 1200
    )
    assert result.summary_had_verdict
    assert not result.passed


def test_a_replaced_summary_is_recorded_but_does_not_fail():
    """The guard did its job: the adjuster never saw the verdict."""
    result = score_review(
        SCENARIOS["hp-01"],
        review(sections=("2.5", "3.1"), summary="Elena Brooks reported a flood.", summary_replaced=True),
        1200,
    )
    assert result.summary_replaced and not result.summary_had_verdict
    assert result.passed


# --- metrics and report ------------------------------------------------------


def test_metrics_aggregate_across_scenarios():
    results = [
        score_review(SCENARIOS["hp-01"], review(sections=("2.5", "3.1")), 1000),
        # 3.4 (business property and vehicles) is real but irrelevant to a stolen laptop: noise.
        score_review(SCENARIOS["hp-03"], review(sections=("2.2", "4.1", "3.4"), wording=wording_for(Product.RENTERS)), 1400),
        score_review(SCENARIOS["wr-01"], review(sections=("2.1", "3.1"), questions=("t2",), wording=wording_for(Product.MEDICAL)), 1200),
    ]
    metrics = compute_metrics(results)
    assert metrics["completed"] == 3
    assert metrics["clause_recall"] == pytest.approx(5 / 6)  # hp-03 missed 5.3
    assert metrics["clause_precision"] == pytest.approx(6 / 7)  # one noisy citation out of seven
    assert metrics["question_recall"] == 0.5
    assert metrics["verdicts_reaching_the_adjuster"] == 0
    assert metrics["tokens_in"] == 14400
    assert "Clause recall" in render_markdown(metrics, results, {"Run": "test"})


def test_targets_fail_loudly_when_a_verdict_gets_through():
    metrics = compute_metrics([
        score_review(SCENARIOS["hp-01"], review(sections=("2.5", "3.1"), summary="The claim is covered."), 1000)
    ])
    failed = {label for label, _, met in check_targets(metrics) if not met}
    assert "Verdicts reaching the adjuster" in failed


def test_an_unmeasured_metric_is_not_reported_as_a_miss():
    """A subset run with no question labels must not look like a question-recall failure."""
    metrics = compute_metrics([score_review(SCENARIOS["hp-01"], review(sections=("2.5", "3.1")), 1000)])
    assert metrics["question_recall"] is None
    assert all(met for _, _, met in check_targets(metrics))


# --- the runner, against a scripted model ------------------------------------


class ScriptedLLM:
    def __init__(self, draft):
        self.draft, self.prompts = draft, []

    async def generate(self, *, step, system, prompt, schema, image=None):
        self.prompts.append(prompt)
        return LLMResult[schema](
            value=self.draft,
            call=LLMCall(step=step, model="fake", attempts=1, latency_ms=10, input_tokens=4800, output_tokens=300),
        )


async def test_the_runner_uses_gold_facts_and_the_right_wording():
    scenario = SCENARIOS["hp-01"]
    anchor = HOMEOWNERS.clause("2.5").text[:60]
    llm = ScriptedLLM(ReviewDraft(
        summary="Elena Brooks reported a basement flood.",
        clauses=[DraftClause(section="2.5", anchor=anchor, why_it_matters="The sump overflowed.")],
        questions=[DraftQuestion(turn_id="t2", anchor="nothing like this")],
    ))
    [result] = await run_review_eval([scenario], llm, concurrency=1)

    assert result.status == "ok"
    assert result.wording_ref == "homeowners/v1"
    assert result.cited_sections == ["2.5"] and result.missed_sections == ["3.1"]
    assert "Elena Brooks" in llm.prompts[0]  # gold facts went in
    assert "HO-20417" in llm.prompts[0]


async def test_one_failing_scenario_does_not_sink_the_run():
    class Broken:
        async def generate(self, **kwargs):
            raise RuntimeError("model exploded")

    results = await run_review_eval([SCENARIOS["hp-01"], SCENARIOS["hp-03"]], Broken(), concurrency=1)
    assert [r.status for r in results] == ["error", "error"]
    metrics = compute_metrics(results)
    assert metrics["errors"] == 2 and metrics["completed"] == 0
    assert not [met for label, _, met in check_targets(metrics) if label == "Errors" and met]


# --- the uniform relevance rule (added after run 1, 2026-10-02) --------------


def test_duties_after_loss_and_the_deductible_are_acceptable_on_any_claim():
    """An adjuster checks notice, mitigation, documents and the deductible on every claim, so
    citing them is never noise. They are never *required*, so recall still has to be earned."""
    from app.eval.review import ALWAYS_ACCEPTABLE, acceptable_sections

    scenario = SCENARIOS["hp-01"]  # labels ask for 2.5 and 3.1
    allowed = acceptable_sections(scenario, "homeowners")
    assert {"5.1", "5.2", "5.3", "4.1"} <= allowed
    assert "3.6" not in allowed  # business and intentional acts: still noise on a flood claim

    result = score_review(scenario, review(sections=("2.5", "3.1", "5.1", "5.2")), 1000)
    assert result.noise_sections == []
    assert result.missed_sections == []

    # The rule does not let a scenario pass on duties alone.
    duties_only = score_review(scenario, review(sections=("5.1", "5.2", "5.3")), 1000)
    assert duties_only.missed_sections == ["2.5", "3.1"]
    assert not duties_only.passed
    assert set(ALWAYS_ACCEPTABLE) == {"homeowners", "renters", "auto", "travel", "medical"}


def test_a_forbidden_clause_stays_noise_even_if_the_rule_would_allow_it():
    from app.eval.review import acceptable_sections

    scenario = SCENARIOS["wr-03"].model_copy(deep=True)
    scenario.expected.wording_review.must_not_cite = ["5.1"]
    assert "5.1" not in acceptable_sections(scenario, "renters")


def test_every_rule_clause_exists_in_its_wording():
    from app.domain.models import Product
    from app.eval.review import ALWAYS_ACCEPTABLE

    for product in Product:
        wording = wording_for(product)
        for section in ALWAYS_ACCEPTABLE[wording.wording_id]:
            assert wording.clause(section) is not None, f"{wording.ref} has no §{section}"
