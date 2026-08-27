"""Unit tests for the corpus fetcher's pure functions (issue #18) — offline."""

import pytest

from invoiceops_agent.data.voxel51 import (
    MANIFEST_VERSION,
    build_manifest,
    classify_quality_tier,
    select_samples,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("width", "height", "std", "expected"),
    [
        (1240, 1754, 55.0, "A"),  # high-res, high contrast
        (1000, 1000, 33.0, "A"),  # boundary inclusive
        (1240, 1754, 30.0, "B"),  # high-res, softer
        (900, 800, 50.0, "B"),  # B resolution boundary
        (600, 900, 25.0, "C"),  # below B resolution gate
        (500, 700, 50.0, "C"),  # low-res
        (1200, 900, 10.0, "C"),  # flat contrast
        (1654, 2200, 36.0, "A"),  # typical corpus top
        (1654, 2200, 27.0, "B"),  # typical corpus mid
        (1654, 2200, 25.0, "C"),  # softest corpus images
    ],
)
def test_quality_tier_heuristic(width: int, height: int, std: float, expected: str) -> None:
    assert classify_quality_tier(width, height, std) == expected


@pytest.mark.unit
def test_select_samples_deterministic_and_counted() -> None:
    samples = [
        {"_id": {"$oid": f"{'abcd'[i] * 24}"}, "filepath": f"data/x{i}.jpg"} for i in range(4)
    ]
    first = select_samples(samples, 2)
    second = select_samples(list(reversed(samples)), 2)
    assert [s["filepath"] for s in first] == [s["filepath"] for s in second]
    assert len(first) == 2
    assert select_samples(samples, 10) == select_samples(samples, 4)


@pytest.mark.unit
def test_manifest_shape_and_version() -> None:
    entries = [
        {
            "content_hash": "a" * 64,
            "path": "data/voxel51/raw/" + "a" * 64 + ".jpg",
            "source_file": "data/batch1-0001.jpg",
            "width": 1000,
            "height": 1400,
            "contrast_std": 44.2,
            "quality_tier": "A",
            "labels": {"vendor_name": "X", "lines": []},
        }
    ]
    manifest = build_manifest(entries, count_requested=5)
    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert manifest["selection"] == {
        "strategy": "annotated samples ordered by sha256(sample_id), first N",
        "count_requested": 5,
        "count_selected": 1,
    }
    assert "license_note" in manifest and manifest["images"] is entries
