"""Policy review service (M8 step 2): the checks that stand between a draft and an adjuster.

No live model: a scripted LLM returns drafts, including the drafts we most need to survive
contact with an adjuster (invented clauses, verdicts, injected instructions).
"""

import pytest

from app.domain.models import (
    ClaimFacts,
    ClaimType,
    Classification,
    DocumentType,
    EvidenceCapture,
    Product,
    Severity,
)
from app.domain.policy_store import lookup_policy
from app.domain.wordings import wording_for
from app.llm.client import LLMCall, LLMResult
from app.pipeline.prompts import Turn
from app.review.checks import has_verdict_language, locate, normalize, sentences_of, verify
from app.review.models import DraftClause, DraftQuestion, PolicyReview, ReviewDraft
from app.review.prompts import review_prompt, review_system
from app.review.service import generate_policy_review, wording_for_policy

MEDICAL = wording_for(Product.MEDICAL)
POLICY = lookup_policy("MD-4418")

FACTS = ClaimFacts(
    policyholder_name="Grace Liu",
    policy_number="MD-4418",
    date_of_loss="2026-09-14",
    loss_location="Seattle",
    loss_description="Urgent care visit for sinusitis, paid out of pocket.",
    estimated_loss_usd=640,
)
CLASSIFICATION = Classification(
    claim_type=ClaimType.MEDICAL_REIMBURSEMENT, severity=Severity.LOW, rationale="Reimbursement request."
)
TURNS = [
    Turn(id="t1", speaker="agent", text="What happened?"),
    Turn(id="t2", speaker="claimant", text="I went to urgent care on the 14th and paid 640 dollars myself."),
    Turn(id="t3", speaker="claimant", text="So I'll definitely get 340 dollars back, right?"),
    Turn(id="t4", speaker="agent", text="An adjuster will review that."),
]
CAPTURES = [
    EvidenceCapture(
        capture_id="c1", document_types=[DocumentType.MEDICAL_BILL], caption="Itemized bill", confirmed=True
    ),
    EvidenceCapture(
        capture_id="c2", document_types=[DocumentType.EXPLANATION_OF_BENEFITS], caption="EOB", confirmed=False
    ),
]

# The opening of a real sentence in medical/v1 §2.1, wrapped across lines as the file wraps it.
ANCHOR = "We reimburse eligible medical costs you paid yourself after your primary health plan"
# What the adjuster must end up seeing: the policy's own sentence, whole and unwrapped.
WHOLE_SENTENCE = (
    "We reimburse eligible medical costs you paid yourself after your primary health plan has paid "
    "its share, up to the annual maximum shown on your declarations page."
)


def draft(**overrides) -> ReviewDraft:
    base = dict(
        summary="Grace Liu asks to be reimbursed $640 she paid for an urgent care visit on 2026-09-14.",
        clauses=[DraftClause(section="2.1", anchor=ANCHOR, why_it_matters="Shows the order of payment to check.")],
        points_to_check=["How much of the $300 annual deductible is already used this year."],
        questions=[DraftQuestion(turn_id="t3", anchor="So I'll definitely get 340 dollars back")],
    )
    return ReviewDraft(**(base | overrides))


def check(d: ReviewDraft, *, wording=MEDICAL, turns=TURNS, policy=POLICY, received=1) -> PolicyReview:
    return verify(
        d,
        wording=wording,
        turns=turns,
        facts=FACTS,
        classification=CLASSIFICATION,
        policy=policy,
        received_documents=received,
    )


def reasons(review: PolicyReview) -> set[str]:
    return {d.reason for d in review.dropped}


# --- normalization (spec section 9.4) ----------------------------------------


def test_normalization_bridges_line_wraps_and_typography():
    assert normalize("We  reimburse\neligible costs") == "we reimburse eligible costs"
    assert normalize("the insurer’s “share”") == normalize("the insurer's \"share\"")
    assert normalize("a – b — c") == "a - b - c"
    assert normalize("and so on…") == "and so on..."
    assert normalize(None) == ""


