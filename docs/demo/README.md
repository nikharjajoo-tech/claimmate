# Demo video: a medical reimbursement claim

A 3½-minute demo: a 45-second brief on what ClaimMate is, then the product. You play **Grace Liu**, a claimant asking to be reimbursed for an urgent care
visit under her supplemental medical policy. The call ends with the claim verified, documented,
and waiting for an adjuster.

Everything here is fictional: the insurer, the clinic, the health plan, and Grace. The policy
matches the demo record `MD-4418` in `backend/app/domain/policy_store.py`.

## The claimant and her documents

| | |
|---|---|
| Claimant | Grace Liu, grace.liu@example.com |
| Policy | **MD-4418**, supplemental medical reimbursement, **active** 01/01/2026 – 12/31/2026 |
| Coverage | Out-of-pocket reimbursement up to $7,500/year, $300 annual deductible |
| What happened | Acute sinus infection, urgent care visit on **Monday, September 14, 2026**, Riverbend Urgent Care, Portland. Fully recovered. |
| Money | Billed $980. Her health plan paid $180 and discounted $160. **She paid $640** by card on 09/25. |

| File | What it is | Counts as |
|---|---|---|
| [`0-policy-MD-4418.jpg`](0-policy-MD-4418.jpg) | Policy declarations page | Background only (and the "wrong document" beat) |
| [`1-itemized-bill.jpg`](1-itemized-bill.jpg) | Itemized statement from the clinic | Itemized provider bill |
| [`2-payment-receipt.jpg`](2-payment-receipt.jpg) | $640 card payment receipt | Proof of payment |
| [`3-explanation-of-benefits.jpg`](3-explanation-of-benefits.jpg) | EOB from her primary health plan | Explanation of benefits |

A medical reimbursement claim needs all three documents, and each one counts only after the
vision model has checked the image. So the route starts at **Needs docs** and changes to
**Ready for adjuster** when the third document is accepted.

## Before you record

1. **Wake the site.** Open https://claimvoice-v2rh.onrender.com a couple of minutes early; the
   free tier sleeps after 15 idle minutes.
2. **Two tabs in Chrome:** the claimant page (`/`) and the adjuster page (`/#/adjuster`, signed in
   with the passcode from `.env`). Zoom the page to 110–125% so text reads well on video.
3. **Documents ready:** keep the three document images in a Finder window for **Upload photo**.
   For the camera version, open them on your phone instead.
4. **Recorder:** QuickTime (File → New Screen Recording) or Loom at 1080p. Turn on Do Not Disturb.
5. **Narration goes on afterwards.** Your microphone feeds the agent during the call, so record the
   call with only Grace's lines, then add the voice-over (or captions) while editing.
6. **Dates:** the script says the visit was September 14. Record before mid-December 2026, or the
   late-report rule (TIMING-002, 90 days) will send the claim to fraud review.

## The script

Timings are approximate. **Voice-over** is added in editing. **Grace** lines are spoken to the
agent. The agent speaks freely, so its exact words will vary from take to take.

### 0:00 – 0:45 · What ClaimMate is

**Screen:** a title card, or the live site's landing page, held still. No clicking yet.

> **Voice-over:** "This is ClaimMate.
>
> When something goes wrong — a car accident, a flooded basement, an urgent care visit — the first
> thing you have to do is file a claim. Today that means a phone queue, a form, and emailing
> documents back and forth.
>
> ClaimMate replaces the form with a conversation. You talk, it listens, and while you're still
> speaking it pulls out the facts, checks your policy, and reads the documents you show it.
>
> Three things it deliberately will not do. It won't take your word that a document exists: a
> separate vision model looks at every image, and a document only counts once it's been checked.
> It won't let an AI decide where your claim goes: that's a rules engine, and every decision
> carries a rule ID you can audit. And it will never tell you whether you're covered. That is a
> licensed adjuster's job.
>
> AI perceives. Code decides. People approve.
>
> Here's what that looks like from both sides."

*Trimming to ~20 seconds:* keep the first paragraph, the "you talk, it listens" line, and "AI
perceives, code decides, people approve." The three guardrails then land later in the demo, where
they are shown rather than claimed.

### 0:45 – 1:00 · Cold open

**Screen:** the claimant page, empty.

> **Voice-over:** "Here's Grace. She had an urgent care visit and wants to be reimbursed under her
> supplemental medical policy."

**Screen:** briefly show `0-policy-MD-4418.jpg` (2 seconds), then back to the app.

### 1:00 – 1:40 · The call

**Screen:** click **Talk**, allow the microphone.

> **Grace:** "Hi, I'd like to get reimbursed for an urgent care visit. I'm Grace Liu, policy
> M D 4 4 1 8. It's not an emergency, I'm fully recovered."

*Expected:* the agent confirms and asks what happened. The notebook fills in name and policy;
the policy check shows **MD-4418 · active**.

> **Grace:** "It was Monday, September 14th, at Riverbend Urgent Care in Portland, for a bad sinus
> infection."

*Expected:* date **2026-09-14** and location appear. Claim type: **Medical reimbursement**.

> **Grace:** "After my health plan paid its share, I paid 640 dollars out of pocket by card. I have
> the itemized bill, my card receipt, and the explanation of benefits."

*Expected:* amount **$640**. The three documents show as **available**, not yet received.

