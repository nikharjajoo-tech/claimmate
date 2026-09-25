import type { ClaimView, ServerMessage, ToolActivity, Turn } from "./types";

export type CallStatus = "idle" | "connecting" | "live" | "ended";

export interface AppState {
  claim: ClaimView | null;
  transcript: Turn[];
  tools: ToolActivity[];
  call: CallStatus;
  busy: boolean; // typed-mode request in flight
  error: string;
}

export const initialState: AppState = {
  claim: null,
  transcript: [],
  tools: [],
  call: "idle",
  busy: false,
  error: "",
};

export type Action =
  | { type: "reset" }
  | { type: "claim"; claim: ClaimView }
  | { type: "server"; message: ServerMessage }
  | { type: "call"; status: CallStatus }
  | { type: "busy"; busy: boolean }
  | { type: "error"; error: string };

/** Streaming turns are replaced in place by id; the final version wins. */
export function upsertTurn(turns: Turn[], turn: Turn): Turn[] {
  const index = turns.findIndex((t) => t.id === turn.id);
  if (index === -1) return [...turns, turn];
  const next = turns.slice();
  next[index] = turn;
  return next;
}

/** A server snapshot is authoritative for final turns; keep local partials it doesn't know yet. */
export function mergeTranscript(server: Turn[], local: Turn[]): Turn[] {
  const known = new Set(server.map((t) => t.id));
  return [...server, ...local.filter((t) => t.partial && !known.has(t.id))];
}

export function upsertTool(tools: ToolActivity[], tool: ToolActivity): ToolActivity[] {
  return [...tools.filter((t) => t.id !== tool.id), tool].slice(-12);
}

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "reset":
      return initialState;
    case "claim":
      return {
        ...state,
        claim: action.claim,
        transcript: mergeTranscript(action.claim.transcript, state.transcript),
        tools: action.claim.tool_activity.length ? action.claim.tool_activity : state.tools,
      };
    case "call":
      return { ...state, call: action.status };
    case "busy":
      return { ...state, busy: action.busy };
    case "error":
      return { ...state, error: action.error };
    case "server": {
      const m = action.message;
      switch (m.type) {
        case "transcript":
          return {
            ...state,
            transcript: upsertTurn(state.transcript, { id: m.id, speaker: m.speaker, text: m.text, partial: !m.final }),
          };
        case "tool": {
          const { type: _type, ...tool } = m;
          return { ...state, tools: upsertTool(state.tools, tool) };
        }
        case "state":
          return reducer(state, { type: "claim", claim: m.state });
        case "error":
          return { ...state, error: m.message };
        case "ready":
          return { ...state, call: "live", error: "" };
        default:
          return state; // audio and interrupted are handled outside React state
      }
    }
  }
}
