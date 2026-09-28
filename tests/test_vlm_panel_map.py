"""VLM panel-map schema + vector→VLM assist wiring."""

from __future__ import annotations

import json
from pathlib import Path

from pre_peer_checker.llm.panel_map import (
    coerce_panel_map_dict,
    panel_map_to_region_dicts,
    parse_panel_map_response,
)
from pre_peer_checker.llm.panel_map_assist import extract_panel_regions_vector_then_vlm
from pre_peer_checker.llm.vlm_backend import CallableVlmBackend, probe_vlm_backends
from pre_peer_checker.parsers.pdf_panel_plots import build_synthetic_multipanel_pdf


def test_coerce_and_parse_panel_map():
    raw = {
        "figure": "Figure 1",
        "page_index": 0,
        "panels": [
            {"panel": "a", "left_pct": 5, "top_pct": 5, "width_pct": 40, "height_pct": 40},
            {"panel": "ZZ", "left_pct": 1, "top_pct": 1, "width_pct": 10, "height_pct": 10},
            {"panel": "B", "left_pct": 50, "top_pct": 5, "width_pct": 200, "height_pct": 40},
        ],
    }
    coerced, soft = coerce_panel_map_dict(raw)
    assert soft
    letters = {p["panel"] for p in coerced["panels"]}
    assert letters == {"A", "B"}
    b = next(p for p in coerced["panels"] if p["panel"] == "B")
    assert b["left_pct"] + b["width_pct"] <= 100.5

    text = json.dumps(coerced)
    parsed = parse_panel_map_response(text)
    assert parsed is not None
    regs = panel_map_to_region_dicts(
        parsed, page_w=1000, page_h=800, source="/x.pdf", source_name="x.pdf"
    )
    assert regs and regs[0]["geometry_source"] == "vlm"
    assert regs[0]["x1"] > regs[0]["x0"]


def test_unwrap_generation_result_text():
    from pre_peer_checker.llm.vlm_backend import _unwrap_vlm_text

    class GR:
        text = '{"figure": "Figure 1", "panels": [{"panel": "A", "left_pct": 1, "top_pct": 1, "width_pct": 40, "height_pct": 40}]}'

    assert "panels" in _unwrap_vlm_text(GR())
    assert _unwrap_vlm_text(("hello",)) == "hello"
    # stringified GenerationResult fallback (Mac mlx-vlm)
    s = (
        "GenerationResult(text='{\\n  \"figure\": \"Figure 1\",\\n  "
        '"page_index\": 0,\\n  \"panels\": []\\n}\', token=151645, logprobs=array([]))'
    )
    unwrapped = _unwrap_vlm_text(s)
    assert '"figure"' in unwrapped or "Figure 1" in unwrapped
    parsed = parse_panel_map_response(unwrapped)
    assert parsed is None  # empty panels


def test_assist_marks_empty_panels(tmp_path: Path):
    import fitz
    from pre_peer_checker.llm.panel_map_assist import assist_panel_regions_with_vlm

    blank = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page(width=400, height=300)
    doc.save(blank)
    doc.close()

    be = CallableVlmBackend(
        lambda _p, _i: json.dumps(
            {"figure": "Figure 1", "page_index": 0, "panels": []}
        )
    )
    regs, meta = assist_panel_regions_with_vlm(blank, backend=be)
    assert regs == []
    assert meta["note"] == "vlm_empty_panels"


def test_vector_then_vlm_assist_on_blank_pdf(tmp_path: Path, monkeypatch):
    import fitz

    blank = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page(width=500, height=400)
    doc.save(blank)
    doc.close()

    def fake_gen(prompt: str, image: Path) -> str:
        assert image.is_file()
        return json.dumps(
            {
                "figure": "Figure 1",
                "page_index": 0,
                "panels": [
                    {
                        "panel": "A",
                        "left_pct": 5,
                        "top_pct": 5,
                        "width_pct": 40,
                        "height_pct": 40,
                        "confidence": 0.9,
                    },
                    {
                        "panel": "B",
                        "left_pct": 50,
                        "top_pct": 5,
                        "width_pct": 40,
                        "height_pct": 40,
                        "confidence": 0.8,
                    },
                ],
            }
        )

    import pre_peer_checker.llm.panel_map_assist as assist

    monkeypatch.setattr(
        assist,
        "select_vlm_backend",
        lambda *a, **k: CallableVlmBackend(fake_gen),
    )
    regions, status = extract_panel_regions_vector_then_vlm(
        [blank], vlm_assist=True, min_vector_panels=1
    )
    assert status["vlm_used"] is True
    assert status["n_vlm"] >= 2
    assert {r["panel"] for r in regions} >= {"A", "B"}
    assert all(r.get("geometry_source") == "vlm" for r in regions)


def test_vector_succeeds_skips_vlm(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "fig.pdf"
    build_synthetic_multipanel_pdf(pdf)

    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("VLM should not run when vector has panels")

    import pre_peer_checker.llm.panel_map_assist as assist

    monkeypatch.setattr(assist, "select_vlm_backend", boom)
    regions, status = extract_panel_regions_vector_then_vlm(
        [pdf], vlm_assist=True, min_vector_panels=1
    )
    assert called["n"] == 0
    assert status["vlm_used"] is False
    assert status["n_vector"] >= 2
    assert regions


def test_probe_vlm_backends_shape():
    infos = probe_vlm_backends()
    assert {i.name for i in infos} >= {"mlx-vlm", "transformers-vlm"}


def test_synthetic_raster_letter_pdf_has_no_vector_labels(tmp_path: Path):
    """Image-drawn A/B must not be picked up by text-span vector geometry."""
    import importlib.util

    from pre_peer_checker.parsers.pdf_panel_geometry import extract_panel_regions_from_pdf

    spec = importlib.util.spec_from_file_location(
        "dev_vlm_panel_map_verify",
        Path(__file__).resolve().parents[1] / "scripts" / "dev_vlm_panel_map_verify.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pdf = mod._build_synthetic_raster_letter_pdf(tmp_path / "raster_letters.pdf")
    assert extract_panel_regions_from_pdf(pdf, max_pages=1) == []
