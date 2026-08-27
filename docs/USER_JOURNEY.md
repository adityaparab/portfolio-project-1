# User Journey — InvoiceOps Agent

This document describes the complete user experience: who uses the system, every step they take, what the system does at each step, and how each step appears in the [interactive mock](../mocks/index.html) (screen names in **bold** match the mock's tabs).

> **How to "walk" the journey now:** open `mocks/index.html` in a browser. The mock implements Journeys A, B, and C (D and E partially, read-only) with clickable flows and live-updating state.

---

## Personas

### Maria Chen — AP Analyst (primary user)
- **Context:** clears 60–100 invoices/day; exceptions are her main time sink
- **Pain today:** pulling POs and receipts manually, retyping comparisons into email, chasing vendors with no evidence trail
- **Success:** clear exception queue where each item already has the comparison, the agent's findings, and a recommended action — she decides, not assembles
- **Permissions:** approve ≤ $25K; larger amounts escalate to Dan

### Dan Okafor — Procurement Ops Manager
- **Context:** owns AP SLAs and headcount; answers to Finance on cost and to Audit on control
- **Pain today:** no visibility into where invoices stall; audit requests take days of archaeology
- **Success:** live STP rate, aging exceptions, cost per invoice; confidence that policy-violating invoices *cannot* auto-approve

### Priya Sharma — Internal Audit / Risk & Control
- **Context:** samples invoice decisions monthly; tests the control environment around new AI systems
- **Pain today:** decisions made in email threads and spreadsheets; AI systems are black boxes
- **Success:** for any invoice, a complete provenance record: evidence, tool/model calls with versions, guardrail outcomes, human decisions with identity and timestamps

### Platform Engineer — runs the service (in the demo narrative: you)
- **Success:** traces per run, evals gating every PR, cost/latency dashboards, clean on-call

---

## Journey Map (system overview)

```
Arrive ─► Ingest ─► Extract ─► Validate ─► 3-Way Match ─► Policy ─► Confidence Gate ─►┬─► Auto-approve ─► Archive
                                                                                       └─► Exception triage ─► HITL review ─► Decision ─► Archive
```

---

## Journey A — Happy Path (Straight-Through Processing)

**Actor:** the system (no human). **Mock screens:** **Intake**, **Agent Run** (choose the "Clean invoice" scenario), **Dashboard**.

| # | Step | Actor | System response | What the user sees |
|---|---|---|---|---|
| A1 | Vendor emails invoice PDF to `ap-invoices@…` | Vendor | Ingestion service receives, virus-scan stub passes, SHA-256 computed, **idempotency check** (same hash seen before?) → new record `INV-2088` | **Intake** — row appears in the channel feed with status `RECEIVED` |
| A2 | — | System | LangGraph run starts; state checkpointed per node | **Agent Run** — pipeline lights up node by node |
| A3 | — | System | **Extract**: VLM tool reads the document → typed `InvoiceDraft` (Pydantic); every field carries a confidence score and bounding-box provenance | Live log: `tool: doc-extract → 14 fields, avg conf 0.97` |
| A4 | — | System | **Validate**: schema, ISO dates, IBAN checksum, line-math (Σ lines + tax = total), vendor exists in master data | Log: `check: line-math OK (12,480.00 = 12,000.00 + 480.00)` |
| A5 | — | System | **Match3Way** (deterministic): PO `PO-4417` found; vendor, currency, quantities, unit prices, delivery match GR `GR-9931` | Log: `match: 3/3 documents · 8/8 line items · deltas none` |
| A6 | — | System | **Policy**: spend limit for cost center, approval matrix, duplicate hash check, vendor-bank-detail change check — all deterministic rules | Log: `policy: 6/6 rules PASS` |
| A7 | — | System | **Gate**: composite confidence 0.96 ≥ τ(0.90) → route to auto-approve | Graph edge to `AutoApprove` lights up |
| A8 | — | System | **AutoApprove → Archive**: payment instruction queued (stub); full trace + decision written to the **audit ledger** | Toast: `INV-2088 auto-approved · 38s · $0.031 · trace ↗` |
| A9 | Dan glances at the dashboard | Dan | Counters update; SLA clock stops | **Dashboard** — STP rate ticks up; new row in "Recent decisions" |

**Emotional goal for Dan:** "It just runs, and I can see it running."

---

## Journey B — Exception Path (the core HITL journey)

**Actor:** system, then Maria. **Mock screens:** **Intake** → **Agent Run** ("Price mismatch" scenario) → **Exception Review** → **Dashboard**. This is the journey the demo video spends the most time on.

| # | Step | Actor | System response | What the user sees |
|---|---|---|---|---|
| B1 | Invoice `INV-2094` arrives (scenario: price mismatch on line 2) | Vendor | Ingested like A1 | **Intake** — feed row `RECEIVED` |
| B2 | — | System | Extract + Validate pass | Log entries stream |
| B3 | — | System | **Match3Way** finds PO-4417 but line 2 unit price **invoice 148.00 vs PO 135.00** (+9.6%); total delta +$650 | Log highlights: `match: MISMATCH line-2 price Δ +9.6%` ; graph routes to `ExceptionTriage` |
| B4 | — | System | **ExceptionTriage agent** (LLM through gateway): gathers evidence — pulls PO + GR rows, computes deltas, classifies exception `PRICE_MISMATCH`, checks contract price list, drafts recommendation *"Approve exception (within 10% contract tolerance) or request vendor credit note"* with an evidence citation list | Analysis card populated; confidence 0.93; severity `MEDIUM` |
| B5 | — | System | Exception created, **assigned to Maria** (ownership by vendor segment), SLA timer starts; notification | Maria's queue count badge +1; toast |
| B6 | Maria opens the queue, picks `INV-2094` | Maria | Loads evidence package | **Exception Review** |
| B7 | Maria scans the **3-way comparison table** — invoice vs PO vs GR side by side, mismatches highlighted red, deltas shown | Maria | — | Table with red cell on line-2 price; document thumbnails with the source region highlighted |
| B8 | Maria reads the agent's findings + evidence citations; clicks a citation | Maria | Highlights the exact source region in the document image (grounding) | Right panel shows extracted snippet from PO PDF |
| B9 | Maria checks the audit panel (who/what has touched this invoice so far) | Maria | Chronological trail with identities | Right rail: every event, timestamped |
| B10 | Maria decides: **Approve & Pay** (within her $25K limit) | Maria | Confirmation dialog: *"This decision will be recorded to the audit ledger with your identity, timestamp, and the agent's evidence package"* → confirm | Modal → on confirm, status flips to `APPROVED` |
| B11 | — | System | Decision event appended to ledger; payment stub queued; metrics update (exception cleared, cycle time recorded) | Toast: `Decision recorded · ledger #8841`; **Dashboard** counters move |
| B12 | (Later) Priya audits this invoice | Priya | Full provenance replay | **Audit & Traces** — see Journey D |

**Emotional goal for Maria:** "I spent 90 seconds deciding, with everything in front of me — and my decision is defensible."

### B-variant actions (same screen)
- **Request vendor correction** → generates a draft vendor email with the evidence table attached; invoice state `PENDING_VENDOR`; auto re-match on resubmission
- **Escalate to manager** → routes to Dan (required when amount > Maria's limit or severity `HIGH`); Maria's rationale captured
- **Reject** → requires a reason code; ledger records; vendor notification drafted

---

## Journey C — Fraud/Suspicion Path (duplicate & bank-detail change)

**Actor:** system, then Maria/Dan. **Mock screens:** **Agent Run** ("Duplicate invoice" scenario) → **Exception Review** → **Audit & Traces**.

| # | Step | System response |
|---|---|---|
| C1 | Invoice `INV-2101` arrives; content hash matches `INV-2073` paid 3 weeks ago (different invoice number, same vendor/amount/line items) | **Policy** node fires `DUPLICATE_SUSPECT` (deterministic hash + fuzzy near-duplicate via embedding similarity in pgvector) |
| C2 | Severity set `HIGH`; auto-approve path **hard-blocked** by policy rule regardless of confidence | Graph forces `ExceptionTriage` |
| C3 | Triage agent assembles side-by-side of both invoices, payment history, and a `POSSIBLE_DUPLICATE` finding with both document images |
| C4 | Routed to Dan (HIGH severity), flagged in his queue with a red indicator | |
| C5 | Dan opens, compares the two rendered documents, rejects as duplicate → ledger entry `REJECTED_DUPLICATE`; optional "report vendor" flag feeds a vendor-risk list | |

Another C-scenario: vendor bank details changed vs master data → policy blocks, triage agent surfaces the bank-detail-change form trail; approval requires a second human (four-eyes) — modeled in policy engine as a rule.

**Emotional goal for Dan/Priya:** "The system catches what a tired human wouldn't — and it can't be talked out of blocking."

---

## Journey D — Audit & Provenance (Priya)

**Mock screen:** **Audit & Traces**.

| # | Step | Priya's action | System response |
|---|---|---|---|
| D1 | Monthly sample | Filters decisions by date range, decision type (`AUTO`, `HUMAN`), amount band | List of runs with trace IDs |
| D2 | Picks one | Opens the run's trace timeline | Every node's span: name, duration, model + prompt-template version, tool calls with inputs/outputs, guardrail evaluations, token counts, cost |
| D3 | Asks "why was this auto-approved?" | Opens the **decision provenance** block | The exact rule results, match deltas, confidence value and threshold *at decision time* (point-in-time policy/model versions) |
| D4 | Exports for the audit file | Clicks Export | JSON + PDF package: document, extraction with bounding boxes, comparisons, trail, decision — self-contained |
| D5 | Asks "has the model changed since?" | Opens model registry view | Deployed model/prompt versions with effective dates; every historical decision pinned to the versions in force then |

**Emotional goal for Priya:** "For the first time, an AI system gives me *more* auditability than the manual process, not less."

---

## Journey E — Ops & Platform Health (Dan + Platform Engineer)

**Mock screens:** **Dashboard**, **Evals**.

| # | Step | Actor | System response |
|---|---|---|---|
| E1 | Dan opens dashboard | Dan | KPIs: today's volume, STP rate, exceptions pending + aging buckets, avg exception cycle time, cost per invoice, p95 latency |
| E2 | Dan drills into an aging exception (> 48h) | Dan | Filtered queue; nudges owner |
| E3 | Engineer opens **Evals** | Engineer | Latest eval run per version: extraction F1, exception-detection F1, STP rate, cost, latency; anomaly-type breakdown; CI status |
| E4 | Engineer opens a PR that changes the extraction prompt | Engineer | CI runs unit → integration → **eval gate**; PR shows metric deltas vs. main; blocks on regression beyond tolerance |
| E5 | On-call gets paged: extraction latency p95 breach | Engineer | Grafana link in the alert; trace exemplar attached; runbook entry |

---

## Edge Cases & System Behaviors (what the mock also communicates)

| Situation | Behavior (by design) |
|---|---|
| Confidence below τ but match clean | Escalate anyway ("abstain, don't guess") — false escalations are measured and tuned via evals |
| No PO found | Classification `MISSING_PO`; if under the no-PO threshold, policy may allow with extra approval; otherwise vendor correction |
| Unreadable scan | Extraction returns low-confidence fields; retry with enhanced preprocessing; still low → human data-entry task generated (agent pre-fills its best guesses) |
| LLM provider down | Gateway fails over to secondary model; if all fail, run parks in `DEGRADED` queue for retry — never silently drops |
| Same invoice submitted twice concurrently | Idempotency key (content hash) collapses to one run |
| Human rejects the agent's recommendation | Rationale captured; becomes a labeled example candidate for the eval set (feedback loop) |

---

## Journey-to-Screen Traceability (mock)

| Mock screen | Journeys covered |
|---|---|
| **Dashboard** | A9, E1–E2 |
| **Intake** | A1, B1, C1 |
| **Agent Run** | A2–A8, B2–B5, C1–C3 (animated, both scenarios selectable) |
| **Exception Review** | B6–B11 + variants, C4–C5 |
| **Audit & Traces** | B12/D1–D5 |
| **Evals** | E3–E4 |

The mock is intentionally stateful: processing an invoice through the exception path and approving it updates the dashboard counters, so the whole loop can be demonstrated in under two minutes.
