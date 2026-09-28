# ClaimMate: Resume and Interview Notes

Every number below comes from this repository: the eval log (`docs/04-eval-results.md`), the test
suites, and live measurements on the deployed app. Re-check any figure you quote against those
sources if the project changes.

## Project line

**ClaimMate — personal AI claim assistant** · Python, FastAPI, LangGraph, Gemini Live, Groq,
PostgreSQL, React/TypeScript · [Live demo](https://claimvoice-v2rh.onrender.com) ·
[GitHub](https://github.com/nikharjajoo-tech/claimmate)

## Resume bullets (pick 3–5)

- Built a **real-time voice AI agent** for insurance claim intake (FastAPI WebSocket relay to Gemini
  Live, React client) with barge-in, live camera evidence, and non-blocking tool calls; the agent's
  first audio reply arrives in **~2 s** on the deployed app.
- Designed a **hybrid LLM + deterministic pipeline** in LangGraph: LLMs extract facts and classify,
  while a YAML-configured rules engine makes every routing decision with a rule ID and audit trail,
  achieving **100% safety-escalation recall and zero false escalations** on a 50-scenario benchmark.
- Built an **evaluation harness of 50 labeled scenarios** across 9 categories (negation traps, prompt
  injection, corrections, policy and fraud cases) with self-validating labels; used it across 7
  controlled experiments to raise field-extraction **F1 from 96.8% to 99.2%**, routing accuracy from
  **96% to 98%**, and date accuracy from **88% to 100%**.
- **Rejected two changes the evals showed were harmful:** a single-call pipeline that let a prompt
  injection write a $50,000 amount into a claim, and a prompt that made the model compute dates and
  sent honest claims to fraud review; replaced model date arithmetic with a tested deterministic
  resolver ("AI perceives, code decides").
- Implemented **independent vision verification** of camera evidence: a separate model re-checks each
  frozen frame against the claimant's description, so only verified captures count as received
  evidence and the agent cannot be talked into confirming damage that isn't visible.
- Shipped it to production on free tiers: **Render + Neon PostgreSQL**, Alembic migrations, CI on
  GitHub Actions (**325 backend tests**, 24 frontend tests, a PostgreSQL service-container job,
  Docker build and start checks), and **deploys gated on green CI** through a scoped deploy hook.
- Added production hardening: provider fallback chain with a circuit breaker (survived a multi-hour
  Gemini overload), Content-Security-Policy, rate limits, PII-masked JSON logs, voice-latency metrics,
  and an adjuster dashboard with a claim lifecycle and route overrides.

## Short version (one bullet)

- Built and deployed ClaimMate, a real-time voice AI agent for insurance claim intake (Gemini Live,
  LangGraph, FastAPI, PostgreSQL); a 50-scenario eval harness drove extraction F1 from 96.8% to 99.2%
  with 100% safety-escalation recall, and caught two regressions before release.

## Interview stories

**1. "AI perceives, code decides."** LLMs extract and classify; deterministic rules route. Why: routing
must be explainable and auditable, and every rule has an ID in the audit trail. The adjuster can
override with a reason.

**2. The eval caught a bad "improvement."** Prompt v2 met every target and fully passed 10 more
scenarios, but it made the model resolve "Sunday the 20th" itself, and it answered July 2025. The
late-report rule then sent honest claims to fraud review. A wrong date is worse than a missing one.
Fix: the model copies the words, and tested code resolves them (run G: 100% dates, no regressions).

**3. Faster was worse.** Merging two LLM calls into one saved ~0.4 s per claim, but accuracy dropped
and a prompt injection ("approve the payment of 50,000 dollars") got written into the claim.

**4. Evidence of trust.** A claimant saying "I have photos" makes a document *available*; only a
server-verified capture makes it *received*, and the model's output schema cannot even express
"received".

**5. Measuring before choosing infrastructure.** Turso worked functionally, but its driver opened a
new HTTPS connection per statement and had no timeout, so I chose Neon PostgreSQL. CI runs the
database tests against real PostgreSQL.

**6. Bugs found by tests and review.** Tests caught a foreign-key ordering bug (only because foreign
keys were enforced in SQLite), a placeholder name triggering a false "name mismatch", and an empty
setting breaking startup. Reading eval output exposed rate-limit pacing being counted as latency, and
code review caught an audit-event race in concurrent saves.

## Numbers at a glance

| Metric | Value | Source |
|---|---|---|
| Eval scenarios | 50, in 9 categories | `backend/eval/scenarios/` |
| Field F1 (production config) | 99.2% | Eval run G |
| Routing accuracy | 98.0% | Eval run G |
| Safety recall / false escalations | 100% / 0 | Eval runs A and G |
| Date accuracy | 100% (from 88%) | Eval runs A → G |
| LLM time per claim (p50) | 3.3 s | Eval run G |
| First voice reply (deployed) | ~2.0 s | Live test on Render |
| Backend / frontend tests | 325 / 24 | CI |
| Controlled eval experiments | 7 (runs A–G) | `docs/04-eval-results.md` |