def test_an_anchor_wrapped_across_lines_still_finds_its_sentence():
    review = check(draft())
    assert [c.section for c in review.clauses] == ["2.1"]
    assert review.clauses[0].heading == "What we pay"
    assert review.dropped == []


def test_the_quote_is_read_out_of_the_policy_not_taken_from_the_model():
    """The point of framing C: the adjuster reads the file's words, whole and unwrapped."""
    review = check(draft())
    assert review.clauses[0].quote == WHOLE_SENTENCE
    assert "\n" not in review.clauses[0].quote
    assert review.clauses[0].anchor == ANCHOR  # what the model pointed with, kept for debugging


def test_a_near_miss_anchor_still_yields_the_true_sentence():
    """Under framing A this clause was lost to a one-word slip; here it survives, quoted correctly.

    The model writes "plan pays its share" where the policy says "plan has paid its share".
    """
    sloppy = "We reimburse eligible medical costs you paid yourself"
    review = check(draft(clauses=[DraftClause(section="2.1", anchor=sloppy, why_it_matters="Order of payment.")]))
    assert review.clauses[0].quote == WHOLE_SENTENCE
    assert review.dropped == []


def test_an_anchor_pointing_mid_clause_quotes_that_sentence_not_the_first():
    review = check(draft(clauses=[
        DraftClause(section="2.1", anchor="We do not pay providers directly", why_it_matters="How payment is made."),
    ]))
    assert review.clauses[0].quote.startswith("We do not pay providers directly")
    assert "annual maximum" not in review.clauses[0].quote


# --- invented and mismatched clauses -----------------------------------------


def test_an_invented_clause_never_reaches_the_adjuster():
    review = check(draft(clauses=[
        DraftClause(section="9.1", anchor="Everything you ask for is covered in full.", why_it_matters="Claimant said so."),
    ]))
    assert review.clauses == []
    assert reasons(review) == {"unknown_section"}


def test_invented_words_cannot_be_displayed_even_for_a_real_section():
    review = check(draft(clauses=[
        DraftClause(section="2.1", anchor="We pay every cost without any deductible at all.", why_it_matters="."),
    ]))
    assert review.clauses == []
    assert reasons(review) == {"anchor_not_in_section"}


def test_an_anchor_from_a_different_clause_is_dropped():
    """The text exists in the wording, but not in the clause the model cited."""
    other = MEDICAL.clause("3.1").text[:60]
    review = check(draft(clauses=[DraftClause(section="2.1", anchor=other, why_it_matters=".")]))
    assert reasons(review) == {"anchor_not_in_section"}


def test_an_anchor_too_vague_to_point_at_one_sentence_is_dropped():
    review = check(draft(clauses=[DraftClause(section="2.1", anchor="we pay", why_it_matters=".")]))
    assert reasons(review) == {"anchor_too_short"}


def test_clauses_are_capped():
    review = check(draft(clauses=[DraftClause(section="2.1", anchor=ANCHOR, why_it_matters=".")] * 9))
    assert len(review.clauses) == 6
    assert reasons(review) == {"over_limit"}


def test_without_a_wording_every_clause_is_dropped_but_questions_survive():
    """Spec section 4.5: an unverifiable policy still shows what the claimant asked."""
    review = check(draft(), wording=None, policy=lookup_policy("ZZ-9999"))
    assert review.clauses == []
    assert reasons(review) == {"no_policy_wording"}
    assert [q.quote for q in review.questions] == ["So I'll definitely get 340 dollars back, right?"]
    assert review.policy_status == "not found"
    assert not review.has_wording


# --- claimant questions ------------------------------------------------------


def test_a_question_must_be_in_the_turn_it_cites():
    review = check(draft(questions=[
        DraftQuestion(turn_id="t2", anchor="So I'll definitely get 340 dollars back"),  # asked in t3
    ]))
    assert review.questions == []
    assert reasons(review) == {"anchor_not_in_turn"}


