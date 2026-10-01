"""Generating one policy review: prompt, one model call, then the checks.

This is deliberately not part of the per-turn pipeline (D14e). It runs once a claim is submitted,
and again when an adjuster asks for it, so it costs one call per claim rather than one per turn and
the live conversation never waits for it.
"""

from __future__ import annotations

import logging

from app.domain.models import ClaimFacts, Classification, EvidenceCapture, PolicyLookup
from app.domain.wordings import PolicyWording, wording_for
from app.llm.client import StructuredLLM
from app.pipeline.prompts import Turn
from app.review.checks import verify
from app.review.models import PolicyReview, ReviewDraft
from app.review.prompts import DEFAULT_PROMPT_VERSION, review_prompt, review_system

logger = logging.getLogger(__name__)

REVIEW_STEP = "wording_review"  # distinct from the policy_review route (POLICY-001)


def wording_for_policy(policy: PolicyLookup) -> PolicyWording | None:
    """The wording this claim is reviewed against, or None when the policy could not be verified.

    A lapsed or cancelled policy still has a wording: the review runs and says so (spec section 4.5).
    """
    if not policy.found or policy.record is None:
        return None
    return wording_for(policy.record.product)


async def generate_policy_review(
    llm: StructuredLLM,
    *,
    policy: PolicyLookup,
    facts: ClaimFacts,
    classification: Classification,
    turns: list[Turn],
    captures: list[EvidenceCapture] | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    pipeline_revision: int | None = None,
) -> PolicyReview:
    """One review. Raises LLMError if every model in the chain fails; the caller records that."""
    captures = list(captures or [])
    wording = wording_for_policy(policy)
    result = await llm.generate(
        step=REVIEW_STEP,
        system=review_system(prompt_version),
        prompt=review_prompt(
            wording=wording,
            policy=policy,
            facts=facts,
            classification=classification,
            turns=turns,
            captures=captures,
        ),
        schema=ReviewDraft,
    )
    review = verify(
        result.value,
        wording=wording,
        turns=turns,
        facts=facts,
        classification=classification,
        policy=policy,
        received_documents=sum(1 for c in captures if c.confirmed),
    )
    review.prompt_version = prompt_version
    review.pipeline_revision = pipeline_revision
    review.llm_call = result.call
    if review.dropped:
        logger.info(
            "policy review dropped %d item(s): %s",
            len(review.dropped),
            ", ".join(f"{d.kind}/{d.reason}" for d in review.dropped),
        )
    return review
