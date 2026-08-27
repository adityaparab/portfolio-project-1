---
name: observability
description: Instrumentation for InvoiceOps — OpenTelemetry spans per graph node and tool call, Langfuse LLM tracing, Prometheus metrics + Grafana dashboards, structured logging, cost telemetry from LiteLLM spend logs. Use whenever adding tracing/metrics, debugging with traces, or building dashboards.
---

# Observability patterns (OTel + Langfuse + Prometheus/Grafana)

Setup lives in `src/obs/`. `docs/ARCHITECTURE.md` §8 is the reference; the goal is **every decision reconstructible** — a trace is the technical half of auditability.

## Tracing

- **One span per graph node** and per significant tool call (ERP query, policy check, storage read/write). Span names are stable identifiers (`graph.extract`, `tools.match3way`) — dashboards and alerts key off them, so renames are breaking changes: grep before renaming.
- Context: `run_id` (and `invoice_id` when known) set as span attributes at run start; extractors propagate via context, not manual passing.
- LLM calls get both an OTel span and a **Langfuse** trace (via LiteLLM callbacks) carrying alias, model served, prompt/model versions, tokens, latency, cost. Correlation id links the two systems.
- Exporters configured from settings (OTLP endpoint, sample ratio); local dev exports to the Compose collector, tests export to memory (assert on spans in integration tests where useful).

## Metrics

- Prometheus via `GET /v1/metrics`. Counter/histogram names follow `invoiceops_<noun>_<verb>` with unit suffixes on histograms (`invoiceops_invoice_latency_seconds`).
- Core signals to keep alive: invoices processed by route (auto/exception/reject), gate confidences (histogram), exception types, node latencies, LLM tokens/cost per alias (LiteLLM spend logs as the billing-grade source), queue age.
- Dashboards provisioned as code in `deploy/grafana/` (dashboards + datasource, no click-ops edits) — p95 latency, STP rate trend, cost per invoice, aging exceptions.

## Logging

- Structured (key=value or JSON) through the configured logger; never `print`. Every line that can carry `run_id` does. Log levels: INFO for lifecycle transitions, WARNING for degraded-but-handled, ERROR only when a human should look.

## Discipline

- Telemetry is a seam like any other: exporters/collectors injected and configurable; unit tests never require a collector.
- No PII/raw document content into span attributes or logs — reference by `content_hash`/MinIO key (the gateway redaction applies to outbound text; telemetry follows the same bar).
- Cardinality budget: attributes are enums/ids, never free text.
