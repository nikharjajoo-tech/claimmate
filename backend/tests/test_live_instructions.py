"""The live agent's instructions (M8 step 6, decision D14d).

The eval harness scores the pipeline over text transcripts and never exercises this prompt, so
these assertions are the only automated cover it has. They check the text says what the decisions
require; whether the model follows it is the manual live check in docs/07-policy-review.md.
"""

from app.live.tools import SYSTEM_INSTRUCTION, build_live_config


def test_the_agent_still_refuses_to_decide_coverage():
    """D14d adds a sentence next to this rule and must never soften it."""
    assert "Never promise or imply coverage, payment, approval, liability, or amounts" in SYSTEM_INSTRUCTION
    assert "a licensed adjuster decides after reviewing the claim" in SYSTEM_INSTRUCTION


def test_the_agent_may_say_the_question_was_noted_for_the_adjuster():
    assert "noted their question for that adjuster" in SYSTEM_INSTRUCTION


def test_noting_a_question_is_not_licence_to_answer_it():
    """Without this, "I've noted that for your adjuster" becomes a preamble to a guess."""
    assert "Noting a question is not answering it" in SYSTEM_INSTRUCTION
    assert "never add your own view of the outcome" in SYSTEM_INSTRUCTION


def test_the_instructions_never_mention_the_wording_review():
    """The review is adjuster-only (D14a). The claimant's agent must not know it exists, or it
    will start describing clauses on the call."""
    lowered = SYSTEM_INSTRUCTION.lower()
    for forbidden in ("wording review", "policy wording", "clause", "exclusion"):
        assert forbidden not in lowered, f"the live agent prompt mentions {forbidden!r}"


def test_the_instruction_reaches_the_live_session():
    config = build_live_config()
    assert "noted their question for that adjuster" in config.system_instruction
