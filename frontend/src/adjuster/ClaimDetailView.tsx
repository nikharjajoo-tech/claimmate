import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ROUTE_LABEL, title } from "../components/Notebook";
import { adjusterApi, type ClaimDetail, HttpError, type WordingReviewState } from "../lib/adjuster";
import { type Outcome, decisionFrom, LABEL, noteFor } from "./decision";
import { WordingReviewPanel } from "./WordingReviewPanel";
import type { ClaimStatus, ClaimView, Route } from "../lib/types";

interface Props {
  claimId: string;
  onChanged: () => void;
  onUnauthorized: (e: unknown) => void;
}

const ACTIONS: Record<ClaimStatus, { status: ClaimStatus; label: string; primary?: boolean }[]> = {
  intake: [],
  submitted: [],
  in_review: [{ status: "awaiting_docs", label: "Request documents" }],
  awaiting_docs: [{ status: "in_review", label: "Resume review" }],
  closed: [],
};

type Tab = "overview" | "policy" | "audit" | "transcript";

/** Wide enough for the transcript to sit beside the workspace; below it the transcript is a tab. */
const SPLIT_QUERY = "(min-width: 1200px)";

const NO_WORDING: WordingReviewState = { status: "none", running: false, review: null, error: "", runs: 0 };

function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (notify: () => void) => {
      if (typeof window.matchMedia !== "function") return () => undefined;
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => list.removeEventListener("change", notify);
    },
    [query],
  );
  // jsdom has no matchMedia; render the full layout there.
  return useSyncExternalStore(subscribe, () => typeof window.matchMedia !== "function" || window.matchMedia(query).matches);
}

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
  const [deciding, setDeciding] = useState<Outcome | null>(null);
  const [decisionReason, setDecisionReason] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  // The wording panel follows a running review itself; it reports back so the summary up top stays current.
  const [wording, setWording] = useState<WordingReviewState | null>(null);
  const split = useMediaQuery(SPLIT_QUERY);
  const transcriptRef = useRef<HTMLOListElement>(null);
  const current: Tab = split && tab === "transcript" ? "overview" : tab;

  const sourceTurns = new Set(highlight ? (detail?.state.fact_sources[highlight] ?? []) : []);
  if (questionTurn) sourceTurns.add(questionTurn);
  const focusTurn = detail?.state.transcript.find((t) => sourceTurns.has(t.id))?.id;

  // Bring the highlighted turn into view inside the transcript, without scrolling the page.
  useEffect(() => {
    const list = transcriptRef.current;
    const turn = focusTurn && list?.querySelector<HTMLElement>(`[data-turn="${focusTurn}"]`);
    if (!list || !turn) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    list.scrollTo?.({ top: turn.offsetTop - list.clientHeight / 3, behavior: still ? "auto" : "smooth" });
  }, [focusTurn, current]);

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
      setDeciding(null);
      setDecisionReason("");
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
  const reviewable = state.status === "in_review" || state.status === "awaiting_docs";
  const decision = decisionFrom(audit);
  // A server mid-deploy may not send it yet; the panel must not take the page down.
  const wordingState = wording ?? detail.wording_review ?? NO_WORDING;

  // On a narrow screen the transcript is a tab, so pointing at a turn has to open it.
  const showFact = (key: string) => {
    setQuestionTurn(null);
    setHighlight(highlight === key ? null : key);
    if (!split && highlight !== key && state.fact_sources[key]) setTab("transcript");
  };
  const showQuestion = (turnId: string | null) => {
    setHighlight(null);
    setQuestionTurn(turnId);
    if (!split && turnId) setTab("transcript");
  };

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "overview", label: "Overview" },
    { id: "policy", label: "Policy wording" },
    { id: "audit", label: "Audit trail", count: audit.length },
    ...(split ? [] : [{ id: "transcript" as const, label: "Transcript", count: state.transcript.length }]),
  ];
  const onTabKey = (e: React.KeyboardEvent) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    const next = tabs[(tabs.findIndex((t) => t.id === current) + step + tabs.length) % tabs.length];
    setTab(next.id);
    document.getElementById(`tab-${next.id}`)?.focus();
  };

  const transcript = (
    <ol className="transcript review-transcript" ref={transcriptRef}>
      {state.transcript.map((t) => (
        <li key={t.id} data-turn={t.id} className={`turn turn-${t.speaker}${sourceTurns.has(t.id) ? " source" : ""}`}>
          <span className="who">
            {t.speaker === "agent" ? "Agent" : "Claimant"} · {t.id}
          </span>
          <p>{t.text}</p>
        </li>
      ))}
    </ol>
  );

  return (
    <section className={`detail${split ? " split" : ""}`} aria-label="Claim detail">
      <div className="panel workspace">
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

        {decision && (
          <p className={`decision decision-${decision.outcome}`} role="status">
            <strong>{LABEL[decision.outcome]}</strong> by the adjuster on{" "}
            {new Date(decision.at).toLocaleString()} — {decision.reason}
          </p>
        )}

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
          {reviewable && !deciding && (
            <>
              <button className="btn btn-primary" onClick={() => setDeciding("approved")}>
                Approve claim
              </button>
              <button className="btn btn-danger" onClick={() => setDeciding("denied")}>
                Deny claim
              </button>
            </>
          )}
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

        {deciding && (
          <form
            className="override decide"
            onSubmit={(e) => {
              e.preventDefault();
              void act(() => adjusterApi.setStatus(claimId, "closed", noteFor(deciding, decisionReason)));
            }}
          >
            <label htmlFor="decision-reason">
              {deciding === "approved" ? "Why this is approved" : "Why this is denied"} (required, recorded in the audit
              trail)
            </label>
            <textarea
              id="decision-reason"
              rows={2}
              autoFocus
              value={decisionReason}
              maxLength={900}
              placeholder={
                deciding === "approved"
                  ? "e.g. Reimbursable after the $300 annual deductible; all three documents verified."
                  : "e.g. Loss falls outside the policy period on the declarations page."
              }
              onChange={(e) => setDecisionReason(e.target.value)}
            />
            <div className="row">
              <button
                className={`btn ${deciding === "approved" ? "btn-primary" : "btn-danger"}`}
                type="submit"
                disabled={busy || decisionReason.trim().length < 10}
              >
                {deciding === "approved" ? "Approve and close" : "Deny and close"}
              </button>
              <button className="btn" type="button" onClick={() => setDeciding(null)}>
                Cancel
              </button>
            </div>
          </form>
        )}

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

        <Glance state={state} wording={wordingState} onOpen={setTab} />

        <div className="tabs" role="tablist" aria-label="Claim sections" onKeyDown={onTabKey}>
          {tabs.map((t) => (
            <button
              key={t.id}
              id={`tab-${t.id}`}
              className="tab"
              role="tab"
              aria-selected={current === t.id}
              aria-controls={`panel-${t.id}`}
              tabIndex={current === t.id ? 0 : -1}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.count !== undefined && <span className="tab-count">{t.count}</span>}
            </button>
          ))}
        </div>

        {/* Every panel stays mounted, so switching tabs keeps a running review and half-typed text. */}
        <div className="tabpanel" role="tabpanel" id="panel-overview" aria-labelledby="tab-overview" hidden={current !== "overview"}>
          <h3>Facts</h3>
          <p className="muted hint">Select a fact to highlight where the claimant said it.</p>
          <dl className="fields">
            {state.fields.map((f) => (
              <div
                key={f.key}
                className={`${f.value ? "filled" : "missing"}${highlight === f.key ? " active" : ""}`}
                role={state.fact_sources[f.key] ? "button" : undefined}
                tabIndex={state.fact_sources[f.key] ? 0 : undefined}
                onClick={() => showFact(f.key)}
                onKeyDown={(e) => e.key === "Enter" && showFact(f.key)}
              >
                <dt>{f.label}</dt>
                <dd>
                  {f.value ?? "Not provided"}
                  {f.note && <span className="field-note">{f.note}</span>}
                </dd>
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

          <div className="overview-cols">
            <div>
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
            </div>

            <div>
              <h3>Rules fired ({state.findings.length})</h3>
              <ul className="plain-list">
                {state.findings.map((f, i) => (
                  <li key={i}>
                    <code>{f.rule_id}</code> {f.message}
                  </li>
                ))}
              </ul>
            </div>
          </div>

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
        </div>

        <div className="tabpanel" role="tabpanel" id="panel-policy" aria-labelledby="tab-policy" hidden={current !== "policy"}>
          <WordingReviewPanel
            claimId={claimId}
            state={detail.wording_review ?? NO_WORDING}
            activeTurn={questionTurn}
            onShowTurn={showQuestion}
            onChange={setWording}
            onUnauthorized={onUnauthorized}
          />
        </div>

        <div className="tabpanel" role="tabpanel" id="panel-audit" aria-labelledby="tab-audit" hidden={current !== "audit"}>
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

        {!split && (
          <div className="tabpanel" role="tabpanel" id="panel-transcript" aria-labelledby="tab-transcript" hidden={current !== "transcript"}>
            {transcript}
          </div>
        )}
      </div>

      {split && (
        <aside className="panel transcript-pane" aria-label="Transcript">
          <h3>Transcript</h3>
          {transcript}
        </aside>
      )}
    </section>
  );
}

/** What an adjuster needs before reading anything else: the summary and a count of what needs attention. */
function Glance({ state, wording, onOpen }: { state: ClaimView; wording: WordingReviewState; onOpen: (tab: Tab) => void }) {
  const { review } = wording;
  const received = state.checklist.filter((c) => c.satisfied).length;
  const flagged = state.safety.filter((s) => s.status !== "absent").length;
  const policy = !state.policy
    ? { value: "Not checked", tone: "" }
    : !state.policy.found
      ? { value: "Not verified", tone: "warn" }
      : { value: title(state.policy.status), tone: state.policy.status === "active" ? "ok" : "warn" };

  const stats: { label: string; value: string; tone: string; tab: Tab }[] = [
    { label: "Policy", ...policy, tab: "overview" },
    {
      label: "Documents",
      value: state.checklist.length ? `${received} of ${state.checklist.length} received` : "None listed",
      tone: !state.checklist.length ? "" : received === state.checklist.length ? "ok" : "warn",
      tab: "overview",
    },
    { label: "Safety", value: flagged ? `${flagged} flagged` : "No concerns", tone: flagged ? "danger" : "", tab: "overview" },
    { label: "Rules fired", value: String(state.findings.length), tone: state.findings.length ? "warn" : "", tab: "overview" },
    {
      label: "To check",
      value: review ? `${review.points_to_check.length} point${review.points_to_check.length === 1 ? "" : "s"}` : "No review yet",
      tone: "",
      tab: "policy",
    },
  ];

  return (
    <div className="glance" aria-label="At a glance">
      <div className="glance-summary">
        <span className="glance-label">
          Summary
          <span className="caveat" title="Written by the model from the claim facts. Check it against the transcript.">
            AI-assisted
          </span>
        </span>
        {review ? (
          <p>{review.summary}</p>
        ) : (
          <p className="muted">{wording.running ? "Reading the policy wording…" : "No summary yet. One is prepared with the wording review."}</p>
        )}
        {review?.summary_replaced && (
          <p className="muted small">
            The model's own summary stated a coverage conclusion, so this one was built from the claim facts instead.
          </p>
        )}
        {state.severity && state.rationale && (
          <p className="muted small">
            <span className="glance-severity">{state.severity} severity:</span> {state.rationale}
          </p>
        )}
      </div>
      <div className="glance-stats">
        {stats.map((s) => (
          <button key={s.label} className={`stat${s.tone ? ` stat-${s.tone}` : ""}`} onClick={() => onOpen(s.tab)}>
            <span className="stat-label">{s.label}</span>
            <span className="stat-value">{s.value}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
