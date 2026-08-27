"""Versioned confidence-gate configuration (issue #26, ADR 0003).

confidence = W_FIELD x min(critical field confidences)
            + W_MATCH x (1 - normalized_match_delta)
            + W_POLICY x policy_severity_term

Defaults (documented starting point, NOT tuned): weights 0.4/0.4/0.2 and
tau = 0.85. Phase 5's eval sweep publishes the STP-vs-missed-anomaly ROC
curve and picks tau as a business decision (EVALUATION §5); any change
here is a reviewable diff against this version string.

Only money/payment-critical fields enter the w1 minimum — a low confidence
on a low-risk field (due date, phone) must not tank the score
(ARCHITECTURE §3.5). Missing match input fails safe (term = 0): the gate
never rewards absent evidence.
"""

VERSION = "gate@v1"

TAU = 0.85

W_FIELD = 0.4
W_MATCH = 0.4
W_POLICY = 0.2

# Extraction confidence keys whose minimum forms the w1 term: header money /
# identity fields plus per-line money fields ("line[<no>].<field>" keys).
CRITICAL_FIELDS: tuple[str, ...] = (
    "vendor_name",
    "invoice_number",
    "issue_date",
    "currency",
    "total_amount",
    "tax_total",
    "iban",
    "po_number",
)
CRITICAL_LINE_FIELDS: tuple[str, ...] = ("qty", "unit_price", "line_total")

# w3 term: 1 - sum(penalties), floored at 0. HIGH+ findings never reach the
# gate (routing forces triage), so these mainly discount future MEDIUM/LOW
# findings; CRITICAL covers any config change that lets one through.
SEVERITY_PENALTY: dict[str, float] = {
    "CRITICAL": 1.0,
    "HIGH": 0.6,
    "MEDIUM": 0.3,
    "LOW": 0.1,
}
