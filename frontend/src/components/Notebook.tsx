import type { ClaimView, Route, ToolActivity } from "../lib/types";

export const ROUTE_LABEL: Record<Route, string> = {
  emergency_escalation: "Emergency — human review now",
  special_investigation: "Special investigation",
  policy_review: "Policy review",
  needs_docs: "Needs documents",
  ready_for_adjuster: "Ready for adjuster",
};

const title = (value: string | null) => (value ? value.replace(/_/g, " ") : "—");

export function Notebook({ claim, tools }: { claim: ClaimView | null; tools: ToolActivity[] }) {
  if (!claim) return <section className="panel notebook">Starting a claim…</section>;
  const present = claim.safety.filter((s) => s.status !== "absent");

  return (
    <section className="panel notebook" aria-label="Claim notebook">
      <header className="notebook-head">
        <div>
          <h2>Claim notebook</h2>
          <p className="muted">
            {claim.claim_type ? title(claim.claim_type) : "Details appear here as you talk"}
            {claim.severity && ` · ${claim.severity} severity`}
            {claim.processing && " · updating…"}
          </p>
        </div>
        {claim.route && <span className={`stamp stamp-${claim.route}`}>{ROUTE_LABEL[claim.route]}</span>}
      </header>

      {claim.error && <p className="banner banner-warn">Claim update failed; it will retry on your next message.</p>}
      {present.length > 0 && (
        <div className="banner banner-danger" role="alert">
          <strong>Safety:</strong> {present.map((s) => `${s.category} (${s.status})`).join(", ")}. If anyone is in
          danger, contact emergency services.
        </div>
      )}

      <dl className="fields">
        {claim.fields.map((f) => (
          <div key={f.key} className={f.value ? "filled" : "missing"}>
            <dt>{f.label}</dt>
            <dd>{f.value ?? "Not yet"}</dd>
          </div>
        ))}
        <div className={claim.estimated_loss_usd != null ? "filled" : "missing"}>
          <dt>Estimated loss</dt>
          <dd>{claim.estimated_loss_usd != null ? `$${claim.estimated_loss_usd.toLocaleString()}` : "Not yet"}</dd>
        </div>
      </dl>

      {claim.policy && (
        <div className={`policy ${claim.policy.found && claim.policy.status === "active" ? "ok" : "flag"}`}>
          {claim.policy.found ? (
            <>
              <strong>{claim.policy.number}</strong> · {claim.policy.holder} · {claim.policy.line} ·{" "}
              <span className="policy-status">{claim.policy.status}</span>
            </>
          ) : (
            <>Policy not verified. {claim.policy.message}</>
          )}
        </div>
      )}

      {claim.checklist.length > 0 && (
        <>
          <h3>Documents</h3>
          <ul className="checklist">
            {claim.checklist.map((item) => (
              <li key={item.label} className={item.satisfied ? "done" : ""} title={item.reason}>
                <span className="box" aria-hidden>
                  {item.satisfied ? "✓" : ""}
                </span>
                {item.label}
                <span className={`chip chip-${item.status}`}>{item.status}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {claim.evidence.length > 0 && (
        <>
          <h3>Evidence ({claim.evidence.length})</h3>
          <ul className="gallery">
            {claim.evidence.map((item) => (
              <li key={item.capture_id}>
                <img src={item.url} alt={item.caption} loading="lazy" />
                <p>{item.caption}</p>
                {item.claimant_claim ? (
                  <span className={`badge ${item.confirmed ? "badge-ok" : "badge-warn"}`}>
                    {item.confirmed ? "Matches your description" : `Not confirmed: “${item.claimant_claim}”`}
                  </span>
                ) : (
                  <span className="badge">No description to check</span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      {claim.next_question && (
        <p className="next">
          <span className="muted">Next question</span>
          {claim.next_question}
        </p>
      )}

      {claim.findings.length > 0 && (
        <details className="findings">
          <summary>Rules fired ({claim.findings.length})</summary>
          <ul>
            {claim.findings.map((f, i) => (
              <li key={`${f.rule_id}-${i}`}>
                <code>{f.rule_id}</code> {f.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      {tools.length > 0 && (
        <>
          <h3>Claims team</h3>
          <ul className="activity">
            {tools
              .slice()
              .reverse()
              .map((t) => (
                <li key={t.id} className={`tool tool-${t.phase}`}>
                  <span className="tool-name">{t.name.replace(/_/g, " ")}</span>
                  <span>{t.headline}</span>
                  <span className="muted">
                    {t.phase === "running"
                      ? "working…"
                      : t.scheduling === "INTERRUPT"
                        ? "interrupted agent"
                        : t.duration_ms != null
                          ? `${t.duration_ms} ms`
                          : ""}
                  </span>
                </li>
              ))}
          </ul>
        </>
      )}

      <p className="disclaimer">
        This intake does not confirm coverage, liability, or payment. A licensed adjuster reviews every claim.
      </p>
    </section>
  );
}
