from datetime import date

import pytest

from app.domain.models import ClaimFacts
from app.domain.policy_store import lookup_policy, names_match, normalize_policy_number, policy_issues


@pytest.mark.parametrize(
    "spoken",
    ["HO-20417", "ho 20417", "H0-20417", "HO-2O417", "h o 2 0 4 1 7", "  HO20417. "],
)
def test_voice_variants_of_policy_number_match(spoken):
    result = lookup_policy(spoken)
    assert result.found
    assert result.record.policyholder_name == "Elena Brooks"


def test_unknown_and_blank_policy_numbers():
    assert not lookup_policy("ZZ-99999").found
    assert not lookup_policy("").found
    assert not lookup_policy("not specified").found
    assert lookup_policy("").message.startswith("No policy number")


def test_normalization_is_symmetric():
    assert normalize_policy_number("HO-20417") == normalize_policy_number("H0 2O4I7".replace("I", "1"))


def _facts(**overrides):
    base = dict(policyholder_name="Elena Brooks", policy_number="HO-20417", date_of_loss="2026-09-20")
    return ClaimFacts(**(base | overrides))


def test_matching_policy_has_no_issues():
    assert policy_issues(_facts(), lookup_policy("HO-20417")) == []


def test_name_match_ignores_case_and_punctuation():
    assert policy_issues(_facts(policyholder_name="elena  brooks."), lookup_policy("HO-20417")) == []


def test_policy_issues_detected():
    assert "Policy status is lapsed." in policy_issues(
        _facts(policyholder_name="Tom Fischer", policy_number="AU-10001", date_of_loss="2026-07-01"),
        lookup_policy("AU-10001"),
    )
    assert "Loss date falls outside the policy period." in policy_issues(
        _facts(date_of_loss="2025-06-01"), lookup_policy("HO-20417")
    )
    assert "Claimant name does not match the policyholder on record." in policy_issues(
        _facts(policyholder_name="Someone Else"), lookup_policy("HO-20417")
    )
    assert policy_issues(_facts(policyholder_name="Tom Fischer", policy_number="AU-10001"), lookup_policy("AU-10001")) == [
        "Policy status is lapsed.", "Loss date falls outside the policy period."
    ]
    assert policy_issues(_facts(), lookup_policy("ZZ-1")) == ["Policy number could not be verified."]


def test_uncollected_facts_are_not_reported_as_mismatches():
    blank = _facts(policyholder_name="not specified", date_of_loss="not specified")
    assert policy_issues(blank, lookup_policy("HO-20417")) == []


def test_period_bounds_are_inclusive():
    record = lookup_policy("HO-20417").record
    assert record.effective_start == date(2026, 1, 1)
    assert policy_issues(_facts(date_of_loss="2026-01-01"), lookup_policy("HO-20417")) == []
    assert policy_issues(_facts(date_of_loss="2026-12-31"), lookup_policy("HO-20417")) == []


# --- names as they are heard (2026-10-04) ------------------------------------


@pytest.mark.parametrize(
    ("spoken", "recorded"),
    [
        ("Grace Lee", "Grace Liu"),        # speech-to-text, the case that prompted this
        ("Omar Hadad", "Omar Haddad"),     # a spelled-out name heard one letter short (co-04)
        ("Grayce Liu", "Grace Liu"),
        ("Elena Smyth", "Elena Smith"),
        ("Elena M Brooks", "Elena Brooks"),  # a middle name the record does not carry
        ("elena  brooks.", "Elena Brooks"),
    ],
)
def test_the_same_name_heard_imperfectly_still_matches(spoken, recorded):
    assert names_match(spoken, recorded)


@pytest.mark.parametrize(
    ("spoken", "recorded"),
    [
        ("Rachel Stone", "Elena Brooks"),  # po-05: someone else's policy
        ("John Smith", "Jane Smith"),      # same sound, different person
        ("Liu", "Grace Liu"),              # a partial name is not a match
        ("Daniel Ortiz", "Grace Liu"),
        ("Elena Brooks", "Elena Brooksworth"),
    ],
)
def test_a_different_person_is_still_caught(spoken, recorded):
    assert not names_match(spoken, recorded)


def test_a_mis_heard_name_no_longer_sends_an_honest_claim_to_policy_review():
    """Before this, "Grace Lee" against a policy reading "Grace Liu" fired POLICY-001."""
    facts = _facts(policyholder_name="Grace Lee", policy_number="MD-4418", date_of_loss="2026-09-14")
    assert policy_issues(facts, lookup_policy("MD-4418")) == []


def test_the_name_check_still_fires_for_someone_elses_policy():
    facts = _facts(policyholder_name="Rachel Stone")
    assert "Claimant name does not match the policyholder on record." in policy_issues(
        facts, lookup_policy("HO-20417")
    )
