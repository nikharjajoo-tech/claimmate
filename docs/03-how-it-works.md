# How ClaimMate Works: Input → Process → Output

This walks through what a person does, what the system does behind the scenes, and what comes
out, using a real run of the pipeline. Section 4 covers the eval harness the same way.

> **Status:** the pipeline (sections 2–3) and eval harness (section 4) are built and runnable today.
> The voice/camera call page (section 1) arrives in M4–M5; until then, input is a typed transcript.

---

## 1. The claimant's experience (target product)

| Step | The person does | They see / hear |
|---|---|---|
| 1 | Opens the page, clicks **Talk** | Agent: *"I can start your claim while we talk. Is everyone safe?"* |
| 2 | Speaks naturally: *"Yes, nobody's hurt. I'm Elena Brooks, policy H O 2 0 4 1 7. Our basement flooded last night…"* | Live transcript; the claim notebook starts filling in |
| 3 | Keeps talking (the agent never pauses to "process") | Agent: *"Thanks Elena, I found your homeowners policy."* ← policy lookup finished in the background |
| 4 | Turns on the camera, points it at the wet carpet | Agent: *"I can see standing water across the carpet. I've added that photo."* |
| 5 | Answers the agent's remaining questions | The checklist ticks off; missing items shown in red |
| 6 | Hangs up | A short summary of what happens next. The claim appears in the adjuster's queue |

**The adjuster** (M6) opens their queue, sees the claim sorted by urgency, and reads the packet
from section 3 with facts, evidence, rule findings, and audit trail. They can override the route
with a reason.

---

## 2. What happens behind the scenes

Each time the claimant says something new, the conversation so far runs through the pipeline:

```
 INPUT: conversation turns (+ camera captures)
   │
   ▼
 ① extract_facts      Gemini reads the whole conversation → structured ClaimFacts (JSON)
   │                  every fact cites the turn it came from
   ├──────────────────────────┐
   ▼                          ▼
 ② classify (Gemini)     ③ lookup_policy (code)       ← run in parallel
   claim type + severity    policy directory check
   │                          │
   └────────────┬─────────────┘
                ▼
 ④ evaluate_rules (code)  deterministic rules → route + findings + document checklist
                │
                ▼
 ⑤ build_packet (code)    adjuster packet + the single best next question
                │
                ▼
 OUTPUT: route, packet, next question → spoken by the agent, shown to the adjuster
```

Only steps ① and ② use AI. **Every routing decision is made by code**, so it is repeatable and explainable.

---

## 3. Worked example (real run)

### Input
`backend/examples/basement_flood.txt`, one line per turn:
```text
AGENT: I can start your claim while we talk. First, is everyone safe?
CLAIMANT: Yes, everyone's fine, nobody was hurt. I'm Elena Brooks, policy H O 2 0 4 1 7.
AGENT: Thanks Elena. What happened?
CLAIMANT: Last night our sump pump failed during the storm and the finished basement in our
          Denver house flooded. The carpet is soaked and the bottom of the drywall is wet.
AGENT: I'm sorry. Do you have any photos of the damage?
CLAIMANT: Yes, I took photos on my phone before we moved anything. I don't have a contractor
          estimate yet, I'm calling one tomorrow.
AGENT: What's the best way for the adjuster to reach you?
CLAIMANT: My cell, 720-555-0148. I'd guess the damage is around 9,000 dollars.
```

### Run it
```bash
cd backend
source .venv/bin/activate
python -m app.cli examples/basement_flood.txt --today 2026-09-24            # summary
python -m app.cli examples/basement_flood.txt --today 2026-09-24 --markdown # full packet
```

### Step ①: extracted facts (Gemini)
| What the claimant said | What was extracted | Why it matters |
|---|---|---|
| "policy H O 2 0 4 1 7" | `HO20417` | Spoken letters/digits normalized; matches `HO-20417` |
| "Last night" | `2026-09-23` | Relative date resolved against the reference date |
| "nobody was hurt" | `injury: absent` | A denial is recorded as absent, **not** as an injury |
| "I took photos on my phone" | `damage_photo: available` | Claimant *has* it; not yet *received* by us |
| "calling one tomorrow" | `repair_estimate: planned` | Future intent is tracked separately |
| "around 9,000 dollars" | `9000.0` | |

### Steps ②–③: classify and verify policy (parallel)
- Classification: `home_water_damage`, severity `high`
- Policy `HO-20417`: found, **active**, holder **Elena Brooks** (name matches), loss date inside the policy period

### Step ④: rules
```
DOC-001  Photos of damaged areas before cleanup not yet received (available).
DOC-001  Water mitigation or drying invoice not yet received (unknown).
DOC-001  Repair estimate or contractor assessment not yet received (planned).
→ ROUTE: needs_docs
```
No safety rule (`SAFE-001`) fired, because "nobody was hurt" was correctly understood.

### Step ⑤: output
**The claimant hears** the next question:
> *"Do you have the water mitigation or drying invoice? You can show it on camera."*

