# Requirements: ClaimMate — Your Personal AI Claim Assistant

> Detailed functional and non-functional requirements. The product overview, users, and success
> metrics are in the [PRD](00-PRD.md).

## 1. Goal

Let a claimant report an insurance loss by **talking** (optionally showing their camera). A live voice
agent runs the conversation, while a background pipeline turns it into a **structured, verified,
routed claim** that a human adjuster can review in a dashboard.

**Success criteria (measurable, resume-ready):**
- Voice response latency (end of user speech → first agent audio) p50 < 1.5 s
- Field extraction F1 ≥ 0.90 on the eval scenario set
- Routing accuracy ≥ 90%; safety-escalation recall = 100% on eval scenarios
- Zero coverage/payment promises in agent output across eval runs (guardrail check)

## 2. Users

| User | Needs |
|------|-------|
| Claimant | Report a loss quickly by voice, show damage, get clear next steps |
| Adjuster | See a queue of routed claims with facts, evidence, rule findings, and audit trail; approve or override routing |
| Developer/Operator | Run evals, inspect traces, see latency and cost per claim |

## 3. Functional requirements

### FR-1 Live voice conversation
- FR-1.1 Browser streams mic audio (PCM16 16 kHz) over WebSocket; agent replies with streamed audio.
- FR-1.2 Barge-in: when the claimant interrupts, queued agent audio stops immediately.
- FR-1.3 Live transcripts for both sides; typed input goes through the same session (no-mic fallback).
- FR-1.4 Agent asks 1–2 questions at a time, finishes the current topic before raising open items.
- FR-1.5 Session resumes on reconnect with prior transcript restored as context.

### FR-2 Background tools (non-blocking)
| Tool | Behavior | Result scheduling |
|------|----------|-------------------|
| `lookup_policy` | Verify policy number against policy DB; normalize voice errors (O vs 0, spaces) | Interrupt if lapsed/not found, else when idle |
| `update_claim` | Run the claim pipeline on the conversation so far; return routing + open items | Interrupt on safety escalation |
| `capture_evidence` | Freeze current camera frame, verify it with a separate vision call, store it | When idle |
| `escalate_to_human` | Mark claim urgent and push to the top of the adjuster queue | Interrupt |

The conversation never blocks waiting for a tool.

### FR-3 Claim pipeline (hybrid LLM + rules)
- FR-3.1 **Extract** (LLM, structured output): claimant, policy #, contact, loss date (ISO), location,
  description, estimated amount, parties, safety facts (present/absent/uncertain), evidence records.
  Every fact cites `source_turn_ids`.
- FR-3.2 **Validate** (code): required fields, date sanity (not future, parseable).
- FR-3.3 **Classify** (LLM): claim type (home water, auto, theft, travel, medical, other) + severity.
- FR-3.4 **Rules engine** (code, config-driven YAML): document requirements per claim type, high-loss
  threshold, policy-period and name-match checks, timing/fraud signals, safety gate.
  Each rule has an ID and writes to an audit trail.
- FR-3.5 **Route** (code): `emergency_escalation` > `special_investigation` > `policy_review` >
  `needs_docs` > `ready_for_adjuster`.
- FR-3.6 **Packet**: adjuster summary, checklist, findings, disclaimer, claimant next message.
- FR-3.7 Results cached per transcript revision; stale runs discarded; one run per claim at a time.

### FR-4 Evidence and trust
- FR-4.1 Evidence status lifecycle: `unknown → missing → planned → available → received`.
- FR-4.2 Only a successful server-side capture can set `received`; LLM output cannot.
- FR-4.3 Each capture stores the image, independent caption, claimant's claim, and `confirmed` flag.
- FR-4.4 Camera text/images are treated as untrusted content (prompt-injection defense).

### FR-5 Safety and guardrails
- FR-5.1 Current injury / unsafe housing / immediate danger → emergency message + escalation.
- FR-5.2 Negated statements ("nobody is hurt") never trigger escalation.
- FR-5.3 Agent never promises coverage, payment, liability, or approval.
- FR-5.4 Output guardrail check on agent transcripts (flag forbidden phrases in eval + logs).

### FR-6 Persistence
- FR-6.1 Claims, turns, extracted facts, rule findings, evidence, and audit events stored in a DB.
- FR-6.2 Evidence images stored on disk/object storage, referenced by ID.
- FR-6.3 Server restart does not lose claims.

### FR-7 Adjuster dashboard
- FR-7.1 Claim queue sorted by severity/route; filter by route and claim type.
- FR-7.2 Claim detail: facts with source-turn highlighting, evidence gallery, findings, audit trail, transcript.
- FR-7.3 Adjuster can override routing with a reason (logged to audit trail).
- FR-7.4 Export packet (Markdown/PDF + evidence ZIP).

