import type { ClaimView, ServerMessage } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  createClaim: () => request<{ id: string; state: ClaimView }>("/api/claims", { method: "POST" }),
  getClaim: (id: string) => request<{ id: string; state: ClaimView }>(`/api/claims/${id}`),
  deleteClaim: (id: string) => request<{ deleted: boolean }>(`/api/claims/${id}`, { method: "DELETE" }),
  sendMessage: (id: string, text: string, messageId: string) =>
    request<{ id: string; state: ClaimView }>(`/api/claims/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ text, id: messageId }),
    }),
  uploadEvidenceBatch: (id: string, photos: { data: string; claim: string }[]) =>
    request<{ id: string; results: { ok: boolean; error?: string }[]; state: ClaimView }>(
      `/api/claims/${id}/evidence/batch`,
      { method: "POST", body: JSON.stringify({ photos }) },
    ),
  packetUrl: (id: string) => `/api/claims/${id}/packet`,
};

/** One live call. Messages are JSON in both directions; see backend/app/live/relay.py. */
export class LiveConnection {
  private socket: WebSocket;

  constructor(claimId: string, onMessage: (m: ServerMessage) => void, onClose: (reason: string) => void) {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(`${scheme}://${location.host}/ws/claims/${claimId}/live`);
    this.socket.onmessage = (event) => onMessage(JSON.parse(event.data as string) as ServerMessage);
    this.socket.onclose = (event) => onClose(event.reason || (event.code === 1008 ? "Connection refused" : ""));
  }

  get open(): boolean {
    return this.socket.readyState === WebSocket.OPEN;
  }

  send(message: Record<string, unknown>): void {
    if (this.open) this.socket.send(JSON.stringify(message));
  }

  close(): void {
    this.send({ type: "close" });
    this.socket.close();
  }
}
