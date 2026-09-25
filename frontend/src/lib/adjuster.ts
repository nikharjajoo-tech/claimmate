import type { AuditEntry, ClaimStatus, ClaimView, QueueItem, Route } from "./types";

export interface ClaimDetail {
  id: string;
  state: ClaimView;
  audit: AuditEntry[];
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
  packetUrl: (id: string) => `/api/adjuster/claims/${id}/packet.zip`,
};

/** "4 min ago", "3 h ago", "2 d ago" for queue ages. */
export function age(iso: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}