### FR-8 Evaluation harness
- FR-8.1 ≥ 50 scripted claim scenarios (text transcripts) with gold labels: fields, claim type, route, safety.
- FR-8.2 `make eval` runs the pipeline on all scenarios and reports field F1, routing accuracy,
  safety recall, guardrail violations, latency, and token cost.
- FR-8.3 Includes adversarial cases: corrections, negations, injected instructions, vague claims, lapsed policy.

### FR-9 Observability
- FR-9.1 Structured logs with claim ID and turn ID.
- FR-9.2 Per-claim metrics: tool latencies, pipeline duration, tokens and estimated cost.
- FR-9.3 PII redaction (phone, email) in logs.

### FR-10 Policy review for adjusters (F13, PRD D14)
Design decisions behind these requirements are in [07-policy-review.md](07-policy-review.md) §9.
- FR-10.1 Five fictional policy wordings with numbered sections (coverage, exclusions, conditions,
  claims), one per product. Every policy record carries a `product` key that selects one wording,
  and wordings are versioned; a review records the wording id and version it read.
- FR-10.2 A policy review is generated when a claim is submitted, and again when the adjuster asks.
  Submission never waits for the model and never fails because of it.
- FR-10.3 The review contains a summary, relevant clauses (section, exact quote, reason), points to
  check, and claimant questions (exact quote, turn ID).
- FR-10.4 The model never writes a quote. It cites a clause and anchors a sentence within it; code
  reads the quote out of the wording file, and a claimant question out of the transcript, so
  displayed text is always the source's own. An anchor that matches nothing in the cited source is
  dropped and logged. Anchors are compared after normalizing whitespace, quote characters, dashes
  and case, and must be at least 20 characters (8 for a question, whose source is one short turn).
- FR-10.5 The review never states a coverage decision or amount; verdict language in the summary is
  replaced by a code-built summary.
- FR-10.6 Only signed-in adjusters can read it. The review is merged in at the adjuster call sites
  only, never inside `session_view`, `build_packet_zip` or the packet markdown, all of which also
  serve the claimant; tests assert the claimant claim endpoint and claimant packet ZIP expose none
  of it.
- FR-10.7 Reviews are stored with the claim (new table, Alembic migration) with status, model,
  prompt version, wording id and version, the pipeline revision they read, and time; every
  generation writes an audit event and a usage row the operations panel counts.
- FR-10.8 If the model is unavailable or the server restarts mid-generation, the claim is still
  submitted; the panel shows "not ready" with a Retry button, and the next adjuster open retries a
  review still pending after a grace period.
- FR-10.9 At most one review is in flight per claim, with a cooldown before a ready review can be
  refreshed again; a concurrent request joins the running one.

## 4. Non-functional requirements
- **Security:** owner cookie per claim session, origin checks, input size caps, per-message-type rate limits,
  session TTL, max live connection duration.
- **Reliability:** tool failures return a recoverable error to the agent; the conversation continues.
- **Code quality:** layered structure (transport / services / domain / rules), typed with Pydantic,
  unit tests for rules, integration tests with mocked models, CI on GitHub Actions.
- **Deployability:** Docker Compose (app + DB); `.env` config; one-command local run.

## 5. Proposed stack

| Layer | Choice |
|-------|--------|
| Realtime voice + vision | Gemini Live API (behind a `RealtimeProvider` interface) |
| Extraction / verification | Gemini Flash with structured output |
| Pipeline orchestration | LangGraph |
| Backend | Python 3.12, FastAPI, WebSockets, Pydantic v2 |
| DB | SQLite in M6 → Turso hosted in M7, fallback Postgres; SQLAlchemy 2.0 async + Alembic (PRD D7) |
| Frontend | React + Vite + TypeScript (claimant call page + adjuster dashboard) |
| Testing / eval | pytest, custom eval runner, Vitest for client audio/state logic |
| DevOps | Docker Compose, GitHub Actions |

## 6. Out of scope (v1)
Real carrier integrations, payments, telephony (Twilio) dialing, multi-language, and generated
incident sketches (optional stretch goal).

## 7. Build plan (milestones)

| # | Milestone | Deliverable |
|---|-----------|-------------|
| M1 | Domain core | Schemas, policy DB seed, rules engine (YAML) + unit tests |
| M2 | Claim pipeline | LangGraph extract → validate → classify → rules → route → packet; runs on text |
| M3 | Eval harness | Scenario set + metrics report (proves M2 quality early) |
| M4 | Live voice | FastAPI WebSocket ↔ Gemini Live, non-blocking tools, barge-in, minimal claimant UI |
| M5 | Camera evidence | Frame capture, independent verification, evidence storage |
| M6 | Persistence + dashboard | DB models, adjuster queue, detail view, routing override |
| M7 | Hardening | Security limits, observability, Docker, CI, README with demo GIF + metrics |
| M8 | Policy review | Policy wordings, review service with code-checked quotes, storage, adjuster panel + packet section, eval metrics |
