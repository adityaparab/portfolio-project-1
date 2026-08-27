# Evaluation Plan — InvoiceOps Agent

Evaluation is the point of this project. Most portfolios ship an app; this one ships an **app plus the measurement system that proves (and improves) it**. Everything below is designed to run headless in CI.

---

## 1. Golden Dataset

### 1.1 Composition (500 invoices)

| Bucket | Count | Source | Purpose |
|---|---|---|---|
| Clean invoices | 350 | Voxel51 base (re-labeled to our schema) + synthetic generator | STP path, extraction accuracy, false-escalation rate |
| Anomalous invoices | 150 | Synthetic generator with injected anomalies | Exception detection (recall/precision per type) |
| Hard negatives | 30 (within clean) | Layout noise: rotation, skew, stamps, faint print, mixed currencies | Robustness; per-tier extraction metrics |

Quality tiers are tagged (`A` clean scan, `B` moderate noise, `C` hard) so accuracy is reported per tier — "98% F1 on easy scans" is a different claim than "98% overall."

### 1.2 Anomaly Catalog (injection is scripted & seeded)

| Code | Anomaly | Prevalence in eval set | Detection mechanism under test |
|---|---|---|---|
| `DUP_EXACT` | Exact duplicate of a paid invoice (same content, renumbered) | 15 | Policy hash check |
| `DUP_NEAR` | Near-duplicate (small edits, e.g., date + total ±0.5%) | 10 | pgvector embedding similarity |
| `PRICE_MM` | Unit price above PO beyond tolerance | 25 | Match3Way deterministic |
| `QTY_MM` | Invoiced qty > received qty | 15 | Match3Way deterministic |
| `MISSING_PO` | No PO reference / PO not found | 20 | Match3Way + policy |
| `BANK_CHANGE` | Vendor bank details differ from master data | 10 | Policy (four-eyes escalation) |
| `CCY_MM` | Currency differs from PO | 8 | Match3Way |
| `TAX_ERR` | Tax computation inconsistent | 12 | Validate (deterministic math) |
| `MATH_ERR` | Line sums ≠ invoice total | 10 | Validate |
| `STALE_PO` | PO closed/expired | 5 | Policy |

Prevalence weights are documented assumptions (industry AP exception literature puts real-world exception rates at 10–25%); the harness makes them parameters, not constants.

### 1.3 Ground truth

