import { useState } from "react";
import { adjusterApi, HttpError, type WordingReviewState } from "../lib/adjuster";

interface Props {
  claimId: string;
  state: WordingReviewState;
  /** Highlights the transcript turn a question was asked in; null clears it. */
  onShowTurn: (turnId: string | null) => void;
  activeTurn: string | null;
  onUnauthorized: (e: unknown) => void;
}

const WAITING: Partial<Record<WordingReviewState["status"], string>> = {
  none: "No wording review yet. One is prepared when the claim is submitted.",
  running: "Reading the policy wording…",
  pending: "The last attempt did not finish. Opening the claim starts it again.",
  disabled: "Wording review is not configured on this server.",
};

export function WordingReviewPanel({ claimId, state: initial, onShowTurn, activeTurn, onUnauthorized }: Props) {
  const [state, setState] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      setState(await adjusterApi.refreshWordingReview(claimId));
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Could not start a new review.");
      onUnauthorized(e);
    } finally {
      setBusy(false);
    }
  };

  const { review } = state;
  return (
    <section className="wording-review" aria-label="Wording review">
      <header>
        <h3>Wording review</h3>
        <span className="caveat" title="Every clause below is quoted from the policy file, but whether it applies is your call.">
          AI-assisted · verify against the policy
        </span>
        {state.status !== "disabled" && (
          <button className="btn btn-small" disabled={busy} onClick={refresh}>
            {busy ? "Starting…" : state.status === "ready" ? "Refresh" : "Retry"}
          </button>
        )}
      </header>

      {error && (
        <p className="banner banner-warn" role="alert">
          {error}
        </p>
      )}
      {state.status === "failed" && (
        <p className="banner banner-warn">Could not prepare a review: {state.error || "the model was unavailable"}.</p>
      )}
      {!review && WAITING[state.status] && <p className="muted">{WAITING[state.status]}</p>}

      {review && (
        <>
          <p className="muted source-line">
            {review.wording_ref ? `Wording ${review.wording_ref}` : "No wording: the policy could not be verified"}
            {review.policy_number && ` · policy ${review.policy_number} (${review.policy_status})`}
          </p>

          <h4>Summary</h4>
          <p>{review.summary}</p>
          {review.summary_replaced && (
            <p className="muted small">
              The model's own summary stated a coverage conclusion, so this one was built from the claim facts instead.
            </p>
          )}

          <h4>Relevant clauses</h4>
          {review.clauses.length === 0 ? (
            <p className="muted">
              {review.wording_ref ? "Nothing in the wording was matched to this claim." : "No policy wording was read."}
            </p>
          ) : (
            <ul className="clauses">
              {review.clauses.map((c) => (
                <li key={c.section}>
                  <span className="clause-label">
                    §{c.section} {c.heading}
                  </span>
                  <blockquote>{c.quote}</blockquote>
                  <p className="why">{c.why_it_matters}</p>
                </li>
              ))}
            </ul>
          )}

          {review.points_to_check.length > 0 && (
            <>
              <h4>To check</h4>
              <ul className="plain-list">
                {review.points_to_check.map((point, i) => (
                  <li key={i}>{point}</li>
                ))}
              </ul>
            </>
          )}

          {review.questions.length > 0 && (
            <>
              <h4>The claimant asked</h4>
              <p className="muted hint">Select a question to highlight where it was asked.</p>
              <ul className="questions">
                {review.questions.map((q) => (
                  <li
                    key={`${q.turn_id}-${q.quote}`}
                    className={activeTurn === q.turn_id ? "active" : ""}
                    role="button"
                    tabIndex={0}
                    onClick={() => onShowTurn(activeTurn === q.turn_id ? null : q.turn_id)}
                    onKeyDown={(e) => e.key === "Enter" && onShowTurn(activeTurn === q.turn_id ? null : q.turn_id)}
                  >
                    “{q.quote}” <span className="muted small">{q.turn_id}</span>
                  </li>
                ))}
              </ul>
            </>
          )}

          {review.dropped.length > 0 && (
            <p className="muted small">
              {review.dropped.length} item{review.dropped.length === 1 ? "" : "s"} the model produced could not be
              verified against the policy and {review.dropped.length === 1 ? "was" : "were"} left out.
            </p>
          )}
        </>
      )}
    </section>
  );
}