> **Grace:** "You can email me at grace.liu@example.com."

> **Voice-over (over this stretch):** "While Grace talks, a background pipeline pulls out the
> facts, checks her policy, and runs the rules. Notice it says 'fully recovered': the safety rules
> read that as no current injury, so nothing is escalated."

### 1:40 – 1:50 · The guardrail

> **Grace:** "So I'll definitely get 340 dollars back, right?"

*Expected:* the agent does **not** promise an amount. It says an adjuster reviews coverage and
payment. (Check this in a rehearsal; if a take says anything like a promise, redo it.)

> **Voice-over:** "The agent never promises coverage or payment. That's a licensed adjuster's call."

### 1:50 – 2:25 · The documents

**Screen:** click **Upload photo** and pick `1-itemized-bill.jpg`, then `2-payment-receipt.jpg`,
then `3-explanation-of-benefits.jpg`. Say each one as you upload it, so the agent follows along:

> **Grace:** "I've uploaded my itemized bill." … "Here's my card receipt." … "And the explanation
> of benefits."

*Expected:* each upload gets a caption describing what's actually in the image. The checklist
ticks off one document at a time, and after the third one the route changes from **Needs docs**
to **Ready for adjuster**.

> **Voice-over:** "Saying 'I have a receipt' isn't enough. A separate vision model reads every
> image, and a document only counts once it's been checked. Once all three are in, the claim is
> ready for an adjuster."

*Camera version (harder, looks better):* click **Show camera**, hold your phone with the document
up to the webcam, and say *"Can you capture my itemized bill?"* Keep it steady and well lit. If a
capture isn't accepted, fall back to Upload photo.

### Optional 15 s beat · The wrong document

Before the real documents, upload `0-policy-MD-4418.jpg` and say *"Here's my payment receipt."*

*Expected:* the caption says it's a policy declarations page, not a receipt; it doesn't count as
proof of payment.

> **Voice-over:** "Show it the wrong document, and it notices."

### 2:25 – 3:15 · The adjuster

**Screen:** click **End call** (the claim is submitted). Switch to the adjuster tab and refresh.

> **Voice-over:** "On the other side, the adjuster sees a queue with urgent claims first. Opening
> Grace's claim freezes the route, so nothing changes under them while they review."

**Screen:** open Grace's claim, then click **Start review**. Slowly scroll through:
- the facts (click one to highlight the sentence it came from in the transcript)
- the evidence gallery with each image's verified caption
- the rule findings and the audit trail

**Screen:** the **Wording review** panel, above the transcript. Give it a few seconds: it is
prepared in the background when the claim is submitted, so it may say "Reading the policy wording…"
and fill in while you watch.

> **Voice-over:** "This part reads the policy itself. Not the one-line summary on her card — the
> full wording for her product. It pulls out the clauses that bear on this claim: that the policy
> pays after her primary health plan has paid its share, the three hundred dollar annual
> deductible, and the three documents she has to send.
>
> Every quote here is lifted out of the policy file by code. The model points at a sentence; it
> never writes one. So it cannot put words in the policy's mouth."

**Screen:** click Grace's question under **The claimant asked** — the transcript turn highlights.

> **Voice-over:** "And the question she asked on the call — whether she'd get her money back —
> is waiting here for the adjuster, in her own words, instead of being answered on the phone."

### 3:15 – 3:30 · The decision

**Screen:** click **Approve claim**, type the reason, click **Approve and close**.

> Suggested reason: "Reimbursable after the $300 annual deductible. Itemized bill, EOB and payment
> receipt all verified."

> **Voice-over:** "The adjuster decides, and says why. That reasoning goes into the audit trail
> next to everything else — what Grace said, what the documents showed, which rules fired."

**Screen:** the green decision banner, then **Download packet**.

### 3:30 – 3:45 · Close

**Screen:** a title card (or the README), with:

> **ClaimMate — your personal AI claim assistant**
> 50-scenario eval · 99.2% field F1 · 98% routing accuracy · 100% safety recall, 0 false escalations
> Wording review: 87.1% clause recall · 0 coverage verdicts reaching an adjuster
> Gemini Live · LangGraph · FastAPI · PostgreSQL · React
> github.com/nikharjajoo-tech/claimmate

## If something goes wrong

| Problem | Fix |
|---|---|
| Page takes a minute to load | The free tier was asleep; wait, then start over. |
| The agent mishears the policy number | Spell it slowly: "M, D, four, four, one, eight." |
| Amount shows $980 instead of $640 | You mentioned the bill total; say only the $640 you paid. |
| An upload doesn't count | Use the JPEG from this folder, not a screenshot of it. |
| Rate-limit or "unavailable" error | Wait a minute and redo the take; free API quotas reset quickly. |
| Wording review stays on "Reading…" | It is one model call; give it ~10 s. If it fails, the panel offers **Retry**. |
| You approved and want another take | A closed claim cannot be reopened. File a fresh claim. |

These lines and documents were tested on 2026-09-29 against a local copy of the app, typing
Grace's lines instead of speaking them. The amount stayed at $640, the policy verified, all three
documents were accepted, the route ended as Ready for adjuster with no rules firing, and the
policy page was rejected as a receipt. The spoken call and the guardrail reply depend on the live
voice model, so do one rehearsal before recording.