def test_the_question_is_read_out_of_the_transcript():
    """A tidied-up question still reaches the adjuster in the claimant's own words."""
    review = check(draft(questions=[DraftQuestion(turn_id="t3", anchor="So I'll definitely get 340")]))
    assert [q.quote for q in review.questions] == ["So I'll definitely get 340 dollars back, right?"]


def test_a_question_attributed_to_the_agent_or_a_missing_turn_is_dropped():
    review = check(draft(questions=[
        DraftQuestion(turn_id="t4", anchor="An adjuster will review that."),
        DraftQuestion(turn_id="t99", anchor="Anything at all."),
    ]))
    assert review.questions == []
    assert reasons(review) == {"not_a_claimant_turn", "unknown_turn"}


def test_a_short_question_survives_because_it_is_matched_against_one_turn():
    """"Am I covered?" is 13 characters and is exactly what an adjuster needs to see."""
    turns = TURNS + [Turn(id="t5", speaker="claimant", text="Am I covered?")]
    review = check(draft(questions=[DraftQuestion(turn_id="t5", anchor="Am I covered?")]), turns=turns)
    assert [q.quote for q in review.questions] == ["Am I covered?"]


# --- no verdicts (D14b, FR-10.5) ---------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "This loss is covered under the policy.",
        "The claim is not covered because of the exclusion.",
        "We will pay $340 to the claimant.",
        "The claim should be approved.",
        "This falls within coverage.",
        "The payout is $340.",
        "It does not qualify for reimbursement.",
        "I recommend denying this claim.",
    ],
)
def test_verdict_language_is_recognized(text):
    assert has_verdict_language(text)


@pytest.mark.parametrize(
    "text",
    [
        "Grace Liu asks to be reimbursed $640 she paid for an urgent care visit.",
        "Check how much of the $300 annual deductible is already used this year.",
        "The declarations page shows a $300 annual deductible.",
        "The EOB shows the primary plan paid $180 first.",
        "Three documents were received and verified.",
        # "denied" and "covered" are ordinary words about the primary plan in a medical claim,
        # and these are exactly the checks an adjuster wants (spec section 3 shows one of them).
        "Check whether the injection was denied by the primary health plan.",
        "Confirm the visit was covered by the primary plan before this policy pays.",
        "That the ceftriaxone injection is not a service the primary plan denied (4.2).",
    ],
)
def test_ordinary_review_language_is_not_mistaken_for_a_verdict(text):
    assert not has_verdict_language(text)


def test_another_payer_in_one_sentence_does_not_excuse_a_verdict_in_the_next():
    assert has_verdict_language("The primary plan paid $180. We will pay the remaining $340.")
    assert has_verdict_language("The primary health plan denied part of it, so the claim is covered here.")


def test_a_summary_that_decides_the_claim_is_replaced_by_one_built_from_facts():
    review = check(draft(summary="Grace paid $640 and this is covered, so we will pay $340."))
    assert review.summary_replaced
    assert not has_verdict_language(review.summary)
    assert "Grace Liu" in review.summary and "2026-09-14" in review.summary
    assert "MD-4418 is active" in review.summary
    assert "$640" in review.summary and "1 document received and verified" in review.summary
    assert reasons(review) == {"verdict_language"}


def test_an_empty_summary_is_replaced_too():
    review = check(draft(summary="   "))
    assert review.summary_replaced and review.summary
    assert reasons(review) == {"empty"}


def test_a_clause_reason_that_decides_the_claim_costs_the_reason_not_the_clause():
    """The policy text is the valuable part and is quoted from the file; only the model's line goes.

    Dropping the whole clause was the first design, and the first real eval run showed what it
    costs: the model cited the two clauses that mattered most and both were thrown away.
    """
    review = check(draft(clauses=[
        DraftClause(section="2.1", anchor=ANCHOR, why_it_matters="So the $340 balance is covered and payable."),
    ]))
    assert [c.section for c in review.clauses] == ["2.1"]
    assert review.clauses[0].quote == WHOLE_SENTENCE  # the adjuster still reads the policy
    assert "stated a conclusion" in review.clauses[0].why_it_matters
    assert not has_verdict_language(review.clauses[0].why_it_matters)
    assert reasons(review) == {"verdict_language"}


