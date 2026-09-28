"""LightGlue threshold: skip if unavailable; else verify per-extractor min_matches separates."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from pre_peer_checker.imaging.lightglue_match import (
    MIN_MATCHES_BY_FEATURES,
    lightglue_available,
    verify_image_pair,
)

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "fixtures" / "gold" / "lightglue_calib" / "calib_summary.json"


@pytest.mark.parametrize("features", sorted(MIN_MATCHES_BY_FEATURES))
def test_min_matches_matches_recorded_calib(features: str):
    data = json.loads(SUMMARY.read_text(encoding="utf-8"))["extractors"][features]
    chosen = MIN_MATCHES_BY_FEATURES[features]
    assert data["chosen_min_matches"] == chosen
    assert data["must_neg_max"] < chosen <= data["must_pos_min"]


@pytest.mark.skipif(not lightglue_available(), reason="lightglue+torch not installed")
@pytest.mark.parametrize("features", sorted(MIN_MATCHES_BY_FEATURES))
def test_lightglue_threshold_separates_probe_vs_noise(tmp_path: Path, features: str):
    size = 192
    rng = np.random.default_rng(0)
    base = (rng.random((size, size)) * 40 + 80).astype(np.uint8)
    img = Image.fromarray(base).convert("L")
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 90, 90], fill=230)
    draw.ellipse([100, 30, 170, 110], outline=15, width=5)
    draw.line([10, 150, 180, 170], fill=5, width=4)
    probe = np.asarray(img, dtype=np.uint8)

    # Distinct unrelated geometry (not shared-block noise — that can spuriously match)
    other = (rng.random((size, size)) * 30 + 100).astype(np.uint8)
    oimg = Image.fromarray(other).convert("L")
    od = ImageDraw.Draw(oimg)
    for r in range(15, 90, 12):
        od.ellipse([96 - r, 96 - r, 96 + r, 96 + r], outline=40, width=2)
    for y in range(0, size, 10):
        od.line([0, y, size, y], fill=210, width=1)
    unrelated = np.asarray(oimg, dtype=np.uint8)

    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    noise = tmp_path / "unrelated.png"
    Image.fromarray(probe).save(a)
    Image.fromarray(probe).save(b)
    Image.fromarray(unrelated).save(noise)

    kw = {"prefer_lightglue": True, "require_lightglue": True, "features": features}
    threshold = MIN_MATCHES_BY_FEATURES[features]
    pos = verify_image_pair(a, b, min_matches=0, **kw)
    neg = verify_image_pair(a, noise, min_matches=0, **kw)
    assert pos.method == f"lightglue+{features}"
    assert pos.num_matches > threshold
    assert neg.num_matches < threshold

    assert verify_image_pair(a, b, **kw).verified is True
    assert verify_image_pair(a, noise, **kw).verified is False
