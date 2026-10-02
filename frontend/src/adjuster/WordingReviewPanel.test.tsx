import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WordingReview, WordingReviewState } from "../lib/adjuster";
import { REVIEW } from "./fixtures";
import { WordingReviewPanel } from "./WordingReviewPanel";

const detail = vi.fn();
const refresh = vi.fn();
const get = vi.fn();
vi.mock("../lib/adjuster", async (original) => ({
  ...(await original<typeof import("../lib/adjuster")>()),
  adjusterApi: {
    detail: (...args: unknown[]) => detail(...args),
    packetUrl: (id: string) => `/zip/${id}`,
    refreshWordingReview: (...args: unknown[]) => refresh(...args),
    wordingReview: (...args: unknown[]) => get(...args),
  },
}));

const noop = () => undefined;

function panel(state: Partial<WordingReviewState> = {}, review: WordingReview | null = REVIEW) {
  const full: WordingReviewState = { status: "ready", running: false, review, error: "", runs: 1, ...state };
  render(
    <WordingReviewPanel claimId="abc" state={full} activeTurn={null} onShowTurn={noop} onUnauthorized={noop} />,
  );
}

describe("WordingReviewPanel", () => {
  beforeEach(() => {
    detail.mockReset();
    refresh.mockReset();
    get.mockReset();
  });
  afterEach(() => vi.useRealTimers());

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
    panel({ status: "none", running: true, review: null }, null);
    expect(screen.getByText("Reading the policy wording…")).toBeInTheDocument();
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("keeps the previous review on screen while a new one is prepared", () => {
    panel({ running: true });
    expect(screen.getByText("Reading the policy wording again…")).toBeInTheDocument();
    expect(screen.getByText(/§2.5 Water backup/)).toBeInTheDocument();
  });

  it("follows a running generation instead of freezing on the old one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    get.mockResolvedValue({
      status: "ready",
      running: false,
      runs: 2,
      error: "",
      review: { ...REVIEW, summary: "A second reading of the policy." },
    });
    panel({ running: true });
    expect(screen.getByText("Reading the policy wording again…")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(2100);
    await waitFor(() => expect(screen.getByText("A second reading of the policy.")).toBeInTheDocument());
    expect(screen.queryByText("Reading the policy wording again…")).not.toBeInTheDocument();

    const polls = get.mock.calls.length;
    await act(async () => void (await vi.advanceTimersByTimeAsync(6000)));
    expect(get.mock.calls.length).toBe(polls); // stops once it is no longer running
  });

  it("gives up polling rather than hammering a server that never finishes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    get.mockResolvedValue({ status: "pending", running: true, runs: 1, error: "", review: null });
    panel({ status: "pending", running: true, review: null }, null);

    // One step per poll interval: React has to flush between them, or the component never
    // re-renders and never schedules the next poll.
    const step = async () => act(async () => void (await vi.advanceTimersByTimeAsync(2100)));
    for (let i = 0; i < 20; i++) await step();
    const during = get.mock.calls.length;
    expect(during).toBeGreaterThan(5); // it kept asking while the server said "running"

    for (let i = 0; i < 30; i++) await step(); // past the 90 s limit
    const afterLimit = get.mock.calls.length;
    for (let i = 0; i < 10; i++) await step();
    expect(get.mock.calls.length).toBe(afterLimit); // and then stopped, rather than asking for ever
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
      running: false,
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
    panel({ status: "disabled", running: false, review: null }, null);
    expect(screen.getByText(/not configured on this server/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
