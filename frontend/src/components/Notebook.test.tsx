import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ClaimView } from "../lib/types";
import { Notebook } from "./Notebook";

const base: ClaimView = {
  id: "c1",
  revision: 2,
  up_to_date: true,
  processing: false,
  live_connected: false,
  error: "",
  transcript: [],
  tool_activity: [],
  escalations: [],
  camera_on: false,
  evidence: [],
  status: "intake",
  pipeline_route: null,
  route_frozen: false,
  route_override: null,
  override_reason: null,
  fact_sources: {},
  route: "needs_docs",
  claim_type: "home_water_damage",
  severity: "medium",
  rationale: "",
  fields: [
    { key: "policyholder_name", label: "Name", value: "Elena Brooks" },
    { key: "contact", label: "Contact", value: null },
  ],
  estimated_loss_usd: 9000,
  safety: [{ category: "injury", status: "absent", description: "Nobody hurt" }],
  checklist: [
    { label: "Photos of damaged areas", status: "received", satisfied: true, reason: "" },
    { label: "Repair estimate", status: "planned", satisfied: false, reason: "" },
  ],
  findings: [{ rule_id: "DOC-001", severity: "medium", message: "Repair estimate not yet received (planned)." }],
  policy: { found: true, number: "HO-20417", holder: "Elena Brooks", line: "Homeowners (HO-3)", status: "active" },
  next_question: "Do you have the repair estimate?",
  packet_markdown: "# Claim",
};

describe("Notebook", () => {
  it("renders facts, missing fields, route, policy, and documents", () => {
    render(<Notebook claim={base} tools={[]} />);
    expect(screen.getByText("Elena Brooks", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getAllByText("Not yet")).toHaveLength(1);
    expect(screen.getByText("$9,000")).toBeInTheDocument();
    expect(screen.getByText("Needs documents")).toBeInTheDocument();
    expect(screen.getByText("HO-20417")).toBeInTheDocument();
    expect(screen.getByText("Do you have the repair estimate?")).toBeInTheDocument();
    expect(screen.getByText("Rules fired (1)")).toBeInTheDocument();
  });

  it("does not show a safety alert for denied hazards", () => {
    render(<Notebook claim={base} tools={[]} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a safety alert and emergency stamp for a present injury", () => {
    const urgent: ClaimView = {
      ...base,
      route: "emergency_escalation",
      safety: [{ category: "injury", status: "present", description: "Neck pain" }],
    };
    render(<Notebook claim={urgent} tools={[]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("injury (present)");
    expect(screen.getByText("Emergency — human review now")).toBeInTheDocument();
  });

  it("shows evidence with verification badges", () => {
    const withEvidence: ClaimView = {
      ...base,
      evidence: [
        { capture_id: "a", caption: "Water line on drywall.", confirmed: true, claimant_claim: "water damage",
          document_types: ["damage_photo"], source: "agent", url: "/api/claims/c1/evidence/a" },
        { capture_id: "b", caption: "A small dark mark.", confirmed: false, claimant_claim: "a big crack",
          document_types: [], source: "claimant", url: "/api/claims/c1/evidence/b" },
        { capture_id: "c", caption: "A receipt.", confirmed: false, claimant_claim: "",
          document_types: ["other_proof"], source: "upload", url: "/api/claims/c1/evidence/c" },
      ],
    };
    render(<Notebook claim={withEvidence} tools={[]} />);
    expect(screen.getByText("Evidence (3)")).toBeInTheDocument();
    expect(screen.getByText("Matches your description")).toBeInTheDocument();
    expect(screen.getByText("Not confirmed: “a big crack”")).toBeInTheDocument();
    expect(screen.getByText("No description to check")).toBeInTheDocument();
    expect(screen.getByAltText("Water line on drywall.")).toHaveAttribute("src", "/api/claims/c1/evidence/a");
  });
});
