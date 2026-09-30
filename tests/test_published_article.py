"""Published-article PDFs: typeset legends, Extended Data keys, figures cropped from the page."""

from __future__ import annotations

from pathlib import Path

import fitz

from pre_peer_checker.engine.ref_label import extract_fig_panel_refs
from pre_peer_checker.parsers.article_figures import export_article_figures, find_article_figures
from pre_peer_checker.parsers.figure_chunks import figure_label, figure_num_key
from pre_peer_checker.parsers.figure_panel_labels import _figure_num_from_pdf_name
from pre_peer_checker.parsers.legend_struct import (
    clean_legend_text,
    extract_figure_captions_from_pdf_text,
    parse_panel_ns,
)
from pre_peer_checker.parsers.pdf_panel_plots import _panel_labels

_PROSE = (
    "The main text of the letter runs in two columns and describes the experiments in full "
    "sentences so that the layout detector treats it as body text rather than artwork. "
) * 12


def test_clean_legend_text_drops_invisible_spaces() -> None:
    assert clean_legend_text("n =\u200b 22, P <\u2009 0.01") == "n = 22, P <  0.01"


def test_comma_openers_and_postfix_n_with_units() -> None:
    text = (
        "Loss of the receptor blocks clearance. a, b, Wing discs of control (a) and mutant (b) "
        "larvae. c, Clone size in control (a) (n\u200b = 18 discs) and mutant (b) (n = 21 discs). "
        "d–f, Staining of mutant clones (n = 12). Scale bars, 20 µm."
    )
    rows = {(r.panel, r.group, r.n) for r in parse_panel_ns("Figure 2", text)}
    assert ("C", "a", 18) in rows
    assert ("C", "b", 21) in rows
    assert {("D", "", 12), ("E", "", 12), ("F", "", 12)} <= rows


def test_extended_data_keys() -> None:
    assert figure_num_key("Extended Data Fig. 3") == "ED3"
    assert figure_num_key("Extended Data Figure 10") == "ED10"
    assert figure_num_key("Figure 3") == "3"
    assert figure_label("ED3") == "Extended Data Figure 3"
    assert figure_label("2") == "Figure 2"
    assert _figure_num_from_pdf_name(Path("Extended_Data_Fig7.pdf")) == "ED7"


def test_pdf_captions_include_extended_data_and_stop_at_footer() -> None:
    raw = (
        "Main text paragraph ends here.\n"
        "Figure 1 | Control panel. a, Discs (n = 5).\n"
        "Extended Data Figure 3 | Extra analysis. a, b, Mutant discs (n = 9).\n"
        "© 2017 Publisher. All rights reserved.\n"
        "Letter RESEARCH\n"
    )
    caps = dict(extract_figure_captions_from_pdf_text(raw))
    assert set(caps) == {"Figure 1", "Extended Data Figure 3"}
    assert caps["Extended Data Figure 3"].endswith("(n = 9).")


def test_fig_panel_refs_keep_extended_data_apart_and_skip_words() -> None:
    text = (
        "as shown in Figure 2 and Extended Data Fig. 3k; Supplementary Fig. 4h (Fig. 1b). "
        "Fig. 5 shows Figure 6B-D and Fig. 7 (A, C and E), Fig. S2c."
    )
    refs = extract_fig_panel_refs([text])
    assert refs == {
        "ED3": {"K"},
        "S4": {"H"},
        "1": {"B"},
        "6": {"B", "C", "D"},
        "7": {"A", "C", "E"},
        "S2": {"C"},
    }


def _letter_page_pdf(path: Path) -> Path:
    """Page 1: prose left, Figure 1 right; page 2: full-width Extended Data figure."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(fitz.Rect(40, 60, 290, 740), _PROSE, fontsize=9)
    for x0 in (315, 440):
        page.draw_rect(fitz.Rect(x0, 95, x0 + 110, 290), color=(0, 0, 0), fill=(0.2, 0.6, 0.2))
    page.insert_text((312, 92), "a", fontname="hebo", fontsize=8)
    page.insert_text((437, 92), "b", fontname="hebo", fontsize=8)
    page.insert_textbox(
        fitz.Rect(310, 305, 560, 400),
        "Figure 1 | Caption words describing the figure. a, Control discs (n = 5). "
        "b, Mutant discs (n = 7).",
        fontsize=8,
    )
    page2 = doc.new_page(width=612, height=792)
    for x0 in (50, 220, 390):
        page2.draw_rect(fitz.Rect(x0, 70, x0 + 150, 380), color=(0, 0, 0), fill=(0.3, 0.3, 0.8))
    page2.insert_textbox(
        fitz.Rect(40, 400, 570, 460),
        "Extended Data Figure 1 | Supporting data for the screen. a–c, Discs (n = 4).",
        fontsize=8,
    )
    doc.save(str(path))
    doc.close()
    return path


def test_article_figures_are_cropped_per_caption_column(tmp_path: Path) -> None:
    pdf = _letter_page_pdf(tmp_path / "letter.pdf")
    figs = {f.key: f for f in find_article_figures(pdf)}
    assert set(figs) == {"1", "ED1"}
    fig1 = figs["1"]
    assert fig1.page_index == 0
    assert fig1.clip[0] >= 300  # prose column excluded
    assert fig1.clip[3] <= 305  # caption excluded
    assert figs["ED1"].page_index == 1
    assert figs["ED1"].label == "Extended Data Figure 1"

    paths = export_article_figures(pdf, cache_dir=tmp_path / "cache")
    assert [p.name for p in paths] == ["Fig1.pdf", "Extended_Data_Fig1.pdf"]
    with fitz.open(paths[0]) as crop:
        text = crop[0].get_text()
        assert "Caption" not in text and "letter runs" not in text
        assert set(_panel_labels(crop[0])) == {"A", "B"}
    assert export_article_figures(pdf, cache_dir=tmp_path / "cache") == paths


def test_bold_lowercase_labels_need_a_letter_run(tmp_path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((10, 20), "a", fontname="hebo", fontsize=8)
    page.insert_text((150, 20), "b", fontname="hebo", fontsize=8)
    page.insert_text((10, 120), "c", fontname="hebo", fontsize=8)
    page.insert_text((60, 150), "x", fontname="helv", fontsize=8)
    assert set(_panel_labels(page)) == {"A", "B", "C"}

    lone = doc.new_page(width=300, height=200)
    lone.insert_text((10, 20), "n", fontname="hebo", fontsize=8)
    assert _panel_labels(lone) == {}
    doc.close()