@pytest.mark.parametrize(
    "why",
    [
        # Both of these are real rationales from the first eval run against gpt-oss-120b.
        "Shows that the loss may be covered under the water backup endorsement, so verify the limit.",
        "Ensures the loss is not excluded as flood or surface water; confirm the water came from the sump.",
        "Requires an itemized bill; confirm all three documents were received.",
        "Sets out the annual deductible that applies before anything is reimbursed.",
    ],
)
def test_a_rationale_that_explains_a_clause_survives(why):
    """A hedge, or the clause as the subject, is the model deferring to the adjuster, not deciding."""
    review = check(draft(clauses=[DraftClause(section="2.1", anchor=ANCHOR, why_it_matters=why)]))
    assert review.clauses[0].why_it_matters == why
    assert review.dropped == []


def test_a_point_to_check_that_decides_the_claim_is_dropped():
    review = check(draft(points_to_check=["Confirm the deductible.", "The claim is covered.", "  "]))
    assert review.points_to_check == ["Confirm the deductible."]
    assert reasons(review) == {"verdict_language", "empty"}


def test_the_draft_schema_has_no_field_for_a_verdict_or_an_amount():
    """D14b is structural: the model cannot return a decision even if it wants to."""
    fields = set(ReviewDraft.model_json_schema()["properties"])
    assert fields == {"summary", "clauses", "points_to_check", "questions"}
    clause_fields = set(DraftClause.model_json_schema()["properties"])
    assert clause_fields == {"section", "anchor", "why_it_matters"}


# --- the prompt --------------------------------------------------------------


def test_the_prompt_carries_the_whole_wording_and_the_declarations():
    prompt = review_prompt(
        wording=MEDICAL, policy=POLICY, facts=FACTS, classification=CLASSIFICATION, turns=TURNS, captures=CAPTURES
    )
    assert "medical/v1" in prompt
    for clause in MEDICAL.clauses:
        assert clause.heading in prompt
    assert "annual $300" in prompt  # the declarations deductible
    assert "[t3] CLAIMANT:" in prompt
    assert "verified: \"Itemized bill\"" in prompt
    assert "not verified: \"EOB\"" in prompt


def test_the_system_prompt_forbids_deciding_the_claim():
    system = review_system()
    assert "never decide" in system.lower() or "never say or imply" in system.lower()
    # The prompt must ask the model to point, not to quote: the quote comes from the file.
    assert "You do not write the quote" in system
    assert "copied exactly" in system


def test_an_unverifiable_policy_is_described_rather_than_invented():
    prompt = review_prompt(
        wording=None, policy=lookup_policy("ZZ-9999"), facts=FACTS, classification=CLASSIFICATION,
        turns=TURNS, captures=[],
    )
    assert "could not be verified" in prompt
    assert "no policy wording" in prompt


# --- the service end to end (scripted model) ---------------------------------


class ScriptedLLM:
    def __init__(self, value: ReviewDraft):
        self.value, self.prompts, self.systems = value, [], []

    async def generate(self, *, step, system, prompt, schema, image=None):
        assert schema is ReviewDraft
        self.prompts.append(prompt)
        self.systems.append(system)
        return LLMResult[schema](
            value=self.value,
            call=LLMCall(step=step, model="fake-120b", attempts=1, latency_ms=12, input_tokens=4800, output_tokens=310),
        )


