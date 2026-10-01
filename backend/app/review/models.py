"""What the model may return, and what an adjuster is allowed to see.

`ReviewDraft` is the structured-output schema. It has **no field for a coverage verdict or an
amount payable** (decision D14b): the model cannot express one even if asked to.

`PolicyReview` is what survives the checks in `checks.py`, plus the record of what did not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.llm.client import LLMCall

# Caps applied after parsing, so an over-eager draft cannot flood the panel.
MAX_CLAUSES = 6
MAX_POINTS = 5
MAX_QUESTIONS = 8


# --- what the model returns --------------------------------------------------


class DraftClause(BaseModel):
    section: str = Field(description='The clause number exactly as the wording prints it, e.g. "2.1".')
    anchor: str = Field(
        description="The opening words of the sentence in that clause you mean, copied exactly, "
        "about ten words. The system reads the sentence out of the policy itself; you are pointing "
        "at it, not quoting it."
    )
    why_it_matters: str = Field(
        description="One sentence on why this clause bears on this claim. State what to compare, "
        "never whether the claim is covered or what will be paid."
    )


class DraftQuestion(BaseModel):
    turn_id: str = Field(description='The claimant turn the question was asked in, e.g. "t7".')
    anchor: str = Field(
        description="The opening words of the question as the claimant said it, copied exactly. "
        "The system reads the rest out of the transcript."
    )


class ReviewDraft(BaseModel):
    """The model's draft. Every field is checked by code before an adjuster sees it."""

    summary: str = Field(
        description="Two or three sentences: who is claiming, what happened, when, the amount they "
        "state, and which documents were received. No coverage or payment conclusion."
    )
    clauses: list[DraftClause] = Field(default_factory=list, description="The clauses an adjuster should read.")
    points_to_check: list[str] = Field(
        default_factory=list,
        description="What the adjuster should verify, phrased as a check, not a conclusion.",
    )
    questions: list[DraftQuestion] = Field(
        default_factory=list, description="Questions the claimant asked, in their own words."
    )


# --- what the adjuster sees --------------------------------------------------


class ReviewClause(BaseModel):
    """A cited clause. `quote` is read out of the wording file, never written by the model."""

    section: str
    heading: str
    part: str
    quote: str  # the policy's own sentence(s)
    anchor: str  # what the model pointed with, kept for debugging and the eval
    why_it_matters: str

    @property
    def label(self) -> str:
        return f"§{self.section} {self.heading}"


class ReviewQuestion(BaseModel):
    """A claimant question. `quote` is read out of the transcript, never written by the model."""

    turn_id: str
    quote: str
    anchor: str = ""


class DroppedItem(BaseModel):
    """Something the checks removed. Kept so the panel and the eval can say what was dropped."""

    kind: Literal["clause", "question", "point", "summary"]
    reason: Literal[
        "unknown_section",
        "anchor_not_in_section",
        "anchor_too_short",
        "unknown_turn",
        "not_a_claimant_turn",
        "anchor_not_in_turn",
        "verdict_language",
        "no_policy_wording",
        "over_limit",
        "empty",
    ]
    detail: str = ""


class PolicyReview(BaseModel):
    status: Literal["ready", "failed"] = "ready"
    wording_ref: str = ""  # "medical/v1"; empty when the policy could not be verified
    policy_number: str = ""
    policy_status: str = ""  # active | lapsed | cancelled | not found
    summary: str = ""
    summary_replaced: bool = False  # True when code replaced verdict language in the model's summary
    clauses: list[ReviewClause] = Field(default_factory=list)
    points_to_check: list[str] = Field(default_factory=list)
    questions: list[ReviewQuestion] = Field(default_factory=list)
    dropped: list[DroppedItem] = Field(default_factory=list)
    prompt_version: str = "v1"
    pipeline_revision: int | None = None  # the pipeline result this review read, None if there was none
    llm_call: LLMCall | None = None  # model, attempts, latency, tokens: recorded like a pipeline run
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str = ""

    @property
    def model(self) -> str:
        return self.llm_call.model if self.llm_call else ""

    @property
    def tokens(self) -> tuple[int, int]:
        return (self.llm_call.input_tokens, self.llm_call.output_tokens) if self.llm_call else (0, 0)

    @property
    def has_wording(self) -> bool:
        return bool(self.wording_ref)
