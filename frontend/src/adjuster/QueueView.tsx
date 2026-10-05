import { useEffect, useState } from "react";
import { ROUTE_LABEL } from "../components/Notebook";
import { adjusterApi, age } from "../lib/adjuster";
import type { QueueItem, Route } from "../lib/types";

const REFRESH_MS = 10_000;
const STATUS_LABEL: Record<string, string> = {
  intake: "In progress",
  submitted: "New",
  in_review: "In review",
  awaiting_docs: "Awaiting docs",
  closed: "Closed",
};
const CLAIM_TYPES = [
  "home_water_damage",
  "auto_collision",
  "theft_property_loss",
  "travel_disruption",
  "medical_reimbursement",
  "other",
];

interface Props {
  selected: string | null;
  onSelect: (id: string) => void;
  version: number;
  onUnauthorized: (e: unknown) => void;
  /** Folded to a thin rail so the claim gets the width; it keeps refreshing either way. */
  collapsed: boolean;
  onToggle: () => void;
}

function Chevron({ pointing }: { pointing: "left" | "right" }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d={pointing === "left" ? "M10 3L5 8l5 5" : "M6 3l5 5-5 5"}
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function QueueView({ selected, onSelect, version, onUnauthorized, collapsed, onToggle }: Props) {
  const [filters, setFilters] = useState({ status: "open", route: "", type: "" });
  const [claims, setClaims] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const result = await adjusterApi.queue(filters);
        if (!cancelled) {
          setClaims(result.claims);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError("Could not load the queue.");
        onUnauthorized(e);
      }
    };
    void load();
    const timer = window.setInterval(load, REFRESH_MS); // new claims arrive while the adjuster works
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [filters, version, onUnauthorized]);

  const set = (key: keyof typeof filters) => (e: React.ChangeEvent<HTMLSelectElement>) =>
    setFilters((f) => ({ ...f, [key]: e.target.value }));

  if (collapsed) {
    return (
      <section className="panel queue collapsed" aria-label="Claim queue">
        <button className="icon-btn" onClick={onToggle} aria-expanded={false} aria-label="Show queue" title="Show queue">
          <Chevron pointing="right" />
        </button>
        <span className="rail-label">Queue {claims && `(${claims.length})`}</span>
      </section>
    );
  }

  return (
    <section className="panel queue" aria-label="Claim queue">
      <header className="queue-head">
        <h2>Queue {claims && <span className="muted">({claims.length})</span>}</h2>
        <button className="icon-btn" onClick={onToggle} aria-expanded aria-label="Hide queue" title="Hide queue">
          <Chevron pointing="left" />
        </button>
      </header>
      <div className="filters">
        <select aria-label="Status" value={filters.status} onChange={set("status")}>
          <option value="open">Open claims</option>
          <option value="submitted">New</option>
          <option value="in_review">In review</option>
          <option value="awaiting_docs">Awaiting docs</option>
          <option value="closed">Closed</option>
          <option value="all">All</option>
        </select>
        <select aria-label="Route" value={filters.route} onChange={set("route")}>
          <option value="">All routes</option>
          {(Object.keys(ROUTE_LABEL) as Route[]).map((r) => (
            <option key={r} value={r}>
              {ROUTE_LABEL[r]}
            </option>
          ))}
        </select>
        <select aria-label="Claim type" value={filters.type} onChange={set("type")}>
          <option value="">All types</option>
          {CLAIM_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="banner banner-warn">{error}</p>}
      {claims?.length === 0 && <p className="muted">No claims match these filters.</p>}
      <ul className="queue-list">
        {claims?.map((c) => (
          <li key={c.id}>
            <button
              className={`queue-item route-${c.route ?? "none"}${c.id === selected ? " selected" : ""}`}
              onClick={() => onSelect(c.id)}
              aria-current={c.id === selected}
            >
              <span className="qi-top">
                <strong>{c.claimant_name ?? "Unknown claimant"}</strong>
                <span className="qi-age">{age(c.submitted_at ?? c.created_at)}</span>
              </span>
              <span className="qi-meta">
                <span className={`pill pill-${c.status}`}>{STATUS_LABEL[c.status]}</span>
                {c.live && <span className="pill pill-live">Live call</span>}
                <span className="qi-text">
                  {c.route ? ROUTE_LABEL[c.route] : "No route yet"}
                  {c.overridden && " · overridden"}
                  {c.claim_type && <span className="qi-type"> · {c.claim_type.replace(/_/g, " ")}</span>}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
