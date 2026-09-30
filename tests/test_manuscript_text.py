"""Manuscript text from PDF (no Word file): legends, body, references."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pre_peer_checker.parsers.legend_struct import extract_structured_legends
from pre_peer_checker.parsers.manuscript_text import (
    _span_text,
    extract_pdf_text,
    manuscript_paragraphs,
    ocr_available,
    select_manuscript_pdfs,
)
from pre_peer_checker.parsers.references import parse_references_from_paragraphs

_BODY = (
    "Muscle fibers were induced from stem cells and quantified across several "
    "independent experiments, as described in prior work [1]. The induction was "
    "robust and reproducible in every culture we examined in this study."
)
_LEGEND = (
    "Figure 1. Induction of muscle fibers. (A) Protocol overview. (B) Quantification "
    "of fiber area (n = 5). (C) Relative mRNA level (n = 3)."
)


def _box(page: pymupdf.Page, y: float, text: str, size: float = 10.0, height: float = 90) -> float:
    rect = pymupdf.Rect(60, y, 540, y + height)
    page.insert_textbox(rect, text, fontsize=size, fontname="helv")
    return y + height + 12


def _write_manuscript(path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=600, height=800)
        page.insert_text((60, 30), "Journal of Tests | Article", fontsize=8)
        page.insert_text((290, 780), str(i + 1), fontsize=8)
        y = 60.0
        if i == 0:
            y = _box(page, y, "Results", size=12, height=30)
            y = _box(page, y, _BODY)
            y = _box(page, y, _LEGEND, size=8, height=60)
            y = _box(page, y, _BODY.replace("[1]", "[2]"))
        elif i == 1:
            y = _box(page, y, "Methods", size=12, height=30)
            y = _box(page, y, "Statistics were computed with a two-tailed t-test. " + _BODY)
            for _ in range(4):
                y = _box(page, y, _BODY, height=60)
        elif i == 2:
            for _ in range(8):
                y = _box(page, y, _BODY, height=60)
        else:
            y = _box(page, y, "References", size=12, height=30)
            y = _box(page, y, "1. Adams, A. First paper on muscle induction. J. Demo 1, 1-2 (2019).", height=30)
            y = _box(page, y, "2. Baker, B. Second paper on fiber area. J. Demo 2, 3-4 (2020).", height=30)
    doc.save(str(path))
    doc.close()
    return path


def _write_figure_only(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    for j, label in enumerate(["a", "b", "DAPI", "Day 0", "Day 15", "10", "20"]):
        page.insert_text((80 + 60 * j, 100 + 10 * j), label, fontsize=7)
    doc.save(str(path))
    doc.close()
    return path


def test_pdf_paragraphs_drop_running_headers_and_relocate_legends(tmp_path: Path) -> None:
    pdf = _write_manuscript(tmp_path / "paper.pdf")
    pt = extract_pdf_text(pdf)
    paras = pt.paragraphs

    assert not any("Journal of Tests" in p for p in paras)
    assert not any(p.strip().isdigit() for p in paras)
    assert pt.legends_relocated == 1
    # Legends move into their own section before References, body stays contiguous.
    i_leg = paras.index("Figure legends")
    i_ref = paras.index("References")
    assert i_leg < i_ref
    assert paras[i_leg + 1].startswith("Figure 1.")
    assert "Figure 1." not in " ".join(paras[:i_leg])


def test_legends_and_references_from_pdf(tmp_path: Path) -> None:
    pdf = _write_manuscript(tmp_path / "paper.pdf")

    legends = extract_structured_legends(pdf)
    assert [leg.figure for leg in legends] == ["Figure 1"]
    ns = {(pn.panel, pn.n) for pn in legends[0].panel_ns}
    assert ("B", 5) in ns and ("C", 3) in ns

    bundle = parse_references_from_paragraphs(manuscript_paragraphs(pdf))
    assert [e.key for e in bundle.entries] == ["1", "2"]
    assert {k for c in bundle.in_text for k in c.keys} >= {"1", "2"}


def test_legend_cut_at_page_end_resumes_on_next_page(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    y = _box(page, 60.0, "Results", size=12, height=30)
    y = _box(page, y, _BODY)
    _box(page, y, "Figure 1. Induction of muscle fibers. (A) Protocol overview. (B) Fiber area in", size=8, height=40)
    page = doc.new_page(width=600, height=800)
    y = _box(page, 60.0, _BODY.replace("[1]", "[2]"))
    _box(page, y, "control and mutant cultures (n = 5). (C) Relative mRNA level (n = 3).", size=8, height=40)
    for _ in range(2):
        page = doc.new_page(width=600, height=800)
        _box(page, 60.0, _BODY)
    pdf = tmp_path / "split.pdf"
    doc.save(str(pdf))
    doc.close()

    legends = extract_structured_legends(pdf)
    assert [leg.figure for leg in legends] == ["Figure 1"]
    assert {(pn.panel, pn.n) for pn in legends[0].panel_ns} == {("B", 5), ("C", 3)}


def test_superscript_citations_become_brackets() -> None:
    body = {"size": 8.0, "flags": 4}
    sup = {"size": 5.0, "flags": 5}
    spans = [
        {**body, "text": "human cells"},
        {**sup, "text": "36"},
        {**sup, "text": "–"},
        {**sup, "text": "38"},
        {**body, "text": ". Area was 5 µm"},
        {**sup, "text": "2"},
        {**body, "text": " per crucial"},
        {**sup, "text": "12"},
    ]
    assert _span_text(spans, 8.0) == "human cells[36–38]. Area was 5 µm2 per crucial[12]"


def test_select_manuscript_pdf_skips_figures_and_cited_papers(tmp_path: Path) -> None:
    ms = _write_manuscript(tmp_path / "paper.pdf")
    fig = _write_figure_only(tmp_path / "Fig1.pdf")
    cited_dir = tmp_path / "cited"
    cited_dir.mkdir()
    cited = _write_manuscript(cited_dir / "prior_work.pdf")

    assert select_manuscript_pdfs([fig, ms, cited], exclude_under=[cited_dir]) == [ms]
    assert select_manuscript_pdfs([fig]) == []


def test_image_only_page_is_reported(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200), False)
    pix.clear_with(255)
    page.insert_image(pymupdf.Rect(50, 50, 550, 750), pixmap=pix)
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()

    pt = extract_pdf_text(path)
    assert pt.ocr_pages == [1] or pt.image_only_pages == [1]


@pytest.mark.skipif(not ocr_available(), reason="Tesseract not installed")
def test_scanned_page_is_read_by_ocr(tmp_path: Path) -> None:
    src = pymupdf.open()
    page = src.new_page(width=600, height=800)
    page.insert_textbox(pymupdf.Rect(60, 60, 540, 400), _LEGEND, fontsize=16, fontname="helv")
    pix = page.get_pixmap(dpi=200)
    src.close()

    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page(width=600, height=800).insert_image(
            pymupdf.Rect(0, 0, 600, 800), pixmap=pix
        )
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()

    pt = extract_pdf_text(path)
    assert pt.ocr_pages == [1, 2]
    text = " ".join(pt.paragraphs)
    assert "Induction of muscle fibers" in text
    assert "n = 5" in text


def test_pipeline_reads_pdf_manuscript_without_word(tmp_path: Path) -> None:
    from pre_peer_checker.pipeline.orchestrator import run_verification

    case = tmp_path / "case"
    case.mkdir()
    _write_manuscript(case / "paper.pdf")

    result = run_verification([case])
    arts = result.artifacts
    assert arts["manuscript_source"]["kind"] == "pdf"
    assert {(pn["panel"], pn["n"]) for pn in arts["legend_panel_ns"]} >= {("B", 5), ("C", 3)}
    assert len(arts["reference_bundle"]["entries"]) == 2
    check = {c["id"]: c for c in arts["run_coverage"]["checks"]}["word_legend"]
    assert check["status"] == "ran"
    assert "PDF 原稿" in check["detail"]
