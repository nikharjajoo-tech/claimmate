"""The code half of the policy review (FR-10.4, FR-10.5, spec section 9.4).

The model never writes a quote. It points: a section number and the opening words of the sentence
it means. Code then reads that sentence out of the wording file and shows *that*. So a quote an
adjuster reads cannot be invented, in the same way the LLM cannot mark evidence as received
(PRD 7.2): the capability is absent rather than audited.

Pointing can still miss. An anchor that matches nothing in the cited clause is dropped and
recorded; an anchor too vague to pick a sentence is dropped before it can pick a misleading one.
What it can never do is put words into the policy's mouth.

Matching is done on normalized text, because a wording wraps its lines wherever the sentence
reaches the margin and models rewrite whitespace, curly quotes and dashes freely.
"""

from __future__ import annotations

import re

from app.domain.models import ClaimFacts, Classification, PolicyLookup, is_blank
from app.pipeline.prompts import Turn
from app.review.models import (
    MAX_CLAUSES,
    MAX_POINTS,
    MAX_QUESTIONS,
    DroppedItem,
    PolicyReview,
    ReviewClause,
    ReviewDraft,
    ReviewQuestion,
)
from app.domain.wordings import PolicyWording

# How precisely the model must point. This is a precision aid, not a safety control: whatever is
# shown comes out of the source either way, so a vague anchor costs relevance, never truth.
# Measured on the five wordings: an anchor is searched inside one clause (280 chars median, not the
# 33,000-char document), and at 20 characters only 0.38% of sentence openings are ambiguous within
# their own clause. A claimant turn is shorter still, and real questions are short ("am I covered?").
REPLACED_REASON = "Cited as relevant; the model's explanation stated a conclusion and was removed."
MIN_CLAUSE_ANCHOR_CHARS = 20
MIN_QUESTION_ANCHOR_CHARS = 8
# An anchor may run past a sentence end; allow it to pull the following sentence with it.
MAX_QUOTE_SENTENCES = 2

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})

# Verdict language. These decide the claim, which is the adjuster's job, not ClaimMate's (D14b).
# Always a verdict, whoever is being talked about.
_VERDICT_ALWAYS = re.compile(
    r"\b("
    r"(we|the insurer|this policy|the policy)\s+(will|should|must|can)\s+(pay|reimburse|cover|approve|deny|decline)"
    r"|(coverage|the claim|this claim)\s+(applies|is valid|is invalid)"
    r"|(falls|comes)\s+(within|outside)\s+coverage"
    r"|(qualifies|does not qualify)\s+for\s+(cover|coverage|reimbursement|payment)"
    r"|(recommend|suggest)\s+(approving|denying|paying|declining)"
    r"|(payout|amount payable|settlement amount)"
    r")\b",
    re.IGNORECASE,
)
# A verdict about our decision, unless the sentence is reporting what another payer decided:
# "the injection was denied by the primary health plan" is a fact an adjuster needs, not a verdict.
_VERDICT_UNLESS_OTHER_PAYER = re.compile(
    r"\b(is|are|was|were|will be|would be|should be|may be|is not|are not)\s+"
    r"(covered|payable|reimbursed|approved|denied|declined|excluded|eligible for payment)\b",
    re.IGNORECASE,
)
# The excuse is deliberately tight: the other payer must be the agent of that very verb
# ("was denied by the primary health plan"). Merely naming a plan elsewhere in the sentence does
# not excuse a verdict, or "the plan denied part of it, so the claim is covered" would slip through.
_BY_OTHER_PAYER = re.compile(
    r"^\s*(by|under)\s+(the\s+|their\s+|her\s+|his\s+)?"
    r"(primary|carrier|another insurer|other insurer)",
    re.IGNORECASE,
)
# A hedge means the model is naming a possibility for the adjuster to resolve, not deciding:
# "the loss may be covered ... so verify the endorsement" is the single most useful line in a
# review, and the first real eval run showed the guard destroying exactly those clauses.
_HEDGED = re.compile(r"\b(may|might|could|appears?|seems?|likely|possibly|potentially|whether)\b", re.IGNORECASE)
# These rationales are written with the clause as the elliptical subject ("Ensures the loss is not
# excluded as flood ... confirm the water came from the sump"). That describes the clause's effect;
# it is not an assertion about the outcome. Only at the start of a sentence, so "the claim is
# covered, so confirm the deductible" is still caught.
_ABOUT_THE_CLAUSE = re.compile(
    r"^\s*(ensures?|shows?|sets out|states?|provides?|means|describes?|defines?|covers?|establishes|"
    r"requires?|limits?|excludes?|applies|governs?|determines?)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.;!?]+[.;!?]?")
