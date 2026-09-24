# Reference Project Analysis: Insurance Claim Live Agent Team

Source: [awesome-llm-apps/voice_ai_agents/insurance_claim_live_agent_team](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team)
Size: ~5,300 lines (Python ~3,300, JS ~680, CSS ~890, HTML ~110)

## 1. What it does

A voice-first **FNOL (First Notice of Loss)** intake app. A claimant talks to a live voice agent
(and optionally shows their camera). While the conversation continues, a background "claim team"
extracts structured facts, verifies the policy, applies deterministic business rules, and builds
an adjuster handoff packet (Markdown + photos + sketch, downloadable as a ZIP).

## 2. Architecture

```
Browser (mic PCM16 @16kHz, camera JPEG @1fps, typed text)
   │  WebSocket /ws/live  (JSON messages: audio | video | text | camera_state | close)
   ▼
FastAPI server (server.py)  ── owns session state, transport, tool execution
   │  bidirectional stream
   ▼
Gemini Live (voice-to-voice, vision, transcription, function calling)
   │  NON_BLOCKING tool calls
   ├── lookup_policy        → policy_directory.py (mock policy admin system)
   ├── sync_claim_packet    → run_claim_workflow() → ADK SequentialAgent graph
   ├── pin_evidence_photo   → freeze frame → independent Gemini Flash verification
   └── draw_incident_sketch → Gemini image model
   │  FunctionResponse scheduling: INTERRUPT (safety/lapsed policy) | WHEN_IDLE
   ▼
Server pushes {transcript, tool, state, audio, interrupted} events → UI "notebook" re-renders
```

### The claim graph (agent.py) — hybrid LLM + deterministic pipeline

| # | Node | Kind | Output |
|---|------|------|--------|
| 1 | NormalizeClaimNarrative | LLM (structured output) | `ClaimNarrative` |
| 2 | ValidateRequiredClaimFields | Python | `FieldValidation` |
| 3 | ClassifyClaimTypeAndSeverity | LLM (structured output) | `ClaimClassification` |
| 4 | ApplyCoverageAndEvidenceRules | Python | `CoverageEvidenceDecision` |
| 5 | GenerateDocumentChecklist | Python | `DocumentChecklist` |
| 6 | FraudSignalAndSafetyGate | Python | `FraudSafetyGate` |
| 7 | FinalClaimIntakePacket | Python | `ClaimIntakePacket` (+ Markdown) |

Only 2 of 7 steps use an LLM. Everything that decides routing is deterministic and auditable.

## 3. Key design patterns (the real learnings)

1. **LLM for perception, code for decisions.** LLMs extract and classify; Python rules decide
   routing (`needs_docs`, `ready_for_adjuster`, `special_investigation`, `emergency_escalation`,
   `policy_review`). Every rule has an ID (`DOC-001`, `SAFE-001`, `TIMING-002`…) and lands in an audit trail.
2. **Non-blocking tools + scheduled responses.** The voice never pauses for a tool. Results come back
   `WHEN_IDLE`, except safety/lapsed-policy results which `INTERRUPT` the agent.
3. **Evidence trust levels.** Documents are `unknown → missing → planned → available → received`.
   Only the server's capture registry can mint `received` (`prepare_claim()` downgrades any LLM-claimed
   `received`). The claimant *saying* they have a photo ≠ evidence.
4. **Independent verification of vision.** The live agent's caption is not trusted; the frozen frame is
   re-described by a separate model call and checked against the claimant's claim → `confirmed` flag.
   Counters sycophancy ("you can see the big crack, right?").
5. **Grounded extraction.** Turns carry IDs; facts carry `source_turn_ids`. Agent turns are context, not
   facts. Latest correction wins. Negations ("no one is hurt") are recorded as `absent`, not injuries.
6. **Revision-keyed caching + concurrency control.** Workflow result cached per claimant-turn revision;
   a per-session `asyncio.Lock` prevents duplicate extraction; stale results are discarded if the
   revision moved during the run. Sketch requests use a revision counter so a slow old request can't
   overwrite a newer correction.
7. **Prompt-injection hygiene.** Camera text and documents are marked as untrusted content; app state
   (camera on/off) is injected as labeled system notices, not claimant speech.
8. **Guardrails in domain language.** Never promise coverage/payment/liability; packet carries a disclaimer.
9. **Operational hardening.** Loopback-only, origin checks, owner cookie, per-type rate limits (sliding
   windows), message size caps, session TTL (30 min), live connection cap (20 min), task cancellation on reset.
10. **Voice-transcript normalization.** Policy numbers normalized for O-vs-0 and spacing confusion.

## 4. Weaknesses / gaps (where a new project can go further)

| Gap | Reference behavior | Opportunity |
|-----|-------------------|-------------|
| Persistence | All in memory; restart loses everything | Database-backed claims + evidence storage |
| Human side | Packet is a ZIP download; no adjuster UI | Adjuster review queue / dashboard with approve/override |
| Evaluation | Regression tests mock the models; no quality metrics | Offline eval harness: extraction F1, routing accuracy, safety recall on scripted scenarios |
| Observability | Tool durations in UI only | Structured traces, per-turn latency (TTFB), token/cost tracking |
| Cost | Re-runs full LLM extraction on every claimant turn | Incremental extraction / debounce |
| Code structure | `server.py` is a 1,080-line monolith; imports private `_positive_safety_concerns` | Layered modules (transport / services / domain / rules) |
| Deployment | Explicitly local-only | Dockerized, auth, deployable |
| Vendor lock-in | Gemini + Google ADK only | Provider interface for the realtime model |
| Rules | Hard-coded in Python dicts | Rules config (YAML) + unit-tested rule engine |
| PII | Raw transcripts and phone/email in packet | PII redaction in logs / exports |

## 5. Tech stack (reference)

Python 3.12 · FastAPI · Uvicorn · WebSockets · Pydantic v2 · Google ADK (SequentialAgent, LlmAgent,
custom BaseAgent) · google-genai (Live API, structured output, image gen) · vanilla JS (Web Audio API,
ScriptProcessor → PCM16 resample, getUserMedia, canvas JPEG capture) · Node `vm` for client tests.
