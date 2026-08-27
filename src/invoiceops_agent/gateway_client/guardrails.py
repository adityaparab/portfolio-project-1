"""Outbound guardrails: PII redaction and prompt-injection heuristics."""

import re
from dataclasses import dataclass, field
from typing import Any

# Redaction applies to text leaving for the model. Patterns are deliberately
# conservative (banking context: IBANs, card numbers, emails).
_DEFAULT_PATTERNS: dict[str, str] = {
    "IBAN": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
    "CARD": r"\b(?:\d[ -]?){13,19}\b",
    "EMAIL": r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
}

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+|any\s+)*(previous\s+|prior\s+|above\s+)*(instructions|prompts?|rules?)",
        r"disregard (the |your |all |any )?(previous|prior|above)? ?(instructions|prompt)",
        r"reveal|show|print|repeat (me )?(your|the) (system )?prompt",
        r"you are now|new instructions:",
        r"</?\s*(system|assistant)\s*>",  # role-token smuggling
        r"developer mode|jailbreak|DAN mode",
    )
)


@dataclass(frozen=True)
class GuardrailResult:
    ok: bool
    reason: str | None = None
    redacted_messages: list[dict[str, Any]] = field(default_factory=list)


class Redactor:
    def __init__(self, patterns: dict[str, str] | None = None, enabled: bool = True) -> None:
        self._patterns = patterns if patterns is not None else _DEFAULT_PATTERNS
        self._compiled = [(name, re.compile(p)) for name, p in self._patterns.items()]
        self._enabled = enabled

    def redact(self, text: str) -> str:
        if not self._enabled:
            return text
        for name, pattern in self._compiled:
            text = pattern.sub(f"[REDACTED:{name}]", text)
        return text


def check_injection(text: str) -> str | None:
    """Return a reason string when the text matches injection heuristics."""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return f"suspicious instruction pattern: {pattern.pattern!r}"
    return None


def apply_guardrails(messages: list[dict[str, Any]], redactor: Redactor) -> GuardrailResult:
    """Redact PII in outbound text; reject user-visible injection attempts.

    Redaction applies to ALL message content; injection checks apply to
    user-role content only (system prompts legitimately reference rules).
    """
    redacted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            if role == "user":
                reason = check_injection(content)
                if reason is not None:
                    return GuardrailResult(ok=False, reason=reason)
            redacted.append({**message, "content": redactor.redact(content)})
        else:
            redacted.append(message)
    return GuardrailResult(ok=True, redacted_messages=redacted)
