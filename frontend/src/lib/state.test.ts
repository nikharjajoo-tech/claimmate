import { describe, expect, it } from "vitest";
import { initialState, mergeTranscript, reducer } from "./state";
import type { ClaimView, ToolActivity } from "./types";

const claim = (overrides: Partial<ClaimView> = {}): ClaimView => ({
  id: "c1",
  revision: 1,
  up_to_date: true,
  processing: false,
  live_connected: true,
  error: "",
  transcript: [],
  tool_activity: [],
  escalations: [],
  camera_on: false,
  evidence: [],
  route: "needs_docs",
  claim_type: "home_water_damage",
  severity: "medium",
  rationale: "",
  fields: [],
  estimated_loss_usd: null,
  safety: [],
  checklist: [],
  findings: [],
  policy: null,
  next_question: "",
  packet_markdown: "",
  ...overrides,
});

describe("transcript", () => {
  it("replaces a streaming turn in place when the final arrives", () => {
    let state = reducer(initialState, { type: "server", message: { type: "transcript", speaker: "claimant", id: "a", text: "My base", final: false } });
    state = reducer(state, { type: "server", message: { type: "transcript", speaker: "claimant", id: "a", text: "My basement flooded.", final: true } });
    expect(state.transcript).toEqual([{ id: "a", speaker: "claimant", text: "My basement flooded.", partial: false }]);
  });

  it("server snapshots win for final turns but keep unknown partials", () => {
    const merged = mergeTranscript(
      [{ id: "a", speaker: "claimant", text: "final text" }],
      [
        { id: "a", speaker: "claimant", text: "stale partial", partial: true },
        { id: "b", speaker: "agent", text: "still speaking", partial: true },
      ],
    );
    expect(merged.map((t) => t.text)).toEqual(["final text", "still speaking"]);
  });
});

describe("tools and state", () => {
  const tool = (id: string, phase: ToolActivity["phase"]): ToolActivity => ({
    id, name: "lookup_policy", args: {}, phase, headline: "", duration_ms: null, scheduling: null,
  });

  it("updates a tool's phase in place", () => {
    let state = reducer(initialState, { type: "server", message: { type: "tool", ...tool("t1", "running") } });
    state = reducer(state, { type: "server", message: { type: "tool", ...tool("t1", "done") } });
    expect(state.tools).toHaveLength(1);
    expect(state.tools[0].phase).toBe("done");
  });

  it("ready marks the call live and clears errors", () => {
    const state = reducer({ ...initialState, error: "old" }, { type: "server", message: { type: "ready", model: "m" } });
    expect(state.call).toBe("live");
    expect(state.error).toBe("");
  });

  it("state messages replace the claim snapshot", () => {
    const state = reducer(initialState, { type: "server", message: { type: "state", state: claim({ route: "policy_review" }) } });
    expect(state.claim?.route).toBe("policy_review");
  });
});
