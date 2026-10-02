import type { AuditEntry, ClaimStatus, ClaimView, QueueItem, Route } from "./types";

export interface ReviewClause {
  section: string;
  heading: string;
  part: string;
  quote: string;
  anchor: string;
  why_it_matters: string;
}

export interface ReviewQuestion {
  turn_id: string;
  quote: string;
}

export interface WordingReview {
  wording_ref: string;
  policy_number: string;
  policy_status: string;
  summary: string;
  summary_replaced: boolean;
  clauses: ReviewClause[];
  points_to_check: string[];
  questions: ReviewQuestion[];
  dropped: { kind: string; reason: string; detail: string }[];
  prompt_version: string;
  pipeline_revision: number | null;
  generated_at: string;
}

/** "none" = never started, "running" = generating now, "pending" = stranded, retried on open. */
export type WordingReviewStatus = "none" | "pending" | "ready" | "failed" | "disabled";

export interface WordingReviewState {
  /** What is stored. "pending" means a generation was stranded; opening the claim retries it. */
  status: WordingReviewStatus;
  /** Whether one is being generated right now. Separate from status: a refresh keeps showing
      the previous review while the new one is prepared. */
  running: boolean;
  review: WordingReview | null;
  error: string;
  runs: number;
  updated_at?: string;
}

export interface ClaimDetail {
  id: string;
  state: ClaimView;
  audit: AuditEntry[];
  wording_review: WordingReviewState;
}

export interface OperationsMetrics {
  claims: { total: number; by_status: Record<string, number>; by_route: Record<string, number> };
  pipeline: {
    runs: number;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
    tokens_in: number;
    tokens_out: number;
    runs_by_model: Record<string, number>;
  };
  voice: { turns: number; first_audio_p50_ms: number | null; first_audio_p95_ms: number | null; target_p50_ms: number };
  review: { reviewed: number; overridden: number };
  wording_review: {
    claims: number;
    by_status: Record<string, number>;
    generations: number;
    latency_p50_ms: number | null;
    tokens_in: number;
    tokens_out: number;
  };
}

export class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/adjuster${path}`, {
    credentials: "include",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!response.ok) {
    // FastAPI errors are JSON ({"detail": ...}); claim rule errors are plain text.
    const text = await response.text();
    let message = text || `Request failed (${response.status})`;
    try {
      const body = JSON.parse(text);
      if (typeof body?.detail === "string") message = body.detail;
      else if (Array.isArray(body?.detail)) message = "Please check the form and try again.";
    } catch {
      // plain text: use as is
    }
    throw new HttpError(response.status, message);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const adjusterApi = {
  me: () => call<{ enabled: boolean; signed_in: boolean }>("/me"),
  login: (passcode: string) => post<void>("/login", { passcode }),
  logout: () => post<void>("/logout"),
  queue: (filters: { status: string; route: string; type: string }) => {
    const params = new URLSearchParams({ status: filters.status });
    if (filters.route) params.set("route", filters.route);
    if (filters.type) params.set("type", filters.type);
    return call<{ claims: QueueItem[] }>(`/claims?${params}`);
  },
  detail: (id: string) => call<ClaimDetail>(`/claims/${id}`),
  open: (id: string) => post<ClaimDetail>(`/claims/${id}/open`),
  setStatus: (id: string, status: ClaimStatus, note: string) => post<ClaimDetail>(`/claims/${id}/status`, { status, note }),
  override: (id: string, route: Route, reason: string) => post<ClaimDetail>(`/claims/${id}/override`, { route, reason }),
  wordingReview: (id: string) => call<WordingReviewState>(`/claims/${id}/wording-review`),
  refreshWordingReview: (id: string) => post<WordingReviewState>(`/claims/${id}/wording-review/refresh`),
  packetUrl: (id: string) => `/api/adjuster/claims/${id}/packet.zip`,
  metrics: () => call<OperationsMetrics>("/metrics"),
};

/** "4 min ago", "3 h ago", "2 d ago" for queue ages. */
export function age(iso: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

/** "1.2 s" / "840 ms"; "No data" when nothing was measured (never a misleading zero). */
export function formatMs(ms: number | null): string {
  if (ms === null) return "No data";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}