_SENTENCE_END = re.compile(r"(?<=[.;!?])\s+")


def normalize(text: str) -> str:
    """Collapse whitespace, straighten quotes and dashes, casefold. Used only for comparison."""
    return " ".join(str(text or "").translate(_QUOTES).replace("…", "...").split()).casefold()


def sentences_of(text: str) -> list[str]:
    """The source's own sentences, unwrapped. Line breaks are the file's margin, not punctuation."""
    return [s.strip() for s in _SENTENCE_END.split(" ".join(str(text or "").split())) if s.strip()]


def locate(anchor: str, source: str, *, max_sentences: int = MAX_QUOTE_SENTENCES) -> str | None:
    """Read out of `source` the sentence the anchor points at, or None if it points at nothing.

    The return value is always the source's own text. The shortest match wins, so an anchor that
    sits inside one sentence quotes that sentence rather than dragging the next one along.
    """
    key = normalize(anchor)
    if not key:
        return None
    sentences = sentences_of(source)
    for span in range(1, max_sentences + 1):
        for start in range(len(sentences) - span + 1):
            window = " ".join(sentences[start : start + span])
            if key in normalize(window):
                return window
    return None


def has_verdict_language(text: str) -> bool:
    """True where the text decides the claim rather than describing it.

    Judged sentence by sentence, because one sentence reporting what the primary health plan
    decided must not excuse another that decides the claim itself.
    """
    cleaned = " ".join(str(text or "").translate(_QUOTES).split())
    for sentence in _SENTENCE.findall(cleaned):
        if _VERDICT_ALWAYS.search(sentence):
            return True
        if _HEDGED.search(sentence) or _ABOUT_THE_CLAUSE.match(sentence):
            continue
        for match in _VERDICT_UNLESS_OTHER_PAYER.finditer(sentence):
            if not _BY_OTHER_PAYER.match(sentence[match.end() :]):
                return True
    return False


def _code_summary(facts: ClaimFacts, classification: Classification, policy: PolicyLookup, received: int) -> str:
    """A plain summary built from facts alone, used when the model's summary decided the claim."""
    who = "The claimant" if is_blank(facts.policyholder_name) else facts.policyholder_name
    kind = classification.claim_type.value.replace("_", " ")
    when = "" if is_blank(facts.date_of_loss) else f" on {facts.date_of_loss}"
    where = "" if is_blank(facts.loss_location) else f" in {facts.loss_location}"
    amount = "" if facts.estimated_loss_usd is None else f" They state an amount of ${facts.estimated_loss_usd:,.0f}."
    policy_text = ""
    if policy.found and policy.record:
        policy_text = f" Policy {policy.record.policy_number} is {policy.record.status}."
    elif policy.query:
        policy_text = f" Policy {policy.query} could not be verified."
    documents = f" {received} document{'s' if received != 1 else ''} received and verified." if received else ""
    return f"{who} reported a {kind} claim{when}{where}.{policy_text}{amount}{documents}".strip()


