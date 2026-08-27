"""Versioned near-duplicate configuration (issue #23).

Threshold semantics: two invoices are near-duplicates when cosine SIMILARITY
>= NEAR_DUP_SIMILARITY_THRESHOLD. pgvector's ``<=>`` operator yields cosine
DISTANCE (= 1 - similarity), so the query keeps rows with distance
STRICTLY below ``1 - threshold`` — similarity exactly at the threshold is
NOT a hit (fail-open at the boundary: an exactly-at-threshold pair is not
accused of duplication).

0.90 was calibrated on the hashing-trick fallback (salient-field token
overlap: a one-field edit on a typical invoice lands ~0.92-0.96, distinct
invoices land < 0.3); revisit with real `nomic-embed` vectors in #45's eval.
"""

VERSION = "neardup@1"

NEAR_DUP_SIMILARITY_THRESHOLD = 0.90
TOP_K = 5  # max near-dup candidates surfaced as evidence
