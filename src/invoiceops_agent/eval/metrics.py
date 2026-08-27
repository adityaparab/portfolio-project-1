"""Eval metrics: field-level exact-match comparison + F1 (issue #19; #53 reuse).

Pure functions — the Phase-5 metrics module (#47) builds on these.
Field F1 convention: per document, a field is TP when the extraction matches
the label under the field's comparison rule; otherwise it counts FN (missed
or wrong) and, when a wrong value was emitted, FP as well.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

MONEY_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class FieldTally:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def f1(self) -> float:
        denom = 2 * self.tp + self.fp + self.fn
        return (2 * self.tp / denom) if denom else 0.0

    @property
    def support(self) -> int:
        return self.tp + self.fn


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.strip().casefold().split()) or None


def as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None


def text_match(extracted: str | None, label: str | None) -> bool:
    return normalize_text(extracted) is not None and normalize_text(extracted) == normalize_text(
        label
    )


def money_match(extracted: Any, label: Any, tolerance: Decimal = MONEY_TOLERANCE) -> bool:
    ex, lb = as_decimal(extracted), as_decimal(label)
    if ex is None or lb is None:
        return False
    return abs(ex - lb) <= tolerance


def date_match(extracted: str | None, label_iso: str | None) -> bool:
    """Extraction must produce ISO dates (schema-validated); label normalized
    at manifest build. Compare directly; None never matches."""
    if extracted is None or label_iso is None:
        return False
    return extracted == label_iso


def lines_count_match(extracted_lines: int, label_lines: int) -> bool:
    return extracted_lines == label_lines


# field name -> comparison function over (extraction, labels)
def compare_field(field: str, extraction: Any, labels: dict[str, Any]) -> bool | None:
    """Return match bool, or None when the field has no label (skip)."""
    label = labels.get(field)
    if field in ("vendor_name", "invoice_number"):
        if label is None:
            return None
        return text_match(extraction, str(label))
    if field in ("total_amount", "tax_total"):
        if label in (None, ""):
            return None
        return money_match(extraction, label)
    if field == "issue_date":
        if labels.get("issue_date_iso") is None:
            return None
        return date_match(extraction, labels.get("issue_date_iso"))
    if field == "line_count":
        return lines_count_match(int(extraction or 0), len(labels.get("lines", [])))
    return None


FIELDS = ("vendor_name", "invoice_number", "issue_date", "total_amount", "tax_total", "line_count")


def tally_documents(documents: list[dict[str, Any]]) -> dict[str, FieldTally]:
    """documents: [{extraction: dict|None, labels: dict}] — None extraction
    (escalated/failed) counts FN for every labeled field, no FP."""
    tallies = {f: FieldTally() for f in FIELDS}
    for doc in documents:
        extraction: dict[str, Any] | None = doc.get("extraction")
        labels: dict[str, Any] = doc.get("labels", {})
        for field in FIELDS:
            matched = compare_field(field, (extraction or {}).get(field), labels)
            if matched is None:
                continue
            if extraction is None:
                tallies[field] = FieldTally(
                    tp=tallies[field].tp, fp=tallies[field].fp, fn=tallies[field].fn + 1
                )
            elif matched:
                tallies[field] = FieldTally(
                    tp=tallies[field].tp + 1, fp=tallies[field].fp, fn=tallies[field].fn
                )
            else:
                tallies[field] = FieldTally(
                    tp=tallies[field].tp, fp=tallies[field].fp + 1, fn=tallies[field].fn + 1
                )
    return tallies
