"""Policy wordings (M8 step 1): the documents themselves, the parser, and the product mapping."""

from pathlib import Path

import pytest

from app.domain.models import Product
from app.domain.policy_store import _SEED, lookup_policy
from app.domain.wordings import WORDING_DIR, PolicyWording, load_wordings, parse_wording, wording_for

WORDINGS = load_wordings()


def _wording(body: str) -> PolicyWording:
    front = '---\nwording_id: demo\nversion: v1\nproduct: medical\ntitle: Demo\n---\n'
    return parse_wording(front + body, Path("demo-v1.md"))


# --- the documents -----------------------------------------------------------


def test_every_product_has_exactly_one_wording():
    assert set(WORDINGS) == set(Product)
    assert len({w.ref for w in WORDINGS.values()}) == len(Product)


@pytest.mark.parametrize("product", list(Product))
def test_wordings_are_two_to_four_pages(product):
    """D14f: long enough for real exclusions and conditions, short enough for one prompt."""
    words = sum(len(c.text.split()) for c in wording_for(product).clauses)
    assert 900 <= words <= 2200, f"{product} wording is {words} words"


@pytest.mark.parametrize("product", list(Product))
def test_every_wording_has_the_parts_a_review_needs(product):
    """A review cites coverage, exclusions, and conditions, so each must exist to be cited."""
    headings = " ".join(f"{c.part} {c.heading}" for c in wording_for(product).clauses).lower()
    assert "what we cover" in headings or "what we pay" in headings
    assert "do not cover" in headings or "do not pay" in headings
    assert "deductible" in headings
    assert "tell us promptly" in headings


@pytest.mark.parametrize("product", list(Product))
def test_notice_window_matches_the_rules_engine(product):
    """TIMING-002 routes to SIU after 90 days; the wording must say the same number.

    Compared with whitespace collapsed, because a wording wraps its lines wherever the sentence
    happens to reach the margin. The quote checks in step 2 normalize the same way (spec section 9.4).
    """
    prompt = " ".join(wording_for(product).as_prompt_text().split())
    assert "within ninety days of the date of" in prompt


def test_clause_numbers_are_unique_and_grouped_under_their_part():
    for wording in WORDINGS.values():
        assert len(set(wording.numbers)) == len(wording.numbers)
        for clause in wording.clauses:
            assert clause.part, f"{wording.ref} {clause.number} has no part heading"


def test_the_demo_claim_can_cite_the_clauses_the_spec_shows():
    """docs/07-policy-review.md section 3 shows MD-4418 citing 2.1, 3.1, and 5.3."""
    medical = wording_for(Product.MEDICAL)
    assert medical.clause("2.1").text.startswith("We reimburse eligible medical costs you paid yourself")
    assert medical.clause("3.1").text.startswith("You pay the first amount shown as your annual deductible")
    assert "explanation of benefits" in medical.clause("5.3").text.lower()


# --- the product mapping -----------------------------------------------------


def test_every_seed_policy_resolves_to_a_wording():
    for record in _SEED:
        assert wording_for(record.product).product == record.product


def test_policy_line_and_product_agree():
    expected = {
        "Homeowners (HO-3)": Product.HOMEOWNERS,
        "Renters (HO-4)": Product.RENTERS,
        "Personal auto": Product.AUTO,
        "Single trip travel": Product.TRAVEL,
        "Supplemental medical reimbursement": Product.MEDICAL,
    }
    for record in _SEED:
        assert record.product == expected[record.policy_line]


def test_lookup_returns_the_product():
    assert lookup_policy("MD-4418").record.product is Product.MEDICAL
    assert lookup_policy("h o 2 0 4 1 7").record.product is Product.HOMEOWNERS


# --- the parser --------------------------------------------------------------


def test_citations_tolerate_the_section_sign_and_spacing():
    medical = wording_for(Product.MEDICAL)
    assert medical.clause("§2.1") is medical.clause(" 2.1 ") is medical.clause("2.1.")
    assert medical.clause("9.9") is None
    assert medical.clause("") is None


def test_prompt_text_carries_every_clause():
    for wording in WORDINGS.values():
        prompt = wording.as_prompt_text()
        assert wording.ref in prompt
        for clause in wording.clauses:
            assert clause.heading in prompt
            assert clause.text.split("\n")[0] in prompt


def test_clause_text_is_joined_across_wrapped_lines():
    wording = _wording("## 2. Part\n### 2.1 Clause\nFirst line\nsecond line.\n")
    assert wording.clauses[0].text == "First line\nsecond line."
    assert wording.clauses[0].label == "§2.1 Clause"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("## 2. Part\nLoose text\n### 2.1 Clause\nBody.\n", "outside a numbered clause"),
        ("## 2. Part\n### 3.1 Clause\nBody.\n", "not under part"),
        ("## 2. Part\n### 2.1 One\nBody.\n### 2.1 Two\nBody.\n", "duplicate clause numbers"),
        ("## 2. Part\n### 2.1 Clause\n\n", "has no text"),
        ("## 2. Part\n#### 2.1.1 Too deep\nBody.\n", "unexpected heading"),
        ("## 2. Part\n", "no clauses found"),
    ],
)
def test_structural_mistakes_are_rejected(body, message):
    with pytest.raises(ValueError, match=message):
        _wording(body)


def test_front_matter_is_required():
    with pytest.raises(ValueError, match="missing YAML front matter"):
        parse_wording("## 2. Part\n### 2.1 Clause\nBody.\n", Path("demo-v1.md"))


def test_file_names_match_their_reference():
    for path in WORDING_DIR.glob("*.md"):
        wording = parse_wording(path.read_text(), path)
        assert path.stem == f"{wording.wording_id}-{wording.version}"
