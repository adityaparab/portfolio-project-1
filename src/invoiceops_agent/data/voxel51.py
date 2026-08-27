"""Voxel51 invoice corpus fetcher (issue #18).

Fetches a deterministic, annotated subset of
``Voxel51/high-quality-invoice-images-for-ocr`` into content-addressed
storage under ``data/voxel51/raw/`` (gitignored) and commits a manifest with
content hashes, quality tiers, and ground-truth label mappings at
``eval/corpus/manifest.json``.

Usage:
    uv run python -m invoiceops_agent.data.voxel51 --count 100

Determinism: samples are filtered to those carrying ``json_annotation`` and
ordered by sha256(sample id); the same count always selects the same subset.
"""

import argparse
import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_download

logger = logging.getLogger(__name__)

REPO_ID = "Voxel51/high-quality-invoice-images-for-ocr"
REPO_TYPE = "dataset"
MANIFEST_VERSION = "voxel51-corpus@1"


def classify_quality_tier(width: int, height: int, contrast_std: float) -> str:
    """Heuristic v1: resolution + contrast -> A/B/C (deterministic, pure).

    Thresholds calibrated on the observed Voxel51 corpus (uniform ~1654px
    renders, contrast std 26-36): contrast is the discriminator, resolution
    the gate. Degraded variants (rotations/stamps in #45's hard negatives)
    land in C.
    """
    if min(width, height) >= 1000 and contrast_std >= 33:
        return "A"
    if min(width, height) >= 800 and contrast_std >= 26:
        return "B"
    return "C"


def select_samples(samples: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Deterministic subset: order by sha256(sample id), take first ``count``."""
    def key(sample: dict[str, Any]) -> str:
        sid = str(sample.get("_id", {}).get("$oid", sample.get("filepath", "")))
        return hashlib.sha256(sid.encode()).hexdigest()

    return sorted(samples, key=key)[:count]

# Dataset card states no explicit license at time of writing; recorded in the
# manifest and README. Usage here: local portfolio evaluation only.
LICENSE_NOTE = (
    "No license declared on the dataset card (checked 2026-08-27); "
    "used locally for portfolio evaluation, not redistributed."
)


def load_samples() -> list[dict[str, Any]]:
    path = hf_hub_download(REPO_ID, "samples.json", repo_type=REPO_TYPE)
    data = json.loads(Path(path).read_text())
    samples: list[dict[str, Any]] = data["samples"]
    return samples


def _parse_annotation(sample: dict[str, Any]) -> dict[str, Any] | None:
    raw = sample.get("json_annotation")
    if not raw:
        return None
    try:
        annotation = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return annotation if isinstance(annotation.get("invoice"), dict) else None


def _map_labels(annotation: dict[str, Any]) -> dict[str, Any]:
    """Ground truth → InvoiceExtraction-shaped label mapping (raw + normalized)."""
    invoice = annotation.get("invoice", {})
    subtotal = annotation.get("subtotal", {})
    items = annotation.get("items", []) or []

    def iso(value: str) -> str | None:
        from datetime import datetime

        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date().isoformat()
            except (ValueError, AttributeError):
                continue
        return None

    issue_raw = str(invoice.get("invoice_date") or "")
    due_raw = str(invoice.get("due_date") or "") or str(
        (annotation.get("payment_instructions") or {}).get("due_date") or ""
    )
    return {
        "vendor_name": invoice.get("seller_name") or None,
        "invoice_number": str(invoice.get("invoice_number") or "") or None,
        "issue_date": issue_raw or None,
        "issue_date_iso": iso(issue_raw),
        "due_date": due_raw or None,
        "due_date_iso": iso(due_raw),
        "currency": "USD",  # assumed — corpus is US-formatted, labels carry none
        "currency_assumed": True,
        "total_amount": subtotal.get("total") or None,
        "tax_total": subtotal.get("tax") or None,
        "lines": [
            {
                "line_no": str(i + 1),
                "description": item.get("description"),
                "qty": item.get("quantity"),
                "unit_price": None,  # derivable: total_price / quantity (#19)
                "line_total": item.get("total_price"),
            }
            for i, item in enumerate(items)
        ],
    }


def _image_stats(data: bytes) -> tuple[int, int, float]:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        width, height = img.size
        std: float = 0.0
        try:
            from PIL import ImageStat

            std = float(ImageStat.Stat(img.convert("L")).stddev[0])
        except Exception:  # noqa: BLE001 — stats are best-effort for tiering
            std = 0.0
    return width, height, std


def build_manifest(
    entries: list[dict[str, Any]], count_requested: int
) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "source_repo": REPO_ID,
        "license_note": LICENSE_NOTE,
        "selection": {
            "strategy": "annotated samples ordered by sha256(sample_id), first N",
            "count_requested": count_requested,
            "count_selected": len(entries),
        },
        "tier_rule": (
            "A: min(w,h)>=1000 and contrast_std>=33; "
            "B: min(w,h)>=800 and std>=26; else C "
            "(heuristic v1, calibrated on corpus: uniform ~1654px, std 26-36)"
        ),
        "images": entries,
    }


def fetch(count: int, out_root: Path = Path("data/voxel51")) -> dict[str, Any]:
    samples = load_samples()
    annotated = [s for s in samples if _parse_annotation(s) is not None]
    logger.info("annotated samples: %d / %d", len(annotated), len(samples))
    chosen = select_samples(annotated, count)

    raw_dir = out_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for sample in chosen:
        filename_in_repo: str = sample["filepath"]
        local = hf_hub_download(REPO_ID, filename_in_repo, repo_type=REPO_TYPE)
        data = Path(local).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        content_path = raw_dir / f"{digest}.jpg"
        if not content_path.exists():
            shutil.copyfile(local, content_path)

        width, height, std = _image_stats(data)
        annotation = _parse_annotation(sample) or {}
        entries.append(
            {
                "content_hash": digest,
                "path": str(content_path),
                "source_file": filename_in_repo,
                "width": width,
                "height": height,
                "contrast_std": round(std, 2),
                "quality_tier": classify_quality_tier(width, height, std),
                "labels": _map_labels(annotation),
            }
        )
        logger.info(
            "fetched %s -> %s (tier %s)",
            filename_in_repo,
            digest[:12],
            entries[-1]["quality_tier"],
        )

    return build_manifest(entries, count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--manifest-out", type=Path, default=Path("eval/corpus/manifest.json"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    manifest = fetch(args.count)
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    tiers: dict[str, int] = {}
    for image in manifest["images"]:
        tiers[image["quality_tier"]] = tiers.get(image["quality_tier"], 0) + 1
    print(
        f"manifest: {args.manifest_out} "
        f"({manifest['selection']['count_selected']} images, tiers {tiers})"
    )


if __name__ == "__main__":
    main()
