"""Gateway client: the ONLY doorway for model calls (ADR 0005).

Thin ``openai``-SDK wrapper over the LiteLLM proxy. Responsibilities beyond
transport (LiteLLM handles routing/fallback): guardrails (PII redaction,
injection heuristics), per-call token budgets, schema-validated structured
output with typed retry-or-escalate, bounded backoff for infra errors, a
telemetry hook, and cassette record/replay for tests.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from invoiceops_agent.gateway_client.cassettes import CassetteStore
from invoiceops_agent.gateway_client.errors import (
    GatewayBudgetError,
    GatewayConfigError,
    GatewayGuardrailError,
    GatewayResponseError,
    GatewayTransportError,
)
from invoiceops_agent.gateway_client.guardrails import Redactor, apply_guardrails

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

TelemetryHook = Callable[[dict[str, Any]], Awaitable[None]]

KNOWN_ALIASES = frozenset({"extract-vision", "triage-reasoner", "eval-judge", "embed"})


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Cheap deterministic estimate (~4 chars/token) for budget checks."""
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return chars // 4


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        redactor: Redactor | None = None,
        token_budgets: dict[str, int] | None = None,
        timeout_seconds: float = 120.0,
        infra_retries: int = 2,
        cassette_store: CassetteStore | None = None,
        cassette_mode: str = "off",  # off | record | replay
        telemetry_hook: TelemetryHook | None = None,
        alias_model_map: dict[str, str] | None = None,
    ) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_seconds)
        self._alias_map = alias_model_map or {}
        self._redactor = redactor or Redactor()
        self._budgets = token_budgets or {}
        self._infra_retries = infra_retries
        self._cassettes = cassette_store
        self._cassette_mode = cassette_mode
        self._telemetry = telemetry_hook

    async def complete(
        self,
        alias: str,
        messages: list[dict[str, Any]],
        response_model: type[T] | None = None,
        *,
        scenario: str | None = None,
        max_output_tokens: int = 2048,
    ) -> T | str:
        """One model call. With ``response_model``: schema-validated ``T``;
        one structured retry with the validation error, then escalation."""
        self._check_alias(alias)

        guardrail = apply_guardrails(messages, self._redactor)
        if not guardrail.ok or guardrail.redacted_messages is None:
            raise GatewayGuardrailError(
                f"guardrail rejected outbound content: {guardrail.reason}",
                alias=alias,
                detail=guardrail.reason,
            )
        outbound = guardrail.redacted_messages

        self._check_budget(alias, outbound, max_output_tokens)

        if response_model is None:
            content = await self._raw_content(alias, outbound, scenario)
            await self._emit(alias, len(content) // 4, "text")
            return content

        # Structured: first attempt, then one corrective retry, then escalate.
        validation_error: str | None = None
        for attempt in range(2):
            attempt_messages = list(outbound)
            if validation_error is not None:
                attempt_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply failed schema validation with: "
                            f"{validation_error}. Reply again with ONLY valid JSON "
                            "for the requested schema."
                        ),
                    }
                )
            content = await self._raw_content(alias, attempt_messages, scenario)
            try:
                parsed = response_model.model_validate_json(_extract_json(content))
            except ValidationError as exc:
                validation_error = str(exc)[:500]
                logger.warning("schema validation failed (attempt %d) for %s", attempt + 1, alias)
                continue
            await self._emit(alias, len(content) // 4, "structured")
            return parsed

        raise GatewayResponseError(
            "model output failed schema validation after retry",
            alias=alias,
            detail=validation_error,
        )

    async def embed(self, alias: str, text: str) -> list[float]:
        """Embeddings (near-duplicate detection, issue #23). Guardrails apply."""
        self._check_alias(alias)
        guardrail = apply_guardrails([{"role": "user", "content": text}], self._redactor)
        if not guardrail.ok or guardrail.redacted_messages is None:
            raise GatewayGuardrailError(
                f"guardrail rejected outbound content: {guardrail.reason}",
                alias=alias,
                detail=guardrail.reason,
            )
        if self._cassette_mode == "replay" and self._cassettes is not None:
            raw = self._cassettes.load(alias, "embed-default")
            values: list[float] = json.loads(raw)
            return values
        try:
            response = await self._client.embeddings.create(
                model=self._resolve(alias), input=guardrail.redacted_messages[0]["content"]
            )
        except (APIConnectionError, APITimeoutError) as exc:
            raise GatewayTransportError("embeddings transport failure", alias=alias) from exc
        except APIStatusError as exc:
            raise GatewayTransportError(f"embeddings HTTP {exc.status_code}", alias=alias) from exc
        vector = list(response.data[0].embedding)
        if self._cassette_mode == "record" and self._cassettes is not None:
            self._cassettes.save(alias, "embed-default", "n/a", json.dumps(vector))
        await self._emit(alias, len(text) // 4, "embed")
        return vector

    # ------------------------------------------------------------------ internals

    async def _raw_content(
        self, alias: str, messages: list[dict[str, Any]], scenario: str | None
    ) -> str:
        """Transport layer with cassette modes and bounded infra backoff."""
        if self._cassette_mode in ("record", "replay"):
            if self._cassettes is None or scenario is None:
                raise GatewayConfigError(
                    "cassette mode requires a cassette store and a scenario name",
                    alias=alias,
                )
            request_hash = self._cassettes.request_hash(messages)
            if self._cassette_mode == "replay":
                content = self._cassettes.load(alias, scenario)
                if self._cassettes is not None:
                    path = self._cassettes._path(alias, scenario)
                    if json.loads(path.read_text())["request_hash"] != request_hash:
                        logger.warning(
                            "cassette request drift on %s/%s — new prompt version?",
                            alias,
                            scenario,
                        )
                return content

        last_exc: Exception | None = None
        for attempt in range(self._infra_retries + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=self._resolve(alias),
                    messages=messages,  # type: ignore[arg-type]
                )
                raw = response.choices[0].message.content
                if raw is None:
                    raise GatewayResponseError("model returned empty content", alias=alias)
                if self._cassette_mode == "record" and self._cassettes is not None:
                    self._cassettes.save(alias, scenario or "default", request_hash, raw)
                return raw
            except (APIConnectionError, APITimeoutError) as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (2**attempt))  # bounded backoff, infra only
            except APIStatusError as exc:
                if exc.status_code is not None and exc.status_code >= 500:
                    last_exc = exc
                    await asyncio.sleep(0.5 * (2**attempt))
                else:
                    # 4xx = caller/config problem (e.g. budget/auth); never retry
                    raise GatewayConfigError(
                        f"gateway rejected request: HTTP {exc.status_code}",
                        alias=alias,
                        detail=str(exc)[:300],
                    ) from exc
        raise GatewayTransportError(
            "transport failure after retries", alias=alias, detail=repr(last_exc)
        )

    def _resolve(self, alias: str) -> str:
        """Virtual alias -> wire model name (identity when the proxy defines
        our aliases itself; map via config when it does not)."""
        return self._alias_map.get(alias, alias)

    def _check_alias(self, alias: str) -> None:
        if alias not in KNOWN_ALIASES:
            raise GatewayConfigError(
                f"unknown alias {alias!r} — known: {sorted(KNOWN_ALIASES)} "
                "(aliases live in deploy/litellm/config.yaml)",
                alias=alias,
            )

    def _check_budget(
        self, alias: str, messages: list[dict[str, Any]], max_output_tokens: int
    ) -> None:
        budget = self._budgets.get(alias)
        if budget is None:
            return
        estimated = _estimate_tokens(messages) + max_output_tokens
        if estimated > budget:
            raise GatewayBudgetError(
                f"per-call token budget exceeded for {alias}: "
                f"~{estimated} > {budget} (rejected before spending)",
                alias=alias,
                detail={"estimated": estimated, "budget": budget},
            )

    async def _emit(self, alias: str, tokens: int, kind: str) -> None:
        if self._telemetry is None:
            return
        started = time.perf_counter()
        record: dict[str, Any] = {
            "alias": alias,
            "tokens_estimate": tokens,
            "kind": kind,
            "cassette_mode": self._cassette_mode,
            "ts": started,
        }
        await self._telemetry(record)


def _extract_json(content: str) -> str:
    """Tolerate markdown-fenced or prose-wrapped JSON."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        return text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text