**The adjuster gets** the packet (abridged):
```markdown
# Claim Intake Packet
**Route:** Needs Docs
**Claim type:** Home Water Damage
**Policy:** HO-20417: Homeowners (HO-3) (active)

## Handoff summary
Elena Brooks reported a home water damage loss at Denver house on 2026-09-23. Sump pump failed
during a storm and the finished basement flooded… Estimated loss: $9,000.

## Documents
- [ ] Photos of damaged areas before cleanup (available)
- [ ] Water mitigation or drying invoice (unknown)
- [ ] Repair estimate or contractor assessment (planned)

## Rule findings
- `DOC-001` [medium] Water mitigation or drying invoice not yet received (unknown).
…
This is an intake triage packet. It does not confirm coverage, benefits, liability, or payment.
```

**If the claimant then shows the photo on camera**, the capture is verified and the photo becomes
`received`. When all documents are received, the route changes to `ready_for_adjuster`.

### What would change the route
| If the claimant had said… | Rule | Route |
|---|---|---|
| "My husband slipped and hurt his back" | `SAFE-001` | `emergency_escalation` |
| A policy number that doesn't exist | `POLICY-001` | `policy_review` |
| "This happened back in May" | `TIMING-002` (> 90 days) | `special_investigation` |
| "$30,000 of damage" with no photos | `EVID-001` | `special_investigation` |

---

## 4. Measuring quality: the eval harness

The harness answers one question: *if we change a prompt, model, or rule, did the system get better or worse?*

### Input: labeled scenarios
50 conversations in `backend/eval/scenarios/*.yaml`, each with the **gold answer** a human
expert would give:

```yaml
- id: ng-02
  title: Shaken up but not injured
  turns:
    - claimant: "Marcus Webb, AU-55830. I got sideswiped on I-25 in Denver on Tuesday, September 22nd."
    - agent: "Are you hurt?"
    - claimant: "I'm not injured, just shaken up. ... I have photos. ... marcus.webb@example.com."
  expected:
    claim_type: auto_collision
    route: needs_docs                     # must NOT be emergency_escalation
    facts:
      policyholder_name: Marcus Webb
      date_of_loss: "2026-09-22"
      ...
    safety: [{category: injury, status: absent}]
    evidence: {damage_photo: available}
    rules_not_fired: [SAFE-001]
```

| Category | # | Tests |
|---|---|---|
| happy_path | 8 | Every claim type; two fully documented claims reach `ready_for_adjuster` |
| safety_escalation | 7 | Injury, electrical, unsafe housing, fire, uncertain mold, third party, late reveal |
| negation_trap | 5 | "Nobody hurt", "no sewage", "no fire, just toast" must not escalate |
| correction | 6 | Latest correction wins: date, policy #, amount, spelling, city, evidence |
| policy_issue | 5 | Lapsed, cancelled, unknown, out of period, name mismatch |
| fraud_timing | 5 | Late report, unsupported large loss, impossible dates, 85-day boundary |
| incomplete | 5 | Must leave blanks blank; no invented values |
| adversarial | 5 | Prompt injection (voice and camera), agent-supplied facts, hypotheticals |
| evidence | 4 | "I already emailed it" ≠ received; partial and irrelevant captures |

**Labels are checked automatically.** `tests/test_eval_labels.py` runs every gold answer through
the real rules engine. If a label's route doesn't follow from its own facts, the test fails, so a
mislabeled scenario can't silently skew the metrics.

### Process
```bash
cd backend && source .venv/bin/activate
python -m app.eval --limit 5                          # quick smoke run
python -m app.eval --models gemini-3-flash-preview    # full run, one model for all 50
python -m app.eval --category safety_escalation       # one category
python -m app.eval --resume eval/results/<run>        # finish an interrupted run
python -m app.eval --check                            # exit 1 if a PRD target is missed (CI)
```
For each scenario the harness runs the **real** pipeline, then scores every field as one of:
`correct_value`, `correct_blank`, `hallucinated` (invented a value never said), `missed`, or `wrong_value`.

### Output
`backend/eval/results/<timestamp>/`:
- `report.md`: the human-readable report (below)
- `summary.json`: all metrics, for tracking over time
- `results.jsonl`: one line per scenario with predicted vs expected values

The report contains:
1. **Targets** from the PRD: F1 ≥ 90%, route accuracy ≥ 90%, safety recall 100%, 0 false escalations, each ✅/❌
2. **Extraction:** micro precision/recall/F1 and per-field accuracy
3. **Routing:** per-route recall and a confusion matrix (so "always say needs_docs" can't hide behind a 62% baseline)
4. **Per category** pass rates: shows *where* the system is weak
5. **Latency / reliability / cost:** p50/p95, retry and fallback rates, tokens, estimated cost per claim
6. **Failures:** exactly what went wrong in each failing scenario

### The improvement loop (real example)
The first smoke run reported:
```
### hp-01: Basement flood, spelled-out policy number, relative date
- `reported_date` hallucinated: expected `not specified`, got `2026-09-24`
```
The model treated *today's call* as the "reported date". The fix was one line in the extraction
prompt ("only when the claimant says they already reported this loss earlier"). The next eval run
confirms the fix and checks that nothing else regressed. That loop of **measure, fix, re-measure**
is what the harness is for.

### Free-tier note
Google's free tier allows **20 requests per model per day**, and a full run needs ~100
(50 scenarios × 2 calls). On the free tier, run in daily batches with `--resume`, which skips
scenarios already scored; or enable billing, where a full run costs a few cents.
