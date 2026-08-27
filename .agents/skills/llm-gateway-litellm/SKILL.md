---
name: llm-gateway-litellm
description: LLM access via the LiteLLM proxy and the src/gateway_client wrapper — model routing, virtual aliases, guardrails (PII redaction, schema validation, token budgets), semantic cache, cost tracking. Use whenever making any LLM/vision/OCR model call, editing LiteLLM config, or touching prompts.
---

# LLM gateway patterns (LiteLLM + `src/gateway_client/`)

Architecture rule (ADR 0005): **every** model call goes through the LiteLLM proxy via the thin wrapper in `src/gateway_client/`. Direct provider SDKs, direct Ollama HTTP calls, or calls from outside `src/agents/` are bugs.

## Topology

```
src/agents/ ──► gateway_client ──► LiteLLM proxy ──► Ollama (dev) / OpenAI (prod)
```

- The wrapper uses the **`openai` SDK pointed at `LITELLM_BASE_URL`** — one client, one API shape, no provider branches in application code.
- Models are addressed **only by virtual alias** (`extract-vision`, `triage-reasoner`, `eval-judge`, …) defined in `deploy/litellm/config.yaml`. Dev aliases map to local Ollama models; prod/eval map to OpenAI. Switching environments = config change, never code.
- Adding a model need = new alias in `config.yaml` first, then reference the alias. Never hardcode a backend model name in Python.

## Wrapper responsibilities (`gateway_client`)

1. Request guardrails: PII redaction on outbound text, prompt-injection heuristics, token budget enforcement per call class.
2. Response discipline: structured-output/schema-validated responses (Pydantic models with per-field confidence for extraction); malformed output = typed retry-or-escalate, not `json.loads` hope.
3. Telemetry: span + cost/token accounting per call (LiteLLM spend logs + OTel attributes incl. alias, model served, latency).
4. Resilience: timeout, bounded retry with backoff for infra errors, fallback chain per alias as configured in LiteLLM.

## Calling conventions in agents

```python
result = await gateway.complete(
    alias="extract-vision",
    messages=[...],
    response_model=InvoiceExtraction,   # schema-validated
)
```

- Prompts are **versioned artifacts** (files with version ids), not f-strings inline in node code. The prompt version is pinned into every ledger entry produced by that call.
- Temperature/sampling pinned per alias in config; do not override per call site without a recorded reason (eval comparability).

## Secrets & config

- `LITELLM_API_KEY` / provider keys only in env / Compose secrets. Never commit keys; synthetic data only — no real vendor/PII content even in prompts.

## Testing

- Unit tests replay **VCR cassettes** through the wrapper (record once against dev proxy, replay offline). Cassette per alias+scenario, committed.
- Eval runs (Phase 5) are the only real-model executions; they tag runs by alias so reports show which backend served them.

## When something's off

- 4xx from proxy → usually alias typo or budget exceeded: check `deploy/litellm/config.yaml` before touching agent code.
- Wrong model served → check alias mapping per environment profile, not the call site.
