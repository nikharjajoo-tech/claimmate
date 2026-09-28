# PRD: ClaimMate — Your Personal AI Claim Assistant

| | |
|---|---|
| **Owner** | Nikhar |
| **Status** | Live demo; M1–M6 complete, M7 nearly done |
| **Last updated** | 2026-09-25 |
| **Related docs** | [Detailed requirements](02-requirements.md) · [How it works](03-how-it-works.md) · [User workflows](05-user-workflows.md) · [Eval results](04-eval-results.md) |

---

## 1. Summary

ClaimMate lets a policyholder report an insurance loss by **talking to a voice agent** and optionally
**showing the damage on camera**. While the conversation flows, a background pipeline extracts
structured facts, verifies the policy, applies auditable business rules, and routes the claim. Human
adjusters work the result from a **review dashboard** instead of re-interviewing the claimant.

The core design principle: **AI perceives, code decides, humans approve.** LLMs handle speech, vision,
and fact extraction; a deterministic rules engine makes every routing decision; an adjuster can
override any decision with a logged reason.

## 2. Problem

First Notice of Loss (FNOL) is the first contact after a loss, and it is slow and error-prone:

- **Forms don't fit stressed people.** A claimant whose basement just flooded doesn't want a 40-field form.
- **Call-center intake is expensive** and inconsistent between agents.
- **Incomplete intake causes rework.** Missing policy numbers, vague dates, and absent documents mean
  follow-up calls before an adjuster can even start.
- **Urgent cases hide in the queue.** An injury or an unsafe home looks the same as a routine claim
  until someone reads it.
- **Plain chatbots are risky here.** They agree with whatever the claimant says ("yes, I see the crack"),
  promise coverage they can't promise, and make routing decisions nobody can audit.

## 3. Goals and non-goals

### Goals
1. A claimant can complete intake by voice in a single conversation of under 5 minutes.
2. Every claim reaches the adjuster **structured, verified, and routed**, with a reason for each decision.
3. Safety situations (injury, unsafe housing) are **always** escalated, and never raised by a negation.
4. The agent never promises coverage, payment, or liability.
5. Quality is **measured**, not assumed: an eval harness reports accuracy on every change.

### Non-goals (v1)
- Real carrier/policy system integrations (mock directory only)
- Claim payment, settlement, or coverage determination
- Phone-line telephony (browser only)
- Languages other than English
- Production multi-tenant deployment with SSO

## 4. Users

| Persona | Context | Primary need |
|---|---|---|
| **Claimant** — "Elena", homeowner | Basement just flooded; stressed; on her phone | Report quickly by talking, show the damage, know what happens next |
| **Adjuster** — "Ravi", claims handler | Works a queue of 30+ new claims per day | See urgent claims first; get facts, evidence, and reasons without calling back |
| **Operator / Developer** | Maintains the system | Know if a prompt or rule change made things worse; see latency and cost |

## 5. User stories

### Claimant
| ID | Story | Priority |
|---|---|---|
| C1 | As a claimant, I can start a claim by clicking **Talk** and speaking naturally. | P0 |
| C2 | I can interrupt the agent and it stops talking immediately. | P0 |
| C3 | I can type instead of speaking if I have no microphone. | P0 |
| C4 | I can see what the agent has noted about my claim, updating live. | P0 |
| C5 | I can turn on my camera and the agent tells me what it actually sees and saves it as evidence. | P1 |
| C6 | If I mention an injury or danger, I'm told to contact emergency services and my claim is flagged urgent. | P0 |
| C7 | At the end I hear a short summary and what happens next. | P0 |
| C8 | If I disconnect, I can reconnect and continue the same claim. | P1 |
| C9 | I can upload a photo without a live call, and it's verified like a camera capture. | P1 |
| C10 | During a call I can capture the camera view as evidence myself, without waiting for the agent. | P1 |

### Adjuster
| ID | Story | Priority |
|---|---|---|
| A1 | As an adjuster, I see a queue sorted by urgency, filterable by route and claim type. | P0 |
| A2 | I open a claim and see facts, each linked to the transcript line it came from. | P1 |
| A3 | I see captured evidence with the system's caption and a confirmed/unconfirmed label. | P1 |
| A4 | I see every rule that fired, and the audit trail. | P0 |
| A5 | I can override the route with a reason, which is logged. | P1 |
| A6 | I can export the claim packet (Markdown/PDF + evidence ZIP). | P2 |
| A7 | I can move a claim through its lifecycle: in review, awaiting documents, closed. Each change is logged. | P1 |
| A8 | Once I open a claim, its route stops changing automatically, so my decision is never silently overwritten. | P1 |
| A9 | I sign in to the adjuster view with a passcode, so claimants can't reach it. | P1 |

