import { useEffect, useState } from "react";
import { ROUTE_LABEL, title } from "../components/Notebook";
import { adjusterApi, type ClaimDetail, HttpError } from "../lib/adjuster";
import { WordingReviewPanel } from "./WordingReviewPanel";
import type { ClaimStatus, Route } from "../lib/types";

interface Props {
  claimId: string;
  onChanged: () => void;
  onUnauthorized: (e: unknown) => void;
}

const ACTIONS: Record<ClaimStatus, { status: ClaimStatus; label: string; primary?: boolean }[]> = {
  intake: [],
  submitted: [],
  in_review: [
    { status: "awaiting_docs", label: "Request documents" },
    { status: "closed", label: "Close claim", primary: true },
  ],
  awaiting_docs: [
    { status: "in_review", label: "Resume review" },
    { status: "closed", label: "Close claim", primary: true },
  ],
  closed: [],
};

export function ClaimDetailView({ claimId, onChanged, onUnauthorized }: Props) {
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [overriding, setOverriding] = useState(false);
  const [overrideRoute, setOverrideRoute] = useState<Route>("needs_docs");
  const [reason, setReason] = useState("");
  const [highlight, setHighlight] = useState<string | null>(null);
  // A question from the wording review highlights its turn directly, by turn id.
  const [questionTurn, setQuestionTurn] = useState<string | null>(null);

  useEffect(() => {
    adjusterApi.detail(claimId).then(setDetail, (e) => {
      setError(e instanceof HttpError ? e.message : "Could not load the claim.");
      onUnauthorized(e);
    });
  }, [claimId, onUnauthorized]);

  const act = async (run: () => Promise<ClaimDetail>) => {
    setBusy(true);
    setError("");
    try {
      setDetail(await run());
      setNote("");
      setOverriding(false);
      setReason("");
      onChanged();
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "The action failed. Try again.");
      onUnauthorized(e);
    } finally {
      setBusy(false);
    }
  };

  if (!detail) {
    return <section className="panel detail">{error ? <p className="banner banner-warn">{error}</p> : "Loading…"}</section>;
  }

  const { state, audit } = detail;
  const name = state.fields.find((f) => f.key === "policyholder_name")?.value ?? "Unknown claimant";
  const sourceTurns = new Set(highlight ? (state.fact_sources[highlight] ?? []) : []);
  if (questionTurn) sourceTurns.add(questionTurn);
  const reviewable = state.status === "in_review" || state.status === "awaiting_docs";

  return (
    <section className="panel detail" aria-label="Claim detail">
      <header className="detail-head">
        <div>
          <h2>{name}</h2>
          <p className="muted">
            Claim {state.id.slice(0, 8)} · {state.claim_type ? title(state.claim_type) : "Type pending"}
            {state.severity && ` · ${state.severity} severity`} · <span className={`pill pill-${state.status}`}>{state.status.replace("_", " ")}</span>
          </p>
        </div>
        {state.route && <span className={`stamp stamp-${state.route}`}>{ROUTE_LABEL[state.route]}</span>}
      </header>

      {(state.route_override || (state.route_frozen && state.pipeline_route !== state.route)) && (
        <p className="banner banner-warn">
          {state.route_override
            ? `Route overridden by adjuster: “${state.override_reason}”. `
            : "Route frozen for review. "}
          The automatic pipeline now says: {state.pipeline_route ? ROUTE_LABEL[state.pipeline_route] : "no route"}.
        </p>
      )}
      {error && (
        <p className="banner banner-warn" role="alert">
          {error}
        </p>
      )}

      <div className="action-bar">
        {state.status === "submitted" && (
          <button className="btn btn-primary" disabled={busy} onClick={() => act(() => adjusterApi.open(claimId))}>
            Start review
          </button>
        )}
        {state.status === "intake" && <p className="muted">The claimant is still reporting this claim. View only.</p>}
        {ACTIONS[state.status].length > 0 && (
          <input
            className="note-input"
            aria-label="Note for the audit trail"
            placeholder="Note for the audit trail (optional)"
            value={note}
            maxLength={1000}
            onChange={(e) => setNote(e.target.value)}
          />
        )}
        {ACTIONS[state.status].map((a) => (
          <button
            key={a.status}
            className={`btn${a.primary ? " btn-primary" : ""}`}
            disabled={busy}
            onClick={() => act(() => adjusterApi.setStatus(claimId, a.status, note))}
          >
            {a.label}
          </button>
        ))}
        {reviewable && !overriding && (
          <button className="btn" onClick={() => setOverriding(true)}>
            Override route
          </button>
        )}
        {state.packet_markdown && (
          <a className="btn" href={adjusterApi.packetUrl(claimId)} download>
            Download packet
          </a>
        )}
      </div>

      {overriding && (
        <form
          className="override"
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => adjusterApi.override(claimId, overrideRoute, reason));
          }}
        >
          <label htmlFor="override-route">New route</label>
          <select id="override-route" value={overrideRoute} onChange={(e) => setOverrideRoute(e.target.value as Route)}>
            {(Object.keys(ROUTE_LABEL) as Route[]).map((r) => (
              <option key={r} value={r}>
                {ROUTE_LABEL[r]}
              </option>
            ))}
          </select>
          <label htmlFor="override-reason">Reason (required, recorded in the audit trail)</label>
          <textarea id="override-reason" rows={2} value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
          <div className="row">
            <button className="btn btn-primary" type="submit" disabled={busy || reason.trim().length < 10}>
              Save override
            </button>
            <button className="btn" type="button" onClick={() => setOverriding(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="detail-grid">
        <div>
          <h3>Facts</h3>
          <p className="muted hint">Select a fact to highlight where the claimant said it.</p>
          <dl className="fields">
            {state.fields.map((f) => (
              <div
                key={f.key}
                className={`${f.value ? "filled" : "missing"}${highlight === f.key ? " active" : ""}`}
                role={state.fact_sources[f.key] ? "button" : undefined}
                tabIndex={state.fact_sources[f.key] ? 0 : undefined}
                onClick={() => {
                  setQuestionTurn(null);
                  setHighlight(highlight === f.key ? null : f.key);
                }}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  setQuestionTurn(null);
                  setHighlight(highlight === f.key ? null : f.key);
                }}
              >
                <dt>{f.label}</dt>
                <dd>{f.value ?? "Not provided"}</dd>
              </div>
            ))}
          </dl>

          {state.policy && (
            <div className={`policy ${state.policy.found && state.policy.status === "active" ? "ok" : "flag"}`}>
              {state.policy.found
                ? `${state.policy.number} · ${state.policy.holder} · ${state.policy.line} · ${state.policy.status}`
                : `Policy not verified. ${state.policy.message}`}
            </div>
          )}

          {state.safety.length > 0 && (
            <>
              <h3>Safety</h3>
              <ul className="plain-list">
                {state.safety.map((s, i) => (
                  <li key={i} className={s.status === "absent" ? "muted" : "danger-text"}>
                    {s.category} ({s.status}): {s.description}
                  </li>
                ))}
              </ul>
            </>
          )}

          <h3>Documents</h3>
          <ul className="checklist">
            {state.checklist.map((item) => (
              <li key={item.label} className={item.satisfied ? "done" : ""}>
                <span className="box" aria-hidden>
                  {item.satisfied ? "✓" : ""}
                </span>
                {item.label}
                <span className={`chip chip-${item.status}`}>{item.status}</span>
              </li>
            ))}
          </ul>

          {state.evidence.length > 0 && (
            <>
              <h3>Evidence ({state.evidence.length})</h3>
              <ul className="gallery">
                {state.evidence.map((e) => (
                  <li key={e.capture_id}>
                    <img src={e.url} alt={e.caption} loading="lazy" />
                    <p>{e.caption}</p>
                    {e.claimant_claim ? (
                      <span className={`badge ${e.confirmed ? "badge-ok" : "badge-warn"}`}>
                        {e.confirmed ? `Confirms “${e.claimant_claim}”` : `Does not confirm “${e.claimant_claim}”`}
                      </span>
                    ) : (
                      <span className="badge">No claimant description</span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}

          <h3>Rules fired ({state.findings.length})</h3>
          <ul className="plain-list">
            {state.findings.map((f, i) => (
              <li key={i}>
                <code>{f.rule_id}</code> {f.message}
              </li>
            ))}
          </ul>
        </div>

        <div>
          <WordingReviewPanel
            claimId={claimId}
            // A server mid-deploy may not send it yet; the panel must not take the page down.
            state={detail.wording_review ?? { status: "none", review: null, error: "", runs: 0 }}
            activeTurn={questionTurn}
            onShowTurn={(turnId) => {
              setHighlight(null);
              setQuestionTurn(turnId);
            }}
            onUnauthorized={onUnauthorized}
          />

          <h3>Transcript</h3>
          <ol className="transcript review-transcript">
            {state.transcript.map((t) => (
              <li key={t.id} className={`turn turn-${t.speaker}${sourceTurns.has(t.id) ? " source" : ""}`}>
                <span className="who">
                  {t.speaker === "agent" ? "Agent" : "Claimant"} · {t.id}
                </span>
                <p>{t.text}</p>
              </li>
            ))}
          </ol>

          <h3>Audit trail</h3>
          <ol className="audit">
            {audit.map((e, i) => (
              <li key={i}>
                <time dateTime={e.at}>{new Date(e.at).toLocaleString()}</time>
                <span className={`actor actor-${e.actor}`}>{e.actor}</span>
                <span>
                  <strong>{e.action.replace(/_/g, " ")}</strong> {e.detail}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}
