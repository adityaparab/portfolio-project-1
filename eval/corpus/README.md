# Evaluation Corpus — Voxel51 Subset (issue #18)

Dev/baseline corpus: a deterministic, annotated subset of
[`Voxel51/high-quality-invoice-images-for-ocr`](https://huggingface.co/datasets/Voxel51/high-quality-invoice-images-for-ocr)
used for the extraction baseline (issue #19) and later the golden dataset (issue #45).

## Contents

- `manifest.json` — committed: content hashes, source files, quality tiers,
  image stats, and ground-truth label mappings (`invoice`, `items`,
  `subtotal` → `InvoiceExtraction`-shaped labels; dates raw + ISO-normalized;
  currency assumed USD — the corpus is US-formatted and carries none).
- `../../data/voxel51/raw/` — gitignored image bytes, content-addressed as
  `{sha256}.jpg`; re-fetchable from the manifest.

## Reproduce (seeded, idempotent)

```bash
uv run python -m invoiceops_agent.data.voxel51 --count 100
```

Selection is deterministic: annotated samples ordered by `sha256(sample_id)`,
first N — re-running yields a byte-identical manifest (HF cache makes it
network-free after the first run).

## Quality tiers (heuristic v1)

Calibrated on the observed corpus (uniform ~1654px renders, contrast std
26–36): **A** ≥1000px min-side and contrast ≥33 · **B** ≥800px and ≥26 ·
else **C**. Hard negatives (rotations, stamps, faint print) injected in
issue #45 exercise tier C. Manual spot-check: pending — recorded per image as
fields are added.

## License note

No license is declared on the dataset card (checked 2026-08-27). The corpus
is used locally for portfolio evaluation only and is not redistributed; the
committed manifest contains metadata and labels, not image bytes.
