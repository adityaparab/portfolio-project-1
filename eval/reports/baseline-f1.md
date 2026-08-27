# Baseline Extraction Field-F1 (issue #19)

First measurement of the real extraction path over the committed Voxel51
corpus — a baseline, not a scorecard. The Phase-5 experiment log (#51)
diffs against this.

## Method

- Corpus: `voxel51-corpus@1`, first **20** images of the committed manifest (limit 20)
- Model: gateway alias `extract-vision` (dev: glm-ocr via native proxy), prompt `extract@v1`
- Comparison: field-level exact match (text: normalized casefold/whitespace; money: Decimal within ±0.01; dates: ISO; line_count: integer equality)
- Escalations (schema-validation failures after retry) count as misses for every labeled field

## Results

| Field | TP | FP | FN | F1 |
|---|---|---|---|---|
| vendor_name | 7 | 0 | 13 | 0.519 |
| invoice_number | 7 | 0 | 13 | 0.519 |
| issue_date | 7 | 0 | 13 | 0.519 |
| total_amount | 0 | 7 | 20 | 0.000 |
| tax_total | 0 | 7 | 20 | 0.000 |
| line_count | 0 | 7 | 20 | 0.000 |

- Images: **20** · escalations: **13** · wall time: 204.8s (~10.2s/image)

## Per quality tier (F1)

| Tier | vendor_name | invoice_number | issue_date | total_amount | tax_total | line_count |
|---|---|---|---|---|---|---|
| A | 0.57 | 0.57 | 0.57 | 0.00 | 0.00 | 0.00 |
| B | 0.50 | 0.50 | 0.50 | 0.00 | 0.00 | 0.00 |

## Reading the baseline

No targets apply here (targets are Phase-5, issue #47). The value is the
delta reference: prompt/model changes in later issues re-run this and
record movement in the experiment log.
## Findings (baseline v0 — curated)

1. **Escalations dominate: 13/20** — glm-ocr frequently violates the JSON
   contract even after the corrective retry (echoes the #15 live finding:
   unquoted keys, string amounts). Tuning hypothesis: stricter few-shot example
   in `extract@v2`, or a text-stronger dev backend for structured output.
2. **Header fields are strong when extraction succeeds** — vendor, invoice
   number, and ISO date all at 0.519 purely due to escalations; within the 7
   successful extractions they matched 7/7 with zero FPs.
3. **Totals and line items are systematically absent** — every successful
   extraction returned `total_amount: null`, `tax_total: null`, `lines: []`.
   The model reads the header block and stops. Hypothesis: the single-pass
   prompt overloads glm-ocr; two-pass extraction (header pass + lines pass)
   is the likely `extract@v2` shape.
4. **Tiers barely discriminate (A 0.57 vs B 0.50)** — consistent with a
   uniform corpus; tier separation becomes meaningful with #45's hard negatives.

Re-run: `uv run python -m invoiceops_agent.eval.baseline --limit 20`
(overwrites the tables above; findings sections are curated by hand).
