import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ClaimDetail, WordingReviewState } from "../lib/adjuster";
import { REVIEW } from "./fixtures";
import type { ClaimStatus, ClaimView } from "../lib/types";
import { ClaimDetailView } from "./ClaimDetailView";

const detail = vi.fn();
const refresh = vi.fn();
vi.mock("../lib/adjuster", async (original) => ({
  ...(await original<typeof import("../lib/adjuster")>()),
  adjusterApi: {
    detail: (...args: unknown[]) => detail(...args),
    packetUrl: (id: string) => `/zip/${id}`,
    refreshWordingReview: (...args: unknown[]) => refresh(...args),
  },
}));

function claim(
  status: ClaimStatus,
  overrides: Partial<ClaimView> = {},
  wording: WordingReviewState = { status: "ready", running: false, review: REVIEW, error: "", runs: 1 },
): ClaimDetail {
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
  return {
    id: state.id,
    state,
    audit: [{ at: "2026-09-25T12:00:00Z", actor: "system", action: "claim_created", detail: "Intake started." }],
    wording_review: wording,
  };
}

const noop = () => undefined;

describe("ClaimDetailView", () => {
  beforeEach(() => {
    detail.mockReset();
    refresh.mockReset();
  });

  it("offers review actions only when the claim is in review", async () => {
    detail.mockResolvedValue(claim("in_review"));
    render(<ClaimDetailView claimId="abcdef123456" onChanged={noop} onUnauthorized={noop} />);
    expect(await screen.findByText("Request documents")).toBeInTheDocument();
    // Closing a claim now says what was decided, so the bare "Close claim" button is gone.
    expect(screen.getByText("Approve claim")).toBeInTheDocument();
    expect(screen.getByText("Deny claim")).toBeInTheDocument();
    expect(screen.queryByText("Close claim")).not.toBeInTheDocument();
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

  it("highlights the transcript turn a claimant question was asked in", async () => {
    detail.mockResolvedValue(claim("in_review"));
    render(<ClaimDetailView claimId="a" onChanged={noop} onUnauthorized={noop} />);
    const turn = (await screen.findByText("I'm Elena Brooks.")).closest("li")!;
    expect(turn).not.toHaveClass("source");

    fireEvent.click(screen.getByRole("button", { name: /I'm Elena Brooks/ }));
    expect(turn).toHaveClass("source");
  });

  it("clears a fact highlight when a question is selected, and the other way round", async () => {
    detail.mockResolvedValue(claim("in_review"));
    render(<ClaimDetailView claimId="a" onChanged={noop} onUnauthorized={noop} />);
    await screen.findByText("Transcript");

    fireEvent.click(screen.getByText("Elena Brooks", { selector: "dd" }));
    const factRow = screen.getByText("Elena Brooks", { selector: "dd" }).closest("div")!;
    expect(factRow).toHaveClass("active");

    fireEvent.click(screen.getByRole("button", { name: /I'm Elena Brooks/ }));
    expect(factRow).not.toHaveClass("active");
  });

  it("requires a reason before a claim can be approved", async () => {
    detail.mockResolvedValue(claim("in_review"));
    render(<ClaimDetailView claimId="a" onChanged={noop} onUnauthorized={noop} />);
    fireEvent.click(await screen.findByText("Approve claim"));

    const confirm = screen.getByRole("button", { name: "Approve and close" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: /Why this is approved/ }), {
      target: { value: "Reimbursable after the $300 deductible." },
    });
    expect(confirm).toBeEnabled();
  });

  it("shows the decision once the claim has been closed with one", async () => {
    detail.mockResolvedValue({
      ...claim("closed"),
      audit: [
        {
          at: "2026-10-02T12:00:00Z",
          actor: "adjuster",
          action: "status_changed",
          detail: "in_review -> closed: Approved — Reimbursable after the $300 deductible.",
        },
      ],
    });
    render(<ClaimDetailView claimId="a" onChanged={noop} onUnauthorized={noop} />);
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("Approved for payment");
    expect(banner).toHaveTextContent("Reimbursable after the $300 deductible.");
  });

  it("explains an override", async () => {
    detail.mockResolvedValue(claim("in_review", { route: "policy_review", route_override: "policy_review", override_reason: "Deductible dispute" }));
    render(<ClaimDetailView claimId="a" onChanged={noop} onUnauthorized={noop} />);
    expect(await screen.findByText(/Route overridden by adjuster: “Deductible dispute”/)).toBeInTheDocument();
  });
});
