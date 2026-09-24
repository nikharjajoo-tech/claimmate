# ClaimVoice

A real-time voice AI agent for insurance claim intake. A claimant reports a loss by **talking**; while
the conversation continues, a background pipeline extracts structured facts, verifies the policy,
applies auditable business rules, and routes the claim for a human adjuster.

**Design principle: AI perceives, code decides, humans approve.** LLMs handle speech and fact
extraction; a deterministic rules engine makes every routing decision, each with a rule ID and an
audit trail.

```
Browser (mic PCM16 16 kHz, typed text)
   │ WebSocket
   ▼
FastAPI relay ──────► Gemini Live (voice in/out, transcripts, background tool calls)
   │                      │ lookup_policy · update_claim · escalate_to_human (non-blocking)
   ▼                      ▼
Claim pipeline (LangGraph): extract_facts → classify ∥ lookup_policy → rules engine → packet
   │                                        (Groq gpt-oss or Gemini Flash, with fallback)
   ▼
Claim notebook in the browser: facts, policy, route, documents, rule findings, next question
```

## Quick start

Requirements: Python 3.12+, Node 20+, a [Gemini API key](https://aistudio.google.com/apikey)
(voice), and optionally a [Groq API key](https://console.groq.com/keys) (claim pipeline; higher free limits).

```bash
cp .env.example .env            # then paste your keys into .env

cd backend
make install                    # creates .venv and installs dependencies
make test                       # 215 backend tests, no API calls

cd ../frontend
npm install
npm test                        # frontend tests
npm run build                   # builds the UI into frontend/dist

cd ../backend
make serve                      # http://localhost:8000 serves the API and the built UI
```

Open **http://localhost:8000**, click **Talk**, and allow the microphone. No microphone? Type in the
box; typed messages work with or without a live call.

For frontend development with hot reload, run `make serve` in `backend/` and `npm run dev` in
`frontend/`, then open http://localhost:5173 (Vite proxies `/api` and `/ws` to the backend).

Try saying: *"Hi, I'm Elena Brooks, policy H O 2 0 4 1 7. Our basement flooded last night when the
sump pump failed. Nobody is hurt."* All policies and people in the demo are fictional; see
`backend/app/domain/policy_store.py`.

## Other ways to run the pipeline

```bash
cd backend && source .venv/bin/activate
python -m app.cli examples/basement_flood.txt --markdown     # one transcript -> adjuster packet
python -m app.eval --limit 5                                 # eval smoke run
python -m app.eval --provider groq --models openai/gpt-oss-120b   # full 50-scenario benchmark
```

## Project layout

```
backend/
  app/domain/      claim models, mock policy directory
  app/rules/       deterministic rules engine + rules.yaml
  app/llm/         Gemini + Groq clients: retries, fallback chain, circuit breaker
  app/pipeline/    LangGraph claim pipeline, prompts, packet builder
  app/services/    claim sessions (revision-cached pipeline runs), UI view
  app/live/        Gemini Live config, tools, WebSocket relay
  app/api/         FastAPI app
  app/eval/        eval harness; scenarios in eval/scenarios/*.yaml
frontend/src/      React + TypeScript call page and claim notebook
docs/              PRD, requirements, reference analysis, how it works
```

## Docs

- [PRD](docs/00-PRD.md): problem, users, metrics, architecture, milestones
- [How it works](docs/03-how-it-works.md): input → process → output, with a real run
- [Requirements](docs/02-requirements.md) · [Reference analysis](docs/01-reference-analysis.md)

This is a demo. It does not confirm coverage, liability, or payment, and it has not been reviewed for
production use with real personal data.
