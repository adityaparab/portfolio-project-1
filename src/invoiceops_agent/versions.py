"""Point-in-time version pins recorded with every ledger entry (ADR 0004).

Single source of truth for what is currently deployed; agents may override
their own prompt version per call (issue #16) but never silently.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VersionPins:
    graph: str
    models: dict[str, str] = field(default_factory=dict)
    policy: str | None = None


CURRENT = VersionPins(graph="0.1.0")