Every synthetic invoice carries a machine-generated label file (expected extraction fields, expected route, expected exception codes, expected evidence). Deterministic checks get unit-tested separately (they're code); **the eval measures the whole system as deployed** — including model, prompt, and gate behavior.

### 1.4 Discipline

- Generator is seeded; dataset versioned (`golden/v1.2.0`); held-out split never used for prompt iteration
- Any prompt/model change re-runs the full suite; results archived per version

---

## 2. Metrics

### 2.1 Primary (CI-gated, thresholds block PRs)

| Metric | Definition | Target v1.0 |
|---|---|---|
| **Exception detection recall** | True anomalies escalated (or correctly routed to exception) ÷ all anomalies | ≥ 0.98 |
| **False-escalation rate** | Clean invoices escalated ÷ all clean | ≤ 0.05 |
| **Extraction field F1** | Per-field exact match (normalized: dates ISO, amounts decimal) macro-averaged; money fields reported separately | ≥ 0.95 overall, ≥ 0.97 on money fields |
| **Routing accuracy** | Final route (AUTO/EXCEPTION/REJECT) matches label | ≥ 0.95 |
| **STP rate** | Auto-approved ÷ total (on the clean + tolerable buckets) | ≥ 0.70 |
| **Cost per invoice** | Σ gateway-reported token cost ÷ N | ≤ $0.04 |
| **p95 latency** | Auto-approve path wall-clock | ≤ 45s |

### 2.2 Diagnostic (reported, not gated)

- Per-anomaly-type detection breakdown (confusion per code)
- Extraction F1 **per field** (vendor name vs IBAN vs line items…)
- Per-quality-tier breakdown (A/B/C)
- Confidence calibration curve: predicted confidence vs. actual correctness (reliability diagram) — is the gate's number trustworthy?
- Triage recommendation quality: LLM-graded rubric (correct type? evidence cited? actionable?) — graded by a *different* model via the gateway, itself versioned
- Escalation funnel: where clean invoices get stuck (validate vs match vs gate)

### 2.3 Anti-metric (watch for regressions)

- **Over-escalation drift**: STP creeping up while false-escalation stays flat is good; recall improving because *everything* escalates is bad. The recall/false-escalation pair is always reported together, and the gate ROC analysis (below) keeps τ honest.

---

## 3. Harness Design

```
eval/
├── golden/v1.2.0/           # documents + labels + manifest (hash-pinned)
├── runners/
│   ├── run_pipeline.py      # executes the real stack via docker compose
│   ├── metrics.py           # precision/recall/F1/STP/cost/latency
│   └── calibration.py       # reliability diagrams
├── reports/                 # versioned MD + HTML reports (committed)
└── ci_gate.py               # compares to thresholds + previous version, exits nonzero
```

- Runs the **actual deployed stack** (compose up → push invoices through the API → collect decisions + traces), not a mocked pipeline
- Determinism: seeded data; N=3 runs averaged for latency; recorded-model mode (`--recorded`) for cheap smoke runs
- Output: `reports/v0.3.0.md` with tables + sparkline-style history vs. prior versions, and a PR comment diff (via GitHub Actions)

### CI gate example (workflow semantics)

```
jobs:
  eval-gate:
    steps:
      - compose up platform + gateway
      - python eval/runners/run_pipeline.py --dataset golden/v1.2.0
      - python eval/ci_gate.py --baseline main --max-regress 0.005
```

`ci_gate.py` fails the PR if any primary metric regresses > 0.5% absolute vs. main or falls below the target floor. This is the regression-validation discipline JD2 §1 asks for, made concrete.

---

## 4. Experiment Log (the flagship written artifact)

Each version documents: hypothesis → change → metrics delta → decision. Skel:

| Ver | Hypothesis | Change | Result (recall / false-esc / F1 / STP / $ / p95) | Decision |
|---|---|---|---|---|
| v0.1 | Baseline is enough | Single VLM pass, τ=0.90 fixed | 0.94 / 0.11 / 0.91 / 0.58 / $0.061 / 52s | Baseline established |
| v0.2 | Structured output + few-shot lifts extraction | Pydantic-enforced JSON, 3 few-shot doc types | 0.95 / 0.08 / 0.945 / 0.63 / $0.052 / 47s | Ship |
| v0.3 | Composite confidence gates better than raw | Gate = f(field conf, deltas, severity); τ swept | 0.98 / 0.05 / 0.946 / 0.71 / $0.041 / 44s | Ship + publish ROC |

(Figures above are **planning targets illustrating the format** — the real log fills as builds complete.)

### τ sweep (gate operating point)

Published as a curve: for τ ∈ [0.5 … 0.99], report STP rate, recall, false-escalation. The chosen τ is annotated with the business rationale ("recall floor 0.98 binds at τ=0.88"). This artifact is a conversation generator in interviews: *choosing the operating point is a risk decision, and you can show the data you'd base it on.*

---

## 5. Online / Production Signals (beyond offline evals)

- Confidence distribution monitored live; shift ⇒ eval set refreshed & gate re-swept
- Human-decision feedback loop: when Maria overrides the recommendation, the case is auto-tagged as a candidate label for the next golden version (with review before inclusion)
- Weekly automated report: STP, exception-aging, cost, provider mix — exported to the Grafana annotation stream
- Trace sampling: 100% of exceptions, 5% of auto-approves retained for quality review

---

## 6. Honest-Limitations Section (written into every report)

- Synthetic data ≠ real invoices; prevalence assumptions are stated, not measured
- Triage-recommendation grading uses an LLM judge — its rubric and model version are pinned and its agreement with human graders is spot-checked on 30 samples
- Latency measured on demo infra; absolute numbers are not production claims, method is the claim
