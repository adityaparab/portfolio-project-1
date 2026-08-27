"""Baseline extraction runner (issue #19): corpus → real model → field F1.

Usage:
    uv run python -m invoiceops_agent.eval.baseline --limit 20

Runs the REAL extraction agent (gateway → native proxy → glm-ocr) over the
committed Voxel51 corpus manifest, tallies per-field exact-match F1 and
per-tier breakdown, and writes:
    eval/reports/baseline-f1.md              (committed summary)
    eval/reports/artifacts/baseline-f1.json  (raw per-image results, gitignored)

No thresholds — this is the baseline the Phase-5 experiment log will diff
against. Code is structured for reuse by the #46 runner.
"""

import argparse
import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

from invoiceops_agent.agents.extraction import ExtractionAgent, InvoiceExtraction
from invoiceops_agent.api.settings import Settings
from invoiceops_agent.eval.metrics import FIELDS, FieldTally, tally_documents
from invoiceops_agent.gateway_client import GatewayClient

logger = logging.getLogger(__name__)


def build_gateway(settings: Settings) -> GatewayClient:
    import json as _json

    return GatewayClient(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
        timeout_seconds=settings.gateway_timeout_seconds,
        infra_retries=settings.gateway_infra_retries,
        token_budgets=settings.gateway_token_budgets,
        redactor=None,
        alias_model_map=_json.loads(settings.gateway_model_map_json or "{}"),
    )


async def run(limit: int, manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    images = manifest["images"][:limit]
    settings = Settings()
    gateway = build_gateway(settings)
    agent = ExtractionAgent(store=_NullStore(), gateway=gateway)

    documents: list[dict[str, Any]] = []
    escalated = 0
    started = time.perf_counter()
    for i, image in enumerate(images, 1):
        path = Path(image["path"])
        data_b64 = base64.b64encode(path.read_bytes()).decode()
        extraction_dict: dict[str, Any] | None
        try:
            result = await agent.extract_bytes(data_b64, "image/jpeg", image["source_file"])
            assert isinstance(result, InvoiceExtraction)
            extraction_dict = result.model_dump(mode="json")
        except Exception as exc:
            logger.warning("escalated on %s: %s", image["source_file"], type(exc).__name__)
            escalated += 1
            extraction_dict = None
        documents.append(
            {
                "source_file": image["source_file"],
                "content_hash": image["content_hash"],
                "quality_tier": image["quality_tier"],
                "extraction": extraction_dict,
                "labels": image["labels"],
            }
        )
        logger.info("[%d/%d] %s", i, len(images), image["source_file"])

    elapsed = time.perf_counter() - started
    tallies = tally_documents(documents)
    by_tier: dict[str, dict[str, FieldTally]] = {}
    for tier in sorted({d["quality_tier"] for d in documents}):
        by_tier[tier] = tally_documents([d for d in documents if d["quality_tier"] == tier])

    return {
        "manifest_version": manifest["manifest_version"],
        "model_alias": "extract-vision",
        "limit": limit,
        "n_images": len(documents),
        "n_escalated": escalated,
        "elapsed_seconds": round(elapsed, 1),
        "tallies": {
            f: {"tp": t.tp, "fp": t.fp, "fn": t.fn, "f1": t.f1} for f, t in tallies.items()
        },
        "by_tier": {tier: {f: t.f1 for f, t in tiers.items()} for tier, tiers in by_tier.items()},
        "documents": documents,
    }


class _NullStore:
    async def put(self, *a: object, **k: object) -> None: ...

    async def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        raise AssertionError("baseline runner uses extract_bytes directly")


def write_report(result: dict[str, Any], out_md: Path) -> None:
    lines = [
        "# Baseline Extraction Field-F1 (issue #19)",
        "",
        "First measurement of the real extraction path over the committed Voxel51",
        "corpus — a baseline, not a scorecard. The Phase-5 experiment log (#51)",
        "diffs against this.",
        "",
        "## Method",
        "",
        f"- Corpus: `{result['manifest_version']}`, first **{result['n_images']}** images"
        f" of the committed manifest (limit {result['limit']})",
        "- Model: gateway alias `extract-vision` (dev: glm-ocr via native proxy),"
        " prompt `extract@v1`",
        "- Comparison: field-level exact match (text: normalized casefold/whitespace;"
        " money: Decimal within ±0.01; dates: ISO; line_count: integer equality)",
        "- Escalations (schema-validation failures after retry) count as misses for"
        " every labeled field",
        "",
        "## Results",
        "",
        "| Field | TP | FP | FN | F1 |",
        "|---|---|---|---|---|",
    ]
    for field in FIELDS:
        t = result["tallies"][field]
        lines.append(f"| {field} | {t['tp']} | {t['fp']} | {t['fn']} | {t['f1']:.3f} |")
    lines += [
        "",
        f"- Images: **{result['n_images']}** · escalations: **{result['n_escalated']}**"
        f" · wall time: {result['elapsed_seconds']}s"
        f" (~{result['elapsed_seconds'] / max(result['n_images'], 1):.1f}s/image)",
        "",
        "## Per quality tier (F1)",
        "",
        "| Tier | " + " | ".join(FIELDS) + " |",
        "|---" * (len(FIELDS) + 1) + "|",
    ]
    for tier, f1s in result["by_tier"].items():
        lines.append(f"| {tier} | " + " | ".join(f"{f1s[f]:.2f}" for f in FIELDS) + " |")
    lines += [
        "",
        "## Reading the baseline",
        "",
        "No targets apply here (targets are Phase-5, issue #47). The value is the",
        "delta reference: prompt/model changes in later issues re-run this and",
        "record movement in the experiment log.",
        "",
    ]
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--manifest", type=Path, default=Path("eval/corpus/manifest.json"))
    parser.add_argument("--out-md", type=Path, default=Path("eval/reports/baseline-f1.md"))
    parser.add_argument(
        "--out-json", type=Path, default=Path("eval/reports/artifacts/baseline-f1.json")
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = asyncio.run(run(args.limit, args.manifest))
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    write_report(result, args.out_md)
    print(f"report: {args.out_md} (escalated {result['n_escalated']}/{result['n_images']})")


if __name__ == "__main__":
    main()