def verify(
    draft: ReviewDraft,
    *,
    wording: PolicyWording | None,
    turns: list[Turn],
    facts: ClaimFacts,
    classification: Classification,
    policy: PolicyLookup,
    received_documents: int = 0,
) -> PolicyReview:
    """Turn a draft into what an adjuster may see, dropping everything that fails its check."""
    review = PolicyReview(
        wording_ref=wording.ref if wording else "",
        policy_number=policy.record.policy_number if policy.found and policy.record else policy.query,
        policy_status=policy.record.status if policy.found and policy.record else "not found",
    )
    dropped = review.dropped

    def drop(kind: str, reason: str, detail: str) -> None:
        dropped.append(DroppedItem(kind=kind, reason=reason, detail=detail[:300]))

    # --- clauses: the model points at a clause, code quotes the wording itself ---
    for draft_clause in draft.clauses:
        cited = f"§{draft_clause.section}"
        if wording is None:
            drop("clause", "no_policy_wording", cited)
            continue
        clause = wording.clause(draft_clause.section)
        if clause is None:
            drop("clause", "unknown_section", f"{cited} is not in {wording.ref}")
            continue
        if len(normalize(draft_clause.anchor)) < MIN_CLAUSE_ANCHOR_CHARS:
            drop("clause", "anchor_too_short", f"{cited}: {draft_clause.anchor}")
            continue
        quote = locate(draft_clause.anchor, clause.text)
        if quote is None:
            drop("clause", "anchor_not_in_section", f"{cited}: {draft_clause.anchor}")
            continue
        why = draft_clause.why_it_matters.strip()
        if has_verdict_language(why):
            # Losing the clause would cost the adjuster the policy text, which is the valuable
            # part and is quoted from the file either way. Only the model's line goes.
            drop("clause", "verdict_language", f"{cited}: {why}")
            why = REPLACED_REASON
        if len(review.clauses) >= MAX_CLAUSES:
            drop("clause", "over_limit", cited)
            continue
        review.clauses.append(
            ReviewClause(
                section=clause.number,
                heading=clause.heading,
                part=clause.part,
                quote=quote,
                anchor=draft_clause.anchor.strip(),
                why_it_matters=why,
            )
        )

    # --- questions: same inversion, with the transcript as the source of the words ---
    by_id = {t.id: t for t in turns}
    for draft_question in draft.questions:
        turn = by_id.get(draft_question.turn_id.strip())
        if turn is None:
            drop("question", "unknown_turn", f"{draft_question.turn_id}: {draft_question.anchor}")
            continue
        if turn.speaker != "claimant":
            drop("question", "not_a_claimant_turn", f"{turn.id} is an agent turn")
            continue
        if len(normalize(draft_question.anchor)) < MIN_QUESTION_ANCHOR_CHARS:
            drop("question", "anchor_too_short", f"{turn.id}: {draft_question.anchor}")
            continue
        quote = locate(draft_question.anchor, turn.text, max_sentences=1)
        if quote is None:
            drop("question", "anchor_not_in_turn", f"{turn.id}: {draft_question.anchor}")
            continue
        if len(review.questions) >= MAX_QUESTIONS:
            drop("question", "over_limit", turn.id)
            continue
        review.questions.append(
            ReviewQuestion(turn_id=turn.id, quote=quote, anchor=draft_question.anchor.strip())
        )

    # --- points to check: free text, so only the verdict guard applies ---
    for point in draft.points_to_check:
        text = point.strip()
        if not text:
            drop("point", "empty", "")
        elif has_verdict_language(text):
            drop("point", "verdict_language", text)
        elif len(review.points_to_check) >= MAX_POINTS:
            drop("point", "over_limit", text)
        else:
            review.points_to_check.append(text)

    # --- summary: replaced rather than dropped, so the panel is never blank ---
    summary = draft.summary.strip()
    if not summary or has_verdict_language(summary):
        drop("summary", "verdict_language" if summary else "empty", summary)
        review.summary = _code_summary(facts, classification, policy, received_documents)
        review.summary_replaced = True
    else:
        review.summary = summary
    return review