async def test_generate_records_what_it_used():
    llm = ScriptedLLM(draft())
    review = await generate_policy_review(
        llm, policy=POLICY, facts=FACTS, classification=CLASSIFICATION, turns=TURNS,
        captures=CAPTURES, pipeline_revision=7,
    )
    assert review.status == "ready"
    assert review.wording_ref == "medical/v1"
    assert review.policy_number == "MD-4418" and review.policy_status == "active"
    assert review.prompt_version == "v1"
    assert review.pipeline_revision == 7
    assert review.model == "fake-120b" and review.tokens == (4800, 310)
    assert [c.label for c in review.clauses] == ["§2.1 What we pay"]
    assert review.generated_at is not None


async def test_generate_survives_a_draft_that_is_entirely_invented():
    llm = ScriptedLLM(ReviewDraft(
        summary="This claim is covered and we will pay it in full.",
        clauses=[DraftClause(section="12.9", anchor="Everything is covered by this policy.", why_it_matters="The claimant said so.")],
        points_to_check=["The claim is payable."],
        questions=[DraftQuestion(turn_id="t99", anchor="Made up entirely.")],
    ))
    review = await generate_policy_review(
        llm, policy=POLICY, facts=FACTS, classification=CLASSIFICATION, turns=TURNS, captures=CAPTURES
    )
    assert review.clauses == [] and review.questions == [] and review.points_to_check == []
    assert review.summary_replaced and not has_verdict_language(review.summary)
    assert len(review.dropped) == 4


def test_the_product_key_selects_the_wording():
    assert wording_for_policy(lookup_policy("MD-4418")).ref == "medical/v1"
    assert wording_for_policy(lookup_policy("HO-20417")).ref == "homeowners/v1"
    assert wording_for_policy(lookup_policy("AU-72214")).ref == "auto/v1"
    assert wording_for_policy(lookup_policy("ZZ-9999")) is None


def test_a_lapsed_policy_still_gets_its_wording():
    """Spec section 4.5: the review runs and says the policy is lapsed."""
    lapsed = lookup_policy("AU-10001")
    assert wording_for_policy(lapsed).ref == "auto/v1"
    review = verify(
        ReviewDraft(summary="Tom Fischer reported a collision."),
        wording=wording_for(Product.AUTO), turns=TURNS, facts=FACTS,
        classification=CLASSIFICATION, policy=lapsed,
    )
    assert review.policy_status == "lapsed"
    assert review.has_wording


# --- locating a sentence (framing C's one moving part) ------------------------


def test_sentences_are_unwrapped_not_split_on_the_file_margin():
    """A line break is the file's margin, not punctuation: it must not end a sentence."""
    assert sentences_of("The quick brown\nfox jumps over it. Next one.") == [
        "The quick brown fox jumps over it.",
        "Next one.",
    ]
    assert sentences_of("Semicolons end a clause; so do questions? Yes.") == [
        "Semicolons end a clause;",
        "so do questions?",
        "Yes.",
    ]


def test_locate_returns_the_source_text_not_the_anchor():
    source = "Alpha beta gamma. Delta epsilon zeta."
    assert locate("beta gamma", source) == "Alpha beta gamma."
    assert locate("BETA   gamma", source) == "Alpha beta gamma."  # normalized comparison
    assert locate("nothing like this", source) is None
    assert locate("", source) is None


def test_locate_prefers_one_sentence_but_will_span_two():
    source = "Alpha beta. Gamma delta. Epsilon zeta."
    assert locate("beta", source) == "Alpha beta."
    assert locate("beta. Gamma", source) == "Alpha beta. Gamma delta."
    assert locate("beta. Gamma delta. Epsilon", source) is None  # three sentences is too far


def test_every_clause_in_every_wording_can_be_pointed_at():
    """A model that copies the opening of any real sentence always gets that sentence back."""
    from app.domain.wordings import load_wordings

    checked = 0
    for wording in load_wordings().values():
        for clause in wording.clauses:
            for sentence in sentences_of(clause.text):
                anchor = sentence[:45]
                found = locate(anchor, clause.text)
                assert found is not None, f"{wording.ref} {clause.number}: {anchor!r}"
                assert normalize(anchor) in normalize(found)
                checked += 1
    assert checked > 200
