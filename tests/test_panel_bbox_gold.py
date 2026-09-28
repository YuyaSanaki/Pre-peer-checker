"""複合マルチパネル PDF のベクター bbox ゴールデン（Phase 6D）。"""

from __future__ import annotations

import json
from pathlib import Path

from pre_peer_checker.parsers.pdf_panel_geometry import extract_panel_regions_from_pdf
from pre_peer_checker.parsers.pdf_panel_plots import build_synthetic_grid_multipanel_pdf

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "fixtures" / "gold" / "panel_bbox" / "panel_bbox_synthetic_gold.json"


def test_panel_bbox_synthetic_gold(tmp_path: Path):
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    pdf = build_synthetic_grid_multipanel_pdf(tmp_path / "grid.pdf")
    regions = extract_panel_regions_from_pdf(pdf, max_pages=1)
    by = {r["panel"]: r for r in regions}
    expect_letters = {p["panel"] for p in gold["panels"]}
    assert expect_letters <= set(by), sorted(by)
    page_w = float(gold["page"]["width"])
    page_h = float(gold["page"]["height"])
    for spec in gold["panels"]:
        letter = spec["panel"]
        r = by[letter]
        pct = r.get("pct") or {}
        if not pct:
            # derive from absolute coords
            pct = {
                "left_pct": 100.0 * r["x0"] / page_w,
                "top_pct": 100.0 * r["y0"] / page_h,
                "width_pct": 100.0 * (r["x1"] - r["x0"]) / page_w,
                "height_pct": 100.0 * (r["y1"] - r["y0"]) / page_h,
                "label_x_pct": 100.0 * r["label_x"] / page_w,
                "label_y_pct": 100.0 * r["label_y"] / page_h,
            }
        tol = float(gold.get("tolerance_pct") or 8.0)
        assert abs(pct["label_x_pct"] - spec["label_x_pct"]) <= tol, (letter, pct)
        assert abs(pct["label_y_pct"] - spec["label_y_pct"]) <= tol, (letter, pct)
        if "expect_left_max_pct" in spec:
            assert pct["left_pct"] <= spec["expect_left_max_pct"] + tol
        if "expect_left_min_pct" in spec:
            assert pct["left_pct"] >= spec["expect_left_min_pct"] - tol
        if "expect_top_max_pct" in spec:
            assert pct["top_pct"] <= spec["expect_top_max_pct"] + tol
        if "expect_top_min_pct" in spec:
            assert pct["top_pct"] >= spec["expect_top_min_pct"] - tol
        assert pct["width_pct"] >= spec.get("expect_width_min_pct", 10) - 1
        assert pct["height_pct"] >= spec.get("expect_height_min_pct", 10) - 1
