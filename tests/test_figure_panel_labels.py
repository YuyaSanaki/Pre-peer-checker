"""Tests for figure PDF name → figure key mapping."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.parsers.figure_panel_labels import (
    _figure_num_from_pdf_name,
    collect_panel_labels_by_figure,
    panel_labels_from_pdf_detailed,
)


def test_fig_s1_is_supplementary_not_main():
    """Regression: FigS1.pdf must not match figs?(\\d+) as main figure 1."""
    assert _figure_num_from_pdf_name(Path("FigS1.pdf")) == "S1"
    assert _figure_num_from_pdf_name(Path("figs1.pdf")) == "S1"
    assert _figure_num_from_pdf_name(Path("FigS12_panel.pdf")) == "S12"


def test_fig_supp_variants():
    assert _figure_num_from_pdf_name(Path("FigSupp2_rev.pdf")) == "S2"
    assert _figure_num_from_pdf_name(Path("FigSup3.pdf")) == "S3"
    assert _figure_num_from_pdf_name(Path("FigureSupp1.pdf")) == "S1"


def test_main_figure_names():
    assert _figure_num_from_pdf_name(Path("Fig1.pdf")) == "1"
    assert _figure_num_from_pdf_name(Path("Fig2_rev.pdf")) == "2"
    assert _figure_num_from_pdf_name(Path("Figure3.pdf")) == "3"
    assert _figure_num_from_pdf_name(Path("fig1_final.pdf")) == "1"


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
        "pre_peer_checker.parsers.raster_figure_panel_ocr.raster_panel_labels_from_pdf",
        lambda path, max_pages=2: (["D", "E"], "vision"),
    )
    labels, meta = panel_labels_from_pdf_detailed(fig, raster_fallback=True)
    assert labels == ["D", "E"]
    assert meta.source == "raster_ocr"
    assert meta.needs_review is True
    assert meta.ocr_engine == "vision"


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
