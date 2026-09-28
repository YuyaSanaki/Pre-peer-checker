"""Phase 6A: PDF vector panel geometry + legend schema coerce."""

from __future__ import annotations

import json
from pathlib import Path

from pre_peer_checker.llm.legend_schema import coerce_legend_dict, parse_legend_llm_response
from pre_peer_checker.parsers.pdf_panel_geometry import (
    extract_panel_regions_from_pdf,
    regions_from_labels,
    write_panel_crops,
)
from pre_peer_checker.parsers.pdf_panel_plots import build_synthetic_multipanel_pdf
from pre_peer_checker.report.figure_previews import panel_boxes_percent


def test_regions_from_labels_grid():
    labels = {
        "A": (14.0, 50.0, 40.0),
        "B": (14.0, 300.0, 40.0),
        "C": (14.0, 50.0, 220.0),
    }
    regs = regions_from_labels(labels, 600.0, 400.0)
    assert {r.panel for r in regs} == {"A", "B", "C"}
    a = next(r for r in regs if r.panel == "A")
    assert a.x1 <= 300.0 + 1e-6  # stops before B's x
    assert a.y1 <= 220.0 + 1e-6  # stops before C's y
    pct = panel_boxes_percent(labels, 600.0, 400.0)
    assert "A" in pct and pct["A"]["width_pct"] > 0


def test_synthetic_pdf_panel_regions_and_crops(tmp_path: Path):
    pdf = tmp_path / "fig_synth.pdf"
    build_synthetic_multipanel_pdf(pdf)
    regions = extract_panel_regions_from_pdf(pdf, max_pages=1)
    letters = {r["panel"] for r in regions}
    assert {"I", "Q"} <= letters
    crop_dir = tmp_path / "crops"
    written = write_panel_crops(pdf, crop_dir, max_pages=1, zoom=1.5)
    assert written
    assert all(Path(w["path"]).is_file() for w in written)
    assert {w["panel"] for w in written} >= {"I", "Q"}


def test_coerce_legend_dict_drops_bad_panels():
    raw = {
        "figure": "Figure 1",
        "panels": [
            {"panel": "F", "n": "12", "groups": ["cont"], "confidence": 1.5},
            {"panel": "not-a-panel", "n": 3},
            {"panel": "G", "n": -1, "groups": "solo"},
        ],
        "tests": ["Welch"],
        "p_values": ["0.01", "x"],
        "citation": {"mentioned": 1},
        "error_bar_type": "SEM",
        "independence_claims": {"mentioned": 1, "kind": "Independent", "spans": ["n=3 independent"]},
        "exclusion_criteria": {"spans": ["animals were excluded"]},
    }
    coerced, soft = coerce_legend_dict(raw)
    assert soft
    panels = {p["panel"]: p for p in coerced["panels"]}
    assert "F" in panels
    assert panels["F"]["n"] == 12
    assert panels["F"]["confidence"] == 1.0  # clamped
    assert "not-a-panel".upper() not in panels
    assert panels["G"]["n"] is None
    assert panels["G"]["groups"] == ["solo"]
    assert coerced["p_values"] == [0.01]
    assert coerced["citation"]["mentioned"] is True
    assert coerced["error_bar_type"] == "sem"
    assert coerced["independence_claims"]["kind"] == "independent"
    assert coerced["exclusion_criteria"]["mentioned"] is True


def test_parse_legend_llm_response_schema_forced():
    text = json.dumps(
        {
            "figure": "Figure 2",
            "panels": [{"panel": "a", "n": 4, "groups": ["early L3"]}],
            "tests": [],
            "p_values": [],
            "citation": {"mentioned": False},
            "error_bar_type": "sd",
        }
    )
    parsed = parse_legend_llm_response(text)
    assert parsed is not None
    assert parsed.panels[0].panel == "A"
    assert parsed.panels[0].n == 4
    assert parsed.error_bar_type == "sd"


def test_detect_p0_extract_keys():
    from pre_peer_checker.llm.legend_schema import (
        detect_error_bar_type,
        detect_exclusion_criteria,
        detect_independence_claims,
    )
    from pre_peer_checker.llm.legend_extract import extract_legend_json_hybrid

    assert detect_error_bar_type("Data are mean ± SEM.") == "sem"
    assert detect_error_bar_type("Bars show SD.") == "sd"
    assert detect_independence_claims(
        "n=3 independent experiments"
    ).kind == "independent"
    assert detect_independence_claims("shared control across panels").kind == (
        "shared_control"
    )
    assert detect_exclusion_criteria("Two animals were excluded from analysis").mentioned

    hybrid = extract_legend_json_hybrid(
        "Figure 1. (A) Quantification. n=5. Data are mean ± SEM from "
        "3 independent experiments. Two animals were excluded due to injury.",
        figure_hint="Figure 1",
    )
    assert hybrid.error_bar_type == "sem"
    assert hybrid.independence_claims.kind == "independent"
    assert hybrid.exclusion_criteria.mentioned
