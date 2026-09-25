import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ClaimDetail } from "../lib/adjuster";
import type { ClaimStatus, ClaimView } from "../lib/types";
import { ClaimDetailView } from "./ClaimDetailView";

const detail = vi.fn();
vi.mock("../lib/adjuster", async (original) => ({
  ...(await original<typeof import("../lib/adjuster")>()),
  adjusterApi: { detail: (...args: unknown[]) => detail(...args), packetUrl: (id: string) => `/zip/${id}` },
}));

function claim(status: ClaimStatus, overrides: Partial<ClaimView> = {}): ClaimDetail {
  const state = {
    id: "abcdef123456", revision: 2, up_to_date: true, processing: false, live_connected: false, error: "",
    transcript: [
      { id: "t0", speaker: "agent", text: "Is everyone safe?" },
      { id: "t1", speaker: "claimant", text: "I'm Elena Brooks." },
    ],
    tool_activity: [], escalations: [], camera_on: false, evidence: [], status,
    pipeline_route: "needs_docs", route_frozen: status !== "submitted" && status !== "intake", route_override: null,
    override_reason: null, fact_sources: { policyholder_name: ["t1"] }, route: "needs_docs",
    claim_type: "home_water_damage", severity: "medium", rationale: "",
    fields: [{ key: "policyholder_name", label: "Name", value: "Elena Brooks" }], estimated_loss_usd: null,
    safety: [], checklist: [], findings: [], policy: null, next_question: "", packet_markdown: "# Claim",
    ...overrides,
  } as ClaimView;
  return { id: state.id, state, audit: [{ at: "2026-09-25T12:00:00Z", actor: "system", action: "claim_created", detail: "Intake started." }] };
}

const noop = () => undefined;

describe("ClaimDetailView", () => {
  beforeEach(() => detail.mockReset());

  it("offers review actions only when the claim is in review", async () => {
    detail.mockResolvedValue(claim("in_review"));
    render(<ClaimDetailView claimId="abcdef123456" onChanged={noop} onUnauthorized={noop} />);
    expect(await screen.findByText("Request documents")).toBeInTheDocument();
    expect(screen.getByText("Close claim")).toBeInTheDocument();
    expect(screen.getByText("Override route")).toBeInTheDocument();
    expect(screen.queryByText("Start review")).not.toBeInTheDocument();
  });

  it("starts review from a submitted claim and is view-only during intake", async () => {
    detail.mockResolvedValue(claim("submitted"));
    const { unmount } = render(<ClaimDetailView claimId="a" onChanged={noop} onUnauthorized={noop} />);
    expect(await screen.findByText("Start review")).toBeInTheDocument();
    expect(screen.queryByText("Override route")).not.toBeInTheDocument();
    unmount();

    detail.mockResolvedValue(claim("intake"));
    render(<ClaimDetailView claimId="b" onChanged={noop} onUnauthorized={noop} />);
    expect(await screen.findByText(/still reporting this claim/)).toBeInTheDocument();
    expect(screen.queryByText("Start review")).not.toBeInTheDocument();
  });

  it("highlights the transcript turn a fact came from", async () => {
    detail.mockResolvedValue(claim("in_review"));
    render(<ClaimDetailView claimId="a" onChanged={noop} onUnauthorized={noop} />);
    const turn = (await screen.findByText("I'm Elena Brooks.")).closest("li")!;
    expect(turn).not.toHaveClass("source");
    fireEvent.click(screen.getByText("Elena Brooks", { selector: "dd" }));
    expect(turn).toHaveClass("source");
  });

  it("explains an override", async () => {
    detail.mockResolvedValue(claim("in_review", { route: "policy_review", route_override: "policy_review", override_reason: "Deductible dispute" }));
    render(<ClaimDetailView claimId="a" onChanged={noop} onUnauthorized={noop} />);
    expect(await screen.findByText(/Route overridden by adjuster: “Deductible dispute”/)).toBeInTheDocument();
  });
});
