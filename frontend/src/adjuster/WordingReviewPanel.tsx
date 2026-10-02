import { useEffect, useRef, useState } from "react";
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
  pending: "The last attempt did not finish. Opening the claim starts it again.",
  disabled: "Wording review is not configured on this server.",
};

/** How often to ask whether a running generation has finished, and when to stop asking.
    A review is one model call, so this settles in seconds; the cap is for a server that died. */
const POLL_MS = 2000;
const POLL_LIMIT_MS = 90_000;

export function WordingReviewPanel({ claimId, state: initial, onShowTurn, activeTurn, onUnauthorized }: Props) {
  const [state, setState] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const startedPolling = useRef(0);
  // Drives the poll loop. Re-arming on the state object alone would stop the moment a response
  // came back reference-identical, because React would skip the re-render.
  const [poll, setPoll] = useState(0);

  // A generation runs in the background, so the panel follows it rather than freezing on the
  // review it was rendered with. Without this, pressing Refresh shows the previous review for
  // ever and the one the adjuster asked for only appears if they reload the page.
  useEffect(() => {
    if (!state.running) {
      startedPolling.current = 0;
      return;
    }
    if (!startedPolling.current) startedPolling.current = Date.now();
    if (Date.now() - startedPolling.current > POLL_LIMIT_MS) return;

    const timer = setTimeout(async () => {
      try {
        setState(await adjusterApi.wordingReview(claimId));
      } catch {
        // A failed poll is not worth reporting: the next one tries again.
      }
      setPoll((n) => n + 1);
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [claimId, state, poll]);

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
          <button className="btn btn-small" disabled={busy || state.running} onClick={refresh}>
            {state.running ? "Reading…" : busy ? "Starting…" : state.status === "ready" ? "Refresh" : "Retry"}
          </button>
        )}
      </header>

      {error && (
        <p className="banner banner-warn" role="alert">
          {error}
        </p>
      )}
      {state.status === "failed" && !state.running && (
        <p className="banner banner-warn">Could not prepare a review: {state.error || "the model was unavailable"}.</p>
      )}
      {state.running && (
        <p className="muted" role="status">
          {review ? "Reading the policy wording again…" : "Reading the policy wording…"}
        </p>
      )}
      {!review && !state.running && WAITING[state.status] && <p className="muted">{WAITING[state.status]}</p>}

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
