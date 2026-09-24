"""Checks the eval labels themselves. A wrong gold label would silently corrupt every metric,
so each scenario's gold facts are run through the real rules engine (no LLM) and must
reproduce the labeled route and rule expectations."""

from collections import Counter

import pytest

from app.domain.models import Classification, Route
from app.domain.policy_store import lookup_policy
from app.eval.scenario import load_scenarios
from app.rules.engine import evaluate

SCENARIOS = load_scenarios()


def test_scenario_set_size_and_coverage():
    assert len(SCENARIOS) >= 50
    routes = Counter(s.expected.route for s in SCENARIOS)
    assert set(routes) == set(Route), f"every route needs at least one scenario: {routes}"
    assert sum(s.expected.escalates for s in SCENARIOS) >= 5
    assert sum(1 for s in SCENARIOS if "negation" in s.tags) >= 5


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_gold_labels_are_consistent_with_rules(scenario):
    gold = scenario.gold_claim_facts()
    decision = evaluate(
        gold,
        Classification(claim_type=scenario.expected.claim_type),
        lookup_policy(gold.policy_number),
        scenario.captures,
        today=scenario.today,
    )
    fired = {f.rule_id for f in decision.findings}
    assert decision.route == scenario.expected.route, f"rules fired: {sorted(fired)}"
    assert set(scenario.expected.rules_fired) <= fired
    assert not set(scenario.expected.rules_not_fired) & fired


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_transcript_is_well_formed(scenario):
    turns = scenario.transcript()
    assert any(t.speaker == "claimant" for t in turns)
    assert len({t.id for t in turns}) == len(turns)