### Operator
| ID | Story | Priority |
|---|---|---|
| O1 | As an operator, I run one command to evaluate the pipeline against labeled scenarios. | P0 |
| O2 | I see latency and token cost per claim. | P1 |
| O3 | Logs never contain raw phone numbers or emails. | P1 |

## 6. User workflows

The full workflows, with diagrams, are in [05-user-workflows.md](05-user-workflows.md) (approved
2026-09-25): voice intake, typed intake, evidence, safety escalation, recovery, adjuster review, and
shipping AI changes. The primary one, voice intake:

| Step | Claimant does | Sees / hears | System does |
|---|---|---|---|
| 1 | Opens the page | Greeting: "Is everyone safe right now?" | Creates a claim tied to a browser cookie |
| 2 | Clicks **Talk** | Mic prompt, then "Live · listening" | Opens the live voice connection |
| 3 | Describes the loss | Words stream into the transcript; agent replies in ~1.5–2 s | Speech to text; agent answers aloud |
| 4 | Keeps talking | Agent confirms the policy mid-conversation | `lookup_policy` runs in the background |
| 5 | Answers follow-ups, shows the camera | Notebook fills in; photos verified; route stamp | Pipeline re-runs after each turn (~2–3 s) |
| 6 | Interrupts at any time | Agent stops speaking at once | Queued agent audio is cut |
| 7 | Ends the call | Two-sentence summary; packet downloadable | Claim moves to `submitted` and into the adjuster queue |

### 6.1 Claim lifecycle

`intake → submitted → in_review ⇄ awaiting_docs → closed`

- **intake:** the claimant is active; the route updates automatically after every new fact.
- **submitted:** the call ended or the claim was idle 30 minutes; waiting in the adjuster queue.
- **in_review:** an adjuster opened it. **The route is frozen** and changes only by adjuster override.
- **awaiting_docs:** the adjuster requested documents.
- **closed:** decision recorded.

Queue order: emergency escalation, special investigation, policy review, needs documents, ready for
adjuster; oldest first within each.

## 7. Features and requirements

Priority: **P0** = required for MVP demo · **P1** = required for v1 · **P2** = stretch

| # | Feature | Priority | Milestone |
|---|---|---|---|
| F1 | Structured claim models + mock policy directory | P0 | M1 ✅ |
| F2 | Deterministic rules engine (YAML config, rule IDs, route precedence, audit trail) | P0 | M1 ✅ |
| F3 | Claim pipeline: extract → classify ∥ policy lookup → rules → packet (LangGraph) | P0 | M2 ✅ |
| F4 | Eval harness: 50 labeled scenarios, self-checking labels, metrics report | P0 | M3 ✅ |
| F5 | Live voice session (Gemini Live), barge-in, transcripts, typed fallback | P0 | M4 ✅ |
| F6 | Non-blocking background tools with interrupt/idle scheduling | P0 | M4 ✅ |
| F7 | Camera evidence capture with independent verification | P1 | M5 ✅ |
| F8 | Persistent storage (claims, turns, evidence, audit) | P1 | M6 ✅ |
| F9 | Adjuster dashboard: passcode sign-in, queue, claim detail, lifecycle actions, route override | P1 | M6 ✅ |
| F10 | Security hardening, observability, PII redaction | P1 | M7 |
| F11 | Docker Compose + CI | P1 | M7 |
| F12 | Incident sketch generation (camera-off illustration) | P2 | — |

Detailed functional requirements (FR-1 … FR-9) are in [02-requirements.md](02-requirements.md).

### 7.1 Routing outcomes

