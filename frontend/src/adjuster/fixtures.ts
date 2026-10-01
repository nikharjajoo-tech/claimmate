import type { WordingReview } from "../lib/adjuster";

/** A ready wording review, as the adjuster API returns it. Shared by the adjuster tests. */
export const REVIEW: WordingReview = {
  wording_ref: "homeowners/v1",
  policy_number: "HO-20417",
  policy_status: "active",
  summary: "Elena Brooks reports a basement flood on 2026-09-23.",
  summary_replaced: false,
  clauses: [
    {
      section: "2.5",
      heading: "Water backup and sump overflow endorsement",
      part: "What we cover",
      quote:
        "This cover applies whether or not the backup or overflow was caused by a mechanical breakdown of the sump pump.",
      anchor: "This cover applies whether or not",
      why_it_matters: "The claimant describes a sump pump failure.",
    },
  ],
  points_to_check: ["Whether the water came from the sump or from outside (3.1)."],
  questions: [{ turn_id: "t1", quote: "I'm Elena Brooks." }],
  dropped: [],
  prompt_version: "v1",
  pipeline_revision: 2,
  generated_at: "2026-10-01T09:00:00Z",
};
