"""Tests for figure PDF name → figure key mapping."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.parsers.figure_panel_labels import _figure_num_from_pdf_name


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
