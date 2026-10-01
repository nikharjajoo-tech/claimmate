import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WordingReview, WordingReviewState } from "../lib/adjuster";
import { REVIEW } from "./fixtures";
import { WordingReviewPanel } from "./WordingReviewPanel";

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

const noop = () => undefined;

function panel(state: Partial<WordingReviewState> = {}, review: WordingReview | null = REVIEW) {
  const full: WordingReviewState = { status: "ready", review, error: "", runs: 1, ...state };
  render(
    <WordingReviewPanel claimId="abc" state={full} activeTurn={null} onShowTurn={noop} onUnauthorized={noop} />,
  );
}

describe("WordingReviewPanel", () => {
  beforeEach(() => {
    detail.mockReset();
    refresh.mockReset();
  });

  it("shows the policy's own words, with the clause it came from", () => {
    panel();
    expect(screen.getByText(/§2.5 Water backup and sump overflow endorsement/)).toBeInTheDocument();
    expect(screen.getByText(/whether or not the backup or overflow was caused by a mechanical breakdown/)).toBeInTheDocument();
    expect(screen.getByText("The claimant describes a sump pump failure.")).toBeInTheDocument();
    expect(screen.getByText(/Wording homeowners\/v1/)).toBeInTheDocument();
  });

  it("always carries the caveat that an adjuster decides", () => {
    panel();
    expect(screen.getByText("AI-assisted · verify against the policy")).toBeInTheDocument();
  });

  it("says when the model's summary was replaced for deciding the claim", () => {
    panel({}, { ...REVIEW, summary_replaced: true });
    expect(screen.getByText(/stated a coverage conclusion/)).toBeInTheDocument();
  });

  it("explains itself while a review is being prepared", () => {
    panel({ status: "running", review: null }, null);
    expect(screen.getByText("Reading the policy wording…")).toBeInTheDocument();
  });

  it("offers a retry and the reason when a review failed", () => {
    panel({ status: "failed", review: null, error: "every model in the chain failed" }, null);
    expect(screen.getByText(/every model in the chain failed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("surfaces the cooldown refusal instead of failing silently", async () => {
    const { HttpError } = await import("../lib/adjuster");
    refresh.mockRejectedValue(new HttpError(429, "This review was just generated. Try again in 42s."));
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 42s.");
  });

  it("replaces the panel with the newly generated review", async () => {
    refresh.mockResolvedValue({
      status: "ready",
      runs: 2,
      error: "",
      review: { ...REVIEW, summary: "A second reading of the policy." },
    });
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByText("A second reading of the policy.")).toBeInTheDocument());
  });

  it("says nothing was matched rather than showing an empty list", () => {
    panel({}, { ...REVIEW, clauses: [] });
    expect(screen.getByText("Nothing in the wording was matched to this claim.")).toBeInTheDocument();
  });

  it("explains an unverifiable policy instead of blaming the wording", () => {
    panel({}, { ...REVIEW, clauses: [], wording_ref: "", policy_number: "" });
    expect(screen.getByText("No policy wording was read.")).toBeInTheDocument();
    expect(screen.getByText(/No wording: the policy could not be verified/)).toBeInTheDocument();
  });

  it("notes when the checks dropped something", () => {
    panel({}, { ...REVIEW, dropped: [{ kind: "clause", reason: "anchor_not_in_section", detail: "§9.1" }] });
    expect(screen.getByText(/1 item the model produced could not be verified/)).toBeInTheDocument();
  });

  it("hides its controls when the feature is switched off", () => {
    panel({ status: "disabled", review: null }, null);
    expect(screen.getByText(/not configured on this server/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