| Route | Meaning | Triggered by |
|---|---|---|
| `emergency_escalation` | Human must review now | `SAFE-001`: injury / hazard present or uncertain |
| `special_investigation` | Needs SIU review | `EVID-001` large unsupported loss, `TIMING-001/002` date anomalies |
| `policy_review` | Policy doesn't check out | `POLICY-001`: not found, lapsed, out of period, name mismatch |
| `needs_docs` | Intake incomplete | `INTAKE-001/002` missing/invalid fields, `DOC-001` documents not received |
| `ready_for_adjuster` | Complete and clean | No routing rule fired (`LOSS-001` flags but doesn't block) |

Precedence is top to bottom; the highest-priority route among fired rules wins.

### 7.2 Evidence trust ladder

`unknown → missing → planned → available → received`

The claimant *saying* they have a photo makes it **available**. Only a server-verified camera capture
makes it **received**. The LLM is structurally unable to mark evidence as received.

## 8. Success metrics

| Metric | Target | How measured |
|---|---|---|
| Field extraction F1 | ≥ 0.90 | Eval harness, labeled scenarios |
| Routing accuracy | ≥ 90% | Eval harness |
| Safety escalation recall | 100% | Eval harness (incl. negation traps) |
| False safety escalations | 0 on negation scenarios | Eval harness |
| Guardrail violations (coverage/payment promises) | 0 | Phrase + LLM-judge check on agent transcripts |
| Voice response latency p50 | < 1.5 s | End of user speech → first agent audio, logged per turn |
| Pipeline latency p50 | < 6 s | Per `update_claim` run |
| Cost per claim | < $0.05 | Token accounting |
| Rules engine test coverage | 100% of rule IDs | pytest |

## 9. System design

### 9.1 Architecture

```
┌──────────────── Browser (React + Vite + TS) ────────────────┐
│  Claimant call page             Adjuster dashboard           │
│  mic PCM16 · camera JPEG · text  queue · detail · override    │
└───────────┬─────────────────────────────────┬────────────────┘
            │ WebSocket /ws/claims/{id}/live  │ REST /api/...
┌───────────▼─────────────────────────────────▼────────────────┐
│ FastAPI                                                      │
│  transport/   live session relay, rate limits, auth cookie   │
│  services/    tool executor, claim service, evidence service │
│  pipeline/    LangGraph claim graph ──┐                      │
│  rules/       deterministic engine ◄──┘  (pure, YAML config) │
│  domain/      models, policy store                           │
│  storage/     SQLModel repositories                          │
└───────┬──────────────────────┬───────────────────┬───────────┘
        │ Gemini Live          │ Gemini Flash      │ SQLite → Postgres
        │ (voice + vision +    │ (extraction,      │ + evidence files
        │  tool calls)         │  classification,  │
        │                      │  frame verify)    │
```

### 9.2 Claim pipeline (LangGraph)

```
extract_facts (LLM) → classify (LLM) → evaluate_rules (code) → build_packet (code)
```
- Runs on the **whole transcript**; LLM output uses structured JSON schemas.
- Cached per transcript revision; a newer claimant turn discards a stale in-flight result.
- Model calls retry with backoff and fall back to a secondary model on 429/503.

### 9.3 Live agent tools

| Tool | Purpose | Result scheduling |
|---|---|---|
| `lookup_policy` | Verify policy number | INTERRUPT if lapsed/not found, else WHEN_IDLE |
| `update_claim` | Run the pipeline; return route + next question | INTERRUPT on emergency, else WHEN_IDLE |
| `capture_evidence` | Freeze + independently verify a camera frame (must be < 10 s old) | WHEN_IDLE |
| `escalate_to_human` | Mark claim urgent | INTERRUPT |

All tools are **non-blocking**, so the agent keeps talking while they run.

### 9.4 Data model (v1)

| Entity | Key fields |
|---|---|
| `Claim` | id, status (`intake`/`submitted`/`in_review`/`awaiting_docs`/`closed`), route, route_frozen, route_override_reason, claim_type, severity, facts (JSON), created_at, updated_at |
| `Turn` | id, claim_id, speaker, text, seq, created_at |
| `EvidenceCapture` | id, claim_id, file_path, caption, claimant_claim, confirmed, document_types, source (agent/claimant/upload) |
| `Finding` | claim_id, rule_id, severity, action, message, pipeline_run_id |
| `AuditEvent` | claim_id, actor (system/adjuster), action, detail, created_at |
| `PipelineRun` | id, claim_id, revision, latency_ms, tokens_in/out, model, status |

### 9.5 API surface (v1)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/claims` | Start a claim session |
| GET | `/api/claims/{id}` | Claim state (facts, route, checklist, evidence) |
| POST | `/api/claims/{id}/messages` | Typed turn (non-live fallback) ✅ |
| POST | `/api/claims/{id}/evidence` | Upload a photo; verified like a capture ✅ |
| GET | `/api/claims/{id}/evidence/{capture_id}` | Evidence image (owner only) ✅ |
| GET | `/api/claims/{id}/packet` | Export packet (Markdown today; ZIP with photos in M6) ✅ |
| WS | `/ws/claims/{id}/live` | Live audio/video/text relay ✅ |
| POST | `/api/adjuster/login` | Passcode sign-in (M6) |
| GET | `/api/adjuster/claims?status=&route=&type=` | Adjuster queue (M6) |
| GET | `/api/adjuster/claims/{id}` | Claim detail: facts with sources, evidence, findings, audit, transcript (M6) |
| POST | `/api/adjuster/claims/{id}/status` | Lifecycle change: in_review, awaiting_docs, closed (M6) |
| POST | `/api/adjuster/claims/{id}/override` | Override route with a required reason (M6) |
| GET | `/api/health` | Health + model config ✅ |

WebSocket messages, client → server: `audio`, `audio_end`, `text`, `camera`, `video`, `capture`, `close`;
server → client: `ready`, `transcript`, `audio`, `interrupted`, `tool`, `state`, `error`.

## 10. AI safety and guardrails

| Risk | Mitigation |
|---|---|
| Agent agrees with a false claim ("you see the crack?") | Frame re-verified by a separate model call; `confirmed=false` unless clearly visible |
| LLM fabricates evidence | Trust ladder: only server captures mint `received` |
| Negation read as danger ("nobody hurt") | Structured present/absent/uncertain safety facts; tested |
| Coverage/payment promises | System prompt rule + transcript guardrail check in evals |
| Prompt injection via camera text or transcript | Content labeled untrusted; app state sent as labeled notices |
| Hallucinated facts | Every fact cites `source_turn_ids`; agent turns are context, not facts |
| Opaque decisions | All routing by rule ID in code; full audit trail; adjuster override |

## 11. Security and privacy

- Session ownership cookie (HttpOnly, SameSite=Strict); origin checks on WebSocket.
- Per-message-type rate limits; payload size caps; session TTL; max live duration.
- API keys only in server environment (`.env`, git-ignored).
- PII (phone, email) redacted in logs; demo uses fictional data only.
- Free-tier Gemini may use inputs for product improvement, so no real personal data.

## 12. Tech stack

Python 3.12+ · FastAPI · WebSockets · Pydantic v2 · LangGraph · google-genai (Gemini Live + Flash) ·
SQLAlchemy 2.0 async + Alembic (SQLite → Turso, fallback Postgres; see D7) · React + Vite + TypeScript · pytest · Vitest · Docker Compose · GitHub Actions

## 13. Milestones

| # | Milestone | Scope | Status |
|---|---|---|---|
| M1 | Domain core | Models, policy store, rules engine, 37 tests | ✅ Done |
| M2 | Claim pipeline | LangGraph graph, Gemini client with retry, fallback chain + circuit breaker, packet builder, CLI; 53 tests | ✅ Done |
| M3 | Eval harness | 50 labeled scenarios in 9 categories, label consistency checks, oracle test, report + targets; 173 tests. Full baseline run pending free-tier quota | ✅ Built |
| M4 | Live voice | WebSocket relay to Gemini Live, 3 non-blocking tools, revision-cached sessions, typed mode, Groq pipeline provider, React call page; 215 backend + 15 frontend tests; verified end to end against real Gemini Live (first audio 1.5–2.1 s) | ✅ Done |
| M5 | Camera evidence | Live camera frames to the agent, `capture_evidence` tool, manual capture and photo upload, independent vision verification (Groq qwen3.8-27b → Gemini fallback), evidence gallery and packet section | ✅ Done |
| M6 | Persistence + adjuster dashboard | SQLite via SQLAlchemy 2.0 async + Alembic (migrations applied at startup, drift-tested); incremental saves with derived audit events; claim lifecycle with route freeze; passcode sign-in (signed 8-hour cookie, rate-limited); queue, claim detail with fact-to-transcript highlighting, status actions, override; packet ZIP with photos. Verified on the live stack, including a server restart. 260 backend + 23 frontend tests | ✅ Done |
| M7 | Hardening + ship | Masked JSON logs, voice latency, operations panel, security headers and limits, Docker + CI (SQLite and PostgreSQL), Turso test (D7), Neon PostgreSQL (D9), **live on Render free tier: https://claimvoice-v2rh.onrender.com** (verified end to end: typed claim, adjuster review on Neon, live voice via Gemini). Remaining: demo video | 🔄 Nearly done |

## 14. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Free-tier limits (20 requests/model/day) / 503 overload | High (hit in M2 and M3) | Retry, 3-model fallback chain, circuit breaker, skip on 429; eval pacing + `--resume`; billing for full runs |
| Preview model names change | Medium | Model IDs in config, not code |
| Live API latency on slow networks | Medium | Measure per-turn; small audio chunks; show "listening/thinking" state |
| Pipeline cost from re-running on every turn | Medium | Revision cache; debounce; incremental extraction later |
| Scope creep (dashboard, sketches) | Medium | P0/P1/P2 priorities; sketches deferred |

## 15. Decisions and open questions

### Decided

| # | Decision | Date |
|---|---|---|
| D1 | The pipeline runs after every claimant turn **and** when the agent calls `update_claim`, cached per transcript revision | 2026-09-24 |
| D2 | Claim lifecycle: `intake → submitted → in_review ⇄ awaiting_docs → closed` | 2026-09-25 |
| D3 | The automatic route freezes once an adjuster opens a claim; it changes only by override with a reason | 2026-09-25 |
| D4 | Adjuster sign-in: one shared demo passcode | 2026-09-25 |
| D5 | Each intake is one claimant session in v1; returning with a claim reference code is v2 | 2026-09-25 |
| D6 | Prompt and model changes ship only after beating the current version on the 50-scenario eval | 2026-09-25 |
| D7 | Storage: local SQLite in M6 (SQLAlchemy 2.0 async + Alembic; evidence photos as files). Hosted database: **Turso** (SQLite-compatible), pending an M7 compatibility test that runs the full test suite through its `turso_serverless` driver; fallback PostgreSQL | 2026-09-25 |
| D7 result | Turso compatibility test (2026-09-26): **the service is compatible** (SQLAlchemy through an aiosqlite bridge, Alembic migrations, and claim saves all worked), but **the Python driver is not production-ready**: `turso_serverless` 0.1.0 opens a new HTTPS connection per statement (~0.45 s each, so a claim save takes seconds) and sets no request timeout (a stalled request hung the connection twice). The adapter stays in the code as experimental and opt-in. Resolved by D9 | 2026-09-26 |
| D8 | Keep the two-call (split) pipeline. Eval run C: one call was ~0.4 s faster per claim but less accurate (27 vs 32 scenarios fully passed) and recorded an injected $50,000 amount; see [eval results](04-eval-results.md) | 2026-09-26 |
| D9 | Hosted database: **PostgreSQL on Neon** (free tier), via asyncpg. Local development keeps SQLite; CI runs the database tests on both SQLite and a PostgreSQL service container. Turso stays experimental (see D7 result) | 2026-09-26 |
| D10 | Prompt v2 not promoted despite meeting all targets (run D: 42/50 fully passed vs 32) because model-computed dates sent two honest claims to fraud investigation. Next: v3 with deterministic date resolution | 2026-09-27 |
| D11 | Product renamed from ClaimVoice to **ClaimMate — "Your personal AI claim assistant"**, with an original gradient speech-bubble-and-sparkle logo. Internal identifiers (`CLAIMVOICE_*` settings, cookies) are unchanged to keep existing configuration and the live deployment working | 2026-09-27 |
| D12 | **Prompt v3 is the default.** Eval run G (gpt-oss-120b): all targets met; F1 99.2% (vs 96.8%), routing 98.0% (vs 96.0%), dates 100% (vs 88%), 42/50 fully passed (vs 32), zero hallucinations and false escalations; +0.4 s LLM time per claim | 2026-09-28 |
| D13 | Hosted demo on **Render's free tier** (Singapore, next to Neon). Deploys run from CI through a service-scoped deploy hook after all tests pass; no Render–GitHub connection and no stored account API key | 2026-09-27 |

### Open

1. PDF export in v1, or Markdown + ZIP only? (Default: Markdown + ZIP; PDF is P2.)
