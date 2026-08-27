# Demo Video Script — InvoiceOps Agent

**Target length:** 3:30 · **Audience:** hiring managers & engineers evaluating for Principal/SVP GenAI roles · **Tone:** platform engineering, not product marketing · **Format:** screen recording + narration; on-screen callouts, no talking head.

> Until the real system is live, this script is walked through using the interactive mock ([`../mocks/index.html`](../mocks/index.html)) — the scenes map 1:1 to mock screens.

---

## Scene-by-Scene Storyboard

### Scene 1 — Hook (0:00–0:20)
**Screen:** Dashboard, live counters.
**Narration:**
> "This is InvoiceOps Agent — an agentic invoice-processing platform for Source-to-Pay. It ingests vendor invoices, extracts and validates them, runs a deterministic three-way match against purchase orders and goods receipts, enforces policy, and auto-approves what it's confident about — escalating everything else to a human with a complete evidence package. And every decision — human or machine — lands in an append-only audit ledger."

**On-screen callout:** KPI cards (STP rate, exceptions pending, cost/invoice, p95 latency).

---

### Scene 2 — Intake & the agent run (0:20–1:05)
**Screen:** Intake → click **Process** on an invoice → Agent Run view animates.
**Narration:**
> "An invoice arrives by email. The pipeline is a LangGraph state machine — each node checkpoints, so a crash resumes rather than restarts. Watch the live run: the extraction agent reads the document with a vision model and returns typed fields — every field carries a confidence score and its source region. Validation is deterministic code: schema, tax and line math, IBAN checksum. The three-way match compares invoice, purchase order, and goods receipt — line by line."

**On-screen callouts (appearing as nodes light up):**
- `tool: doc-extract → 14 fields · avg conf 0.97 · bbox provenance`
- `check: line-math OK (12,480.00 = 12,000.00 + 480.00)`
- `match: 8/8 line items · deltas none`
- cost ticker + model name on each LLM call ("routed via LLM Gateway")

---

### Scene 3 — The decision gate (1:05–1:25)
**Screen:** Agent Run continues → Gate node → AutoApprove → toast.
**Narration:**
> "The confidence gate is the system's conscience: a composite of extraction confidence, match deltas, and policy severity. Above threshold — auto-approve, no human. Below — it abstains and escalates. It never guesses. And notice: policy checks are deterministic rules that run *independently* of the model — a high-severity rule failure hard-blocks auto-approval no matter how confident the model is."

**On-screen callout:** `conf 0.96 ≥ τ 0.90 → AUTO · policy 6/6 PASS`.

---

### Scene 4 — Exception & human-in-the-loop (1:25–2:20)
**Screen:** Process a second invoice (price-mismatch scenario) → routes to ExceptionTriage → switch to **Exception Review**.
**Narration:**
> "Here's an invoice where line two prices at nine-point-six percent over the purchase order. The match fails, and the triage agent assembles the evidence: side-by-side comparison, computed deltas, relevant contract terms, and a recommendation — but it cannot approve anything. That's a human decision. Maria opens the queue, sees exactly what differs, checks the cited evidence in the source documents, and approves — within her limit. Her decision, rationale, and the full evidence package are written to the audit ledger."

**On-screen callouts:**
- red-highlighted mismatch cell: `unit price 148.00 vs PO 135.00 (Δ +9.6%)`
- evidence citation click → source region highlighted
- approve → modal: "Decision recorded to audit ledger with your identity, timestamp, and evidence package"

---

### Scene 5 — Audit & provenance (2:20–2:50)
**Screen:** Audit & Traces → open a run's trace → provenance block.
**Narration:**
> "Now the part banks actually care about. For any invoice, I can reconstruct the decision: every node, every model call — with model, prompt-template, and policy *versions in force at the time* — every guardrail evaluation, token count, and cost. If a model changes next week, last month's decisions stay pinned to what was deployed then. This is what mapping OCC 2026-13 and EU AI Act human-oversight and record-keeping expectations to *engineering controls* looks like."

**On-screen callout:** trace timeline; provenance block with version pins.

---

### Scene 6 — Evals & CI gate (2:50–3:20)
**Screen:** Evals tab → version table → PR check running.
**Narration:**
> "None of this is trusted because I say so. There's a 500-invoice golden dataset — 150 with injected anomalies: duplicates, price and quantity mismatches, bank-detail changes. The full pipeline runs against it in CI, on every pull request. Regression in recall, false-escalation rate, extraction F1, or cost — the PR fails. The experiment log shows how each design change moved every metric, including the operating-point sweep for the confidence threshold — because choosing that threshold is a risk decision, and it should be made with data."

**On-screen callout:** eval table v0.1 → v0.3; PR blocked with metric-delta comment.

---

### Scene 7 — Close (3:20–3:30)
**Screen:** One-clip montage: docker compose up → seeded stack boots.
**Narration:**
> "The whole platform — API, orchestrator, Postgres, gateway, tracing — comes up with one command. Repo, architecture decision records, and the full eval reports are linked below."

**End card:** repo URL · portfolio site · `docker compose up` one-liner.

---

## Production Notes

- Record at 1080p+; cursor highlights on click targets; no zooms faster than 400ms
- Use the real system when built (mock for dry runs); keep mock and product visually consistent so the video ages well
- Captions burned in; script narrated at ~150 wpm; total word budget ≈ 540 words
- Companion 60-second cut: Scenes 1, 4, 6 only — for LinkedIn/social
- One-take-per-scene editing; scene transitions via screen-title cards (`1 · Intake`, `2 · The Run`…)
