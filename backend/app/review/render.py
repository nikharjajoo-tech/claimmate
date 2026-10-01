"""The wording review as Markdown, for the adjuster's packet (FR-10.6).

Adjuster-only. The caller passes the review in; nothing here reaches for it, so the claimant's own
packet cannot acquire one by accident.
"""

from __future__ import annotations

from app.review.models import PolicyReview

CAVEAT = "AI-assisted. Verify every clause against the policy. This is not a coverage decision."


def review_markdown(review: PolicyReview) -> str:
    lines = ["# Wording review", "", f"_{CAVEAT}_", ""]
    where = review.wording_ref or "no policy wording"
    lines.append(f"Policy {review.policy_number or 'unknown'} ({review.policy_status}) · wording {where}")
    if review.model:
        lines.append(f"Generated {review.generated_at:%Y-%m-%d %H:%M UTC} by {review.model}, prompt {review.prompt_version}")
    lines += ["", "## Summary", "", review.summary or "(none)"]
    if review.summary_replaced:
        lines += ["", "_The model's summary stated a coverage conclusion and was replaced by this one, built from the claim facts._"]

    lines += ["", "## Relevant clauses", ""]
    if review.clauses:
        for clause in review.clauses:
            lines += [
                f"### {clause.label}",
                f"_{clause.part}_",
                "",
                f"> {clause.quote}",
                "",
                clause.why_it_matters,
                "",
            ]
    else:
        lines += ["(none found)" if review.has_wording else "(the policy could not be verified, so no wording was read)", ""]

    lines += ["## To check", ""]
    lines += [f"- {point}" for point in review.points_to_check] or ["(none)"]

    lines += ["", "## The claimant asked", ""]
    lines += [f'- "{q.quote}" ({q.turn_id})' for q in review.questions] or ["(nothing recorded)"]

    if review.dropped:
        lines += ["", "## Dropped by the checks", "", "Items the model produced that could not be verified:", ""]
        lines += [f"- {d.kind}: {d.reason.replace('_', ' ')}{f' ({d.detail})' if d.detail else ''}" for d in review.dropped]
    return "\n".join(lines) + "\n"
