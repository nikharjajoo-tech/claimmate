// Mirrors backend/app/services/view.py and the relay message protocol.

export type Speaker = "claimant" | "agent";

export interface Turn {
  id: string;
  speaker: Speaker;
  text: string;
  partial?: boolean;
}

export interface ToolActivity {
  id: string;
  name: string;
  args: Record<string, unknown>;
  phase: "running" | "done" | "error" | "cancelled";
  headline: string;
  duration_ms: number | null;
  scheduling: string | null;
}

export type Route =
  | "emergency_escalation"
  | "special_investigation"
  | "policy_review"
  | "needs_docs"
  | "ready_for_adjuster";

export interface ClaimView {
  id: string;
  revision: number;
  up_to_date: boolean;
  processing: boolean;
  live_connected: boolean;
  error: string;
  transcript: Turn[];
  tool_activity: ToolActivity[];
  escalations: string[];
  route: Route | null;
  claim_type: string | null;
  severity: string | null;
  rationale: string;
  fields: { key: string; label: string; value: string | null }[];
  estimated_loss_usd: number | null;
  safety: { category: string; status: "present" | "absent" | "uncertain"; description: string }[];
  checklist: { label: string; status: string; satisfied: boolean; reason: string }[];
  findings: { rule_id: string; severity: string; message: string }[];
  policy: { found: true; number: string; holder: string; line: string; status: string } | { found: false; message: string } | null;
  next_question: string;
  packet_markdown: string;
}

export type ServerMessage =
  | { type: "ready"; model: string }
  | { type: "transcript"; speaker: Speaker; id: string; text: string; final: boolean }
  | { type: "audio"; data: string }
  | { type: "interrupted" }
  | ({ type: "tool" } & ToolActivity)
  | { type: "state"; state: ClaimView }
  | { type: "error"; message: string };
