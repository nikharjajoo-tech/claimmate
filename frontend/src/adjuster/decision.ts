/** The adjuster's decision on a claim.
 *
 * INTERIM (M8 demo). The claim record has no `outcome` column yet, so a decision is written as the
 * note on the closing transition and read back out of the audit trail here. That keeps the demo
 * honest — the claim really does close and the words really are stored — while the structured
 * field waits for its own migration.
 *
 * When that lands: delete this file, read `state.outcome`, and keep the labels.
 */
import type { AuditEntry } from "../lib/types";

export type Outcome = "approved" | "denied";

export interface Decision {
  outcome: Outcome;
  reason: string;
  at: string;
}

/** The prefix the note carries, so the outcome survives a page reload. */
export const PREFIX: Record<Outcome, string> = {
  approved: "Approved",
  denied: "Denied",
};

export const LABEL: Record<Outcome, string> = {
  approved: "Approved for payment",
  denied: "Denied",
};

export function noteFor(outcome: Outcome, reason: string): string {
  return `${PREFIX[outcome]} — ${reason.trim()}`;
}

/** The decision recorded on this claim, if there is one. Reads the closing audit entry. */
export function decisionFrom(audit: AuditEntry[]): Decision | null {
  for (let i = audit.length - 1; i >= 0; i--) {
    const entry = audit[i];
    if (entry.actor !== "adjuster" || entry.action !== "status_changed") continue;
    for (const outcome of ["approved", "denied"] as Outcome[]) {
      const marker = `closed: ${PREFIX[outcome]} — `;
      const at = entry.detail.indexOf(marker);
      if (at !== -1) {
        return { outcome, reason: entry.detail.slice(at + marker.length).trim(), at: entry.at };
      }
    }
  }
  return null;
}
