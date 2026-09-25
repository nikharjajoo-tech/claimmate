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
}

export function QueueView({ selected, onSelect, version, onUnauthorized }: Props) {
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

  return (
    <section className="panel queue" aria-label="Claim queue">
      <header className="queue-head">
        <h2>Queue {claims && <span className="muted">({claims.length})</span>}</h2>
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
                <span className="muted">{age(c.submitted_at ?? c.created_at)}</span>
              </span>
              <span className="qi-meta">
                {c.route ? ROUTE_LABEL[c.route] : "No route yet"}
                {c.overridden && " · overridden"}
              </span>
              <span className="qi-tags">
                <span className={`pill pill-${c.status}`}>{STATUS_LABEL[c.status]}</span>
                {c.live && <span className="pill pill-live">Live call</span>}
                {c.claim_type && <span className="muted">{c.claim_type.replace(/_/g, " ")}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
