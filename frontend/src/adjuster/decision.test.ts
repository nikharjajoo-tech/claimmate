import { describe, expect, it } from "vitest";
import type { AuditEntry } from "../lib/types";
import { decisionFrom, noteFor } from "./decision";

const entry = (overrides: Partial<AuditEntry>): AuditEntry => ({
  at: "2026-10-02T12:00:00Z",
  actor: "adjuster",
  action: "status_changed",
  detail: "",
  ...overrides,
});

describe("the adjuster's decision", () => {
  it("round-trips through the note the backend stores", () => {
    const note = noteFor("approved", "Reimbursable after the $300 deductible.");
    const audit = [entry({ detail: `in_review -> closed: ${note}` })];
    expect(decisionFrom(audit)).toEqual({
      outcome: "approved",
      reason: "Reimbursable after the $300 deductible.",
      at: "2026-10-02T12:00:00Z",
    });
  });

  it("reads a denial the same way", () => {
    const audit = [entry({ detail: `in_review -> closed: ${noteFor("denied", "Outside the policy period.")}` })];
    expect(decisionFrom(audit)?.outcome).toBe("denied");
  });

  it("ignores a claim closed without a decision", () => {
    expect(decisionFrom([entry({ detail: "in_review -> closed" })])).toBeNull();
    expect(decisionFrom([entry({ detail: "in_review -> closed: tidying up" })])).toBeNull();
  });

  it("ignores entries that are not an adjuster closing the claim", () => {
    const audit = [
      entry({ actor: "system", detail: "intake -> closed: Approved — by the system" }),
      entry({ action: "route_overridden", detail: "closed: Approved — not a status change" }),
    ];
    expect(decisionFrom(audit)).toBeNull();
  });

  it("takes the most recent decision when a claim was decided more than once", () => {
    const audit = [
      entry({ detail: `in_review -> closed: ${noteFor("denied", "No police report provided.")}` }),
      entry({ at: "2026-10-02T15:00:00Z", detail: `in_review -> closed: ${noteFor("approved", "Report arrived later.")}` }),
    ];
    expect(decisionFrom(audit)?.outcome).toBe("approved");
  });
});
