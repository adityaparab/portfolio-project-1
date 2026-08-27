"""Cassette record/replay for deterministic offline tests (ADR 0007).

A cassette is one JSON file per (alias, scenario): the raw request signature
and the recorded model output. Modes:
  off    — real calls through the LiteLLM proxy (default)
  record — real call, then persist the cassette
  replay — cassette must exist; a miss is an error (no accidental network)

A new prompt version = a new scenario name; cassettes are never edited.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CassetteMissingError(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(
            f"cassette missing: {path} — record it once (GATEWAY_CASSETTE_MODE=record) "
            "or fix the scenario name; replay mode never touches the network"
        )
        self.path = path


@dataclass(frozen=True)
class CassetteStore:
    root: Path

    def _path(self, alias: str, scenario: str) -> Path:
        safe = self.root / alias / f"{scenario}.json"
        if not safe.is_relative_to(self.root):
            raise ValueError(f"invalid cassette path for {alias}/{scenario}")
        return safe

    def request_hash(self, messages: list[dict[str, Any]]) -> str:
        return hashlib.sha256(
            json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def save(self, alias: str, scenario: str, request_hash: str, content: str) -> Path:
        path = self._path(alias, scenario)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"request_hash": request_hash, "content": content}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return path

    def load(self, alias: str, scenario: str) -> str:
        path = self._path(alias, scenario)
        if not path.exists():
            raise CassetteMissingError(path)
        payload = json.loads(path.read_text())
        content: str = payload["content"]
        return content
