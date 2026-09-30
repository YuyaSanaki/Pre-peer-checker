"""Tests for figure PDF name → figure key mapping."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.parsers.figure_panel_labels import (
    _figure_num_from_pdf_name,
    collect_panel_labels_by_figure,
    panel_labels_from_pdf_detailed,
)
from pre_peer_checker.parsers.raster_figure_panel_ocr import PdfPageRasterOcr, RasterPanelOcrResult

_OCR_PDF = "pre_peer_checker.parsers.raster_figure_panel_ocr.raster_panel_analyses_from_pdf"


def _ocr_pages(letters: set[str], engine: str, **fields):
    analysis = RasterPanelOcrResult(letters=set(letters), engines=engine.split("+"), **fields)
    return lambda path, max_pages=2: [
        PdfPageRasterOcr(0, analysis, (0.0, 0.0, 100.0, 100.0), (100.0, 100.0))
    ]


def test_fig_s1_is_supplementary_not_main():
    """Regression: FigS1.pdf must not match figs?(\\d+) as main figure 1."""
    assert _figure_num_from_pdf_name(Path("FigS1.pdf")) == "S1"
    assert _figure_num_from_pdf_name(Path("figs1.pdf")) == "S1"
    assert _figure_num_from_pdf_name(Path("FigS12_panel.pdf")) == "S12"
    assert _figure_num_from_pdf_name(Path("FigureS2.jpg")) == "S2"


def test_fig_supp_variants():
    assert _figure_num_from_pdf_name(Path("FigSupp2_rev.pdf")) == "S2"
    assert _figure_num_from_pdf_name(Path("FigSup3.pdf")) == "S3"
    assert _figure_num_from_pdf_name(Path("FigureSupp1.pdf")) == "S1"


def test_main_figure_names():
    assert _figure_num_from_pdf_name(Path("Fig1.pdf")) == "1"
    assert _figure_num_from_pdf_name(Path("Fig2_rev.pdf")) == "2"
    assert _figure_num_from_pdf_name(Path("Figure3.pdf")) == "3"
    assert _figure_num_from_pdf_name(Path("fig1_final.pdf")) == "1"
    assert _figure_num_from_pdf_name(Path("Fig1.png")) == "1"
    assert _figure_num_from_pdf_name(Path("FigureS2.jpg")) == "S2"


def test_unrelated_name():
    assert _figure_num_from_pdf_name(Path("RplotBetaExp.pdf")) is None
    assert _figure_num_from_pdf_name(Path("methods.pdf")) is None


def test_vector_fig1_labels(monkeypatch):
    fig = Path("fixtures/synthetic/ref_label/Fig1.pdf")
    labels, meta = panel_labels_from_pdf_detailed(fig, raster_fallback=False)
    assert labels == ["A", "B", "C"]
    assert meta.source == "vector"
    assert meta.needs_review is False


def test_raster_fallback_when_vector_empty(monkeypatch):
    fig = Path("fixtures/synthetic/ref_label/Fig1.pdf")
    monkeypatch.setattr(
        "pre_peer_checker.parsers.figure_panel_labels._vector_panel_labels",
        lambda _page: [],
    )
    monkeypatch.setattr(
        _OCR_PDF,
        _ocr_pages({"D", "E"}, "vision+florence"),
    )
    labels, meta = panel_labels_from_pdf_detailed(fig, raster_fallback=True)
    assert labels == ["D", "E"]
    assert meta.source == "raster_ocr"
    assert meta.needs_review is True
    assert meta.ocr_engine == "vision+florence"


def test_raster_ocr_noise_letters_are_dropped(monkeypatch):
    """Axis/legend letters far from the panel run must not become panel labels."""
    fig = Path("fixtures/synthetic/ref_label/Fig1.pdf")
    monkeypatch.setattr(
        "pre_peer_checker.parsers.figure_panel_labels._vector_panel_labels",
        lambda _page: [],
    )
    monkeypatch.setattr(
        _OCR_PDF,
        _ocr_pages(set("ABCDFGHJK") | {"O", "U", "Г"}, "vision"),
    )
    labels, meta = panel_labels_from_pdf_detailed(fig, raster_fallback=True)
    assert labels == list("ABCDFGHJK")
    assert meta.dropped == ["O", "U", "Г"]


def test_vector_gap_triggers_ocr_and_unions(monkeypatch):
    """A letter missing inside the vector span is likely baked into the artwork."""
    fig = Path("fixtures/synthetic/ref_label/Fig1.pdf")
    monkeypatch.setattr(
        "pre_peer_checker.parsers.figure_panel_labels._vector_panel_labels",
        lambda _page: ["A", "B", "D"],
    )
    monkeypatch.setattr(
        _OCR_PDF,
        _ocr_pages({"C", "D"}, "florence"),
    )
    labels, meta = panel_labels_from_pdf_detailed(fig, raster_fallback=True)
    assert labels == ["A", "B", "C", "D"]
    assert meta.source == "mixed"
    assert meta.needs_review is True


def test_raster_pdf_regions_are_page_points_fitted_to_photos(monkeypatch):
    """A figure PDF holding one bitmap gets panel boxes in page points."""
    fig = Path("fixtures/synthetic/ref_label/Fig1.pdf")
    monkeypatch.setattr(
        "pre_peer_checker.parsers.figure_panel_labels._vector_panel_labels",
        lambda _page: [],
    )
    analysis = RasterPanelOcrResult(
        letters={"a", "b"},
        engines=["florence"],
        width=400,
        height=200,
        crops=[
            {"panel": "a", "box": [0.0, 0.0, 200.0, 200.0]},
            {"panel": "b", "box": [200.0, 0.0, 400.0, 200.0]},
        ],
        label_boxes={"a": [2.0, 5.0, 14.0, 22.0], "b": [215.0, 35.0, 228.0, 52.0]},
        photo_boxes=[[20.0, 30.0, 190.0, 190.0], [210.0, 30.0, 380.0, 190.0]],
    )
    monkeypatch.setattr(
        _OCR_PDF,
        lambda path, max_pages=2: [
            PdfPageRasterOcr(0, analysis, (100.0, 50.0, 300.0, 150.0), (400.0, 300.0))
        ],
    )
    _labels, meta = panel_labels_from_pdf_detailed(fig, raster_fallback=True)
    boxes = {r["panel"]: (r["x0"], r["y0"], r["x1"], r["y1"]) for r in meta.regions}
    assert set(boxes) == {"A", "B"}
    assert boxes["A"] == (101.0, 52.5, 195.0, 145.0)
    assert boxes["B"] == (205.0, 65.0, 290.0, 145.0)
    assert meta.regions[0]["pct"]["left_pct"] == 101.0 / 400.0 * 100


def test_complete_vector_skips_ocr(monkeypatch):
    fig = Path("fixtures/synthetic/ref_label/Fig1.pdf")

    def boom(*_a, **_k):
        raise AssertionError("OCR must not run when vector labels are complete")

    monkeypatch.setattr(
        _OCR_PDF,
        boom,
    )
    labels, meta = panel_labels_from_pdf_detailed(fig, raster_fallback=True)
    assert labels == ["A", "B", "C"]
    assert meta.source == "vector"
    assert meta.needs_review is False


def test_collect_by_figure_vector():
    fig = Path("fixtures/synthetic/ref_label/Fig1.pdf")
    by_fig = collect_panel_labels_by_figure([fig], raster_fallback=False)
    assert by_fig.get("1") == ["A", "B", "C"]


def test_chunk_prompt_marks_raster_review():
    from pre_peer_checker.parsers.figure_chunks import FigureChunk
    from pre_peer_checker.parsers.figure_panel_labels import (
        PanelLabelProvenance,
        attach_panel_labels_to_chunks,
    )

    ch = FigureChunk(figure_id="Figure 1", figure_num="1")
    prov = PanelLabelProvenance(labels=["A"], source="raster_ocr", needs_review=True)
    attach_panel_labels_to_chunks([ch], {"1": ["A"]}, meta_by_figure={"1": prov})
    body = ch.prompt_body()
    assert "raster OCR" in body
    assert "A" in body
