"""P-SCALE-BAR-LEGEND-MISMATCH / P-ANTIBODY-HOST-MISMATCH."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.engine.antibody_host import (
    extract_antibody_mentions,
    warnings_from_antibody_hosts,
)
from pre_peer_checker.engine.scale_bar_legend import (
    legend_scale_claim,
    warnings_from_scale_bar_labels,
    warnings_from_scale_bar_legend,
)
from pre_peer_checker.parsers.scale_labels import (
    FigureScaleLabel,
    parse_scale_label,
    scale_labels_from_ocr_texts,
    vector_scale_labels_from_pdf,
)

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "fixtures" / "synthetic"


# --- scale bar ---------------------------------------------------------------


def test_legend_scale_claim_styles():
    c = legend_scale_claim("a, Images. Scale bar, 100 μm. b, scans (scale bar, 1 mm).")
    assert c.values_um == [100.0, 1000.0]
    assert not c.flexible
    assert legend_scale_claim("Scale bars, 100µm (B) and 30µm (E).").values_um == [100.0, 30.0]
    assert legend_scale_claim("Images. Bars, 20 µm. Error bars, s.d.").values_um == [20.0]
    assert legend_scale_claim("Scale bar = 0.5 µm; inset 200 nm.").values_um == [0.5, 0.2]


def test_legend_scale_claim_ignores_non_scale_lengths():
    c = legend_scale_claim("Error bars represent s.e.m. Timescale is mm:ss. Cells 30 µm apart.")
    assert c.values_um == []


def test_legend_scale_claim_flexible():
    c = legend_scale_claim("Scale bars represent 10 µm unless otherwise indicated.")
    assert c.values_um == [10.0]
    assert c.flexible


def test_parse_scale_label():
    assert parse_scale_label("10µm") == (10.0, "um")
    assert parse_scale_label("50 μm") == (50.0, "um")
    assert parse_scale_label("2 mm") == (2000.0, "mm")
    assert parse_scale_label("200 nm") == (0.2, "nm")
    assert parse_scale_label("10 µM") is None
    assert parse_scale_label("Distance (µm)") is None
    assert parse_scale_label("Merge 50 µm") is None
    assert parse_scale_label("Merge 50 µm", ocr=True) == (50.0, "um")
    assert parse_scale_label("50 pm", ocr=True) == (50.0, "um")


def test_vector_labels_from_fixture_pdf():
    labels = vector_scale_labels_from_pdf(SYN / "scale_bar_legend" / "Fig1.pdf")
    assert {lb.value_um for lb in labels} == {100.0}
    assert all(lb.origin == "vector" for lb in labels)


def test_scale_bar_mismatch_fixture():
    fig = SYN / "scale_bar_legend" / "Fig1.pdf"
    legend = ("Figure 1", "Figure 1. Confocal images. (A) Control. (B) Mutant. Scale bars, 50 µm.")
    warns, art = warnings_from_scale_bar_legend([legend], [fig], ocr=False)
    assert len(warns) == 1
    w = warns[0]
    assert w.metadata["pattern_id"] == "P-SCALE-BAR-LEGEND-MISMATCH"
    assert w.metadata["figure_values_um"] == [100.0]
    assert w.metadata["legend_values_um"] == [50.0]
    assert "Figure 1" in w.location
    assert art["figure_labels"]["1"]


def test_scale_bar_match_and_flexible_are_silent():
    fig = SYN / "scale_bar_legend" / "Fig1.pdf"
    ok = ("Figure 1", "Figure 1. Images. Scale bars, 100 µm.")
    assert warnings_from_scale_bar_legend([ok], [fig], ocr=False)[0] == []
    flex = ("Figure 1", "Figure 1. Images. Scale bars, 50 µm unless otherwise indicated.")
    assert warnings_from_scale_bar_legend([flex], [fig], ocr=False)[0] == []
    other_fig = ("Figure 2", "Figure 2. Images. Scale bars, 50 µm.")
    assert warnings_from_scale_bar_legend([other_fig], [fig], ocr=False)[0] == []


def test_symbol_font_mm_counts_as_um():
    claim = legend_scale_claim("Scale bar, 50 µm.")
    lb = FigureScaleLabel(50000.0, "50 mm", "mm", "Fig1.pdf", 0, "vector")
    assert warnings_from_scale_bar_labels({"1": claim}, {"1": [lb]}) == []


def test_ocr_labels_flagged_for_review():
    labels = scale_labels_from_ocr_texts(
        [
            {"text": "100 um", "box": [1, 2, 3, 4]},
            {"text": "100 um", "box": [1.5, 2, 3.5, 4]},
            {"text": "Control", "box": [0, 0, 1, 1]},
        ],
        source="Fig2.png",
    )
    assert [lb.value_um for lb in labels] == [100.0]
    claim = legend_scale_claim("Scale bars, 50 µm.")
    warns = warnings_from_scale_bar_labels({"2": claim}, {"2": labels})
    assert len(warns) == 1
    assert warns[0].metadata["needs_review"] is True
    assert "OCR" in warns[0].reason


# --- antibody host -----------------------------------------------------------


def _checks(paragraphs: list[str]) -> list[str]:
    return sorted(w.metadata["check"] for w in warnings_from_antibody_hosts(paragraphs))


def test_antibody_fixture_paragraph():
    para = (
        "Immunostaining. Primary antibodies were rabbit anti-GFP (Abcam, ab290; 1:500) and "
        "mouse anti-Dcp-1 (Atlas Antibodies, HPA012345; 1:200). Secondary antibodies were goat "
        "anti-rabbit IgG Alexa Fluor 488 and goat anti-rat IgG Alexa Fluor 546."
    )
    warns = warnings_from_antibody_hosts([para])
    by_check = {w.metadata["check"]: w for w in warns}
    assert set(by_check) == {"catalog_host", "secondary_unmatched"}
    assert by_check["catalog_host"].metadata["stated_host"] == "mouse"
    assert by_check["catalog_host"].metadata["catalog_host"] == "rabbit"
    assert by_check["secondary_unmatched"].metadata["secondary_target"] == "rat"
    assert by_check["secondary_unmatched"].metadata["primary_hosts_without_secondary"] == ["mouse"]


def test_postfix_host_wording():
    para = (
        "To test the primary antibodies anti-GAB2 (HPA001368, Sigma-Aldrich), derived from murine "
        "origin, and anti-PTEN-L (MABS1680, Merck), derived from canine origin, cells were blocked. "
        "Secondary antibodies donkey anti-rabbit IgG and donkey anti-mouse IgG were applied."
    )
    ms = extract_antibody_mentions(para)
    hosts = {m.target: m.host for m in ms if not m.secondary}
    assert hosts == {"GAB2": "mouse", "PTEN-L": "dog"}
    assert _checks([para]) == ["catalog_host", "secondary_unmatched"]


def test_key_resources_table_consistent_is_silent():
    rows = [
        "Rabbit Anti-Cleaved Drosophila Dcp-1", "Cell Signaling Technology", "Cat# 9578 RRID: AB_2721060",
        "Mouse Anti-Phospho-Histone H3 (Ser10) (6G3)", "Cell Signaling Technology", "Cat# 9706",
        "Rat Anti-Dilp2", "(Géminard et al., 2009)", "N/A",
        "Rabbit Anti-Phospho-Akt (Ser473) (D9E) XP®", "Cell Signaling Technology", "Cat# 11962",
        "Goat Anti-Rabbit Secondary Antibody, Alexa Fluor 546", "Invitrogen", "Cat# A11035",
        "Goat Anti-Mouse Secondary Antibody, Alexa Fluor 405", "Invitrogen", "Cat# A-31553",
    ]
    assert _checks(rows) == []


def test_species_specific_primaries_are_not_secondaries():
    para = (
        "a three-step antibody labelling procedure was used: (1) rat anti-human IgM SSEA-3 (1:10, BD); "
        "mouse anti-human NLGN4X IgG2a (1:128); (2) mouse anti-rat IgM PE (1:200, eBiosciences); "
        "BV605 goat anti-mouse IgG (1:100, BioLegend); and rat anti-mouse CD45 (1:100)."
    )
    ms = extract_antibody_mentions(para)
    secondary = sorted(m.target for m in ms if m.secondary)
    assert secondary == ["mouse", "rat"]
    assert _checks([para]) == []


def test_unknown_host_primary_suppresses_pairing():
    para = (
        "Anti-Actin (1:1000) was from Cell Signaling Technology and mouse anti-HA (1:500) from Covance. "
        "Secondary antibodies were goat anti-rabbit IgG-HRP and goat anti-mouse IgG-HRP."
    )
    assert _checks([para]) == []


def test_inline_identity_and_self_conflicts():
    inline = "Cells were stained with rabbit anti-GFP (mouse monoclonal, clone 3E6; 1:500)."
    assert _checks([inline]) == ["inline_conflict"]
    identity = [
        "Western blots used rabbit anti-Tubulin (Abcam, ab6046) at 1:5000.",
        "Immunostaining used mouse anti-Tubulin (Abcam, ab6046) at 1:200.",
    ]
    assert "identity_conflict" in _checks(identity)
    self_sec = "Primary rabbit anti-GFP was detected with rabbit anti-rabbit IgG Alexa Fluor 488."
    assert "secondary_self" in _checks([self_sec])


def test_non_antibody_anti_words_ignored():
    para = "The anti-inflammatory and anti-apoptotic effects were antiparallel to anti-tumor growth."
    assert extract_antibody_mentions(para) == []
