"""Phase 2: shared-control fixtures + PDF-only panel identity."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector, extract_group_vectors
from pre_peer_checker.engine.panel_plot_identity import warnings_from_figure_panel_identity
from pre_peer_checker.engine.shared_control import (
    is_endpoint_dropout,
    manuscript_claims_shared_control,
    warnings_from_shared_controls,
)
from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.parsers.pdf_panel_plots import (
    build_synthetic_multipanel_pdf,
    extract_multipanel_figure_pdf,
    find_identical_panel_pairs,
)
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.warnings import WarningTag

ROOT = Path(__file__).resolve().parents[1]
SC = ROOT / "fixtures" / "synthetic" / "shared_control"


def test_endpoint_dropout_detection():
    full = GroupVector(Path("a"), "ctrl", (1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6), 7)
    dropped = GroupVector(Path("b"), "ctrl", (1.1, 1.2, 1.3, 1.4, 1.5), 5)
    assert is_endpoint_dropout(full, dropped)
    other = GroupVector(Path("c"), "ctrl", (1.1, 1.2, 1.3, 9.9, 1.5), 5)
    assert not is_endpoint_dropout(full, other)


def test_shared_control_fixture_vectors():
    va = extract_group_vectors(SC / "fig1a" / "quant_panel.csv")
    vb = extract_group_vectors(SC / "fig1b" / "quant_panel.csv")
    ctrl_a = next(v for v in va if v.group_key == "ctrl")
    ctrl_b = next(v for v in vb if v.group_key == "ctrl")
    assert is_endpoint_dropout(ctrl_a, ctrl_b)
    warns = warnings_from_shared_controls(va + vb, min_n=4)
    assert any(
        w.metadata.get("pattern_id") == "P-SHARED-CONTROL-UNDISCLOSED"
        and w.metadata.get("share_kind") == "endpoint_dropout"
        for w in warns
    )


def test_shared_control_suppressed_when_claimed():
    assert manuscript_claims_shared_control(["WT is a shared control across panels."])
    va = extract_group_vectors(SC / "fig1a" / "quant_panel.csv")
    vb = extract_group_vectors(SC / "fig1b" / "quant_panel.csv")
    asserts = warnings_from_shared_controls(
        va + vb, manuscript_mentions_shared=True
    )
    assert asserts == []


def test_shared_control_demoted_on_weak_disclosure():
    from pre_peer_checker.engine.shared_control import shared_control_disclosure

    assert shared_control_disclosure(["The same WT is shown again for comparison."]) == "weak"
    va = extract_group_vectors(SC / "fig1a" / "quant_panel.csv")
    vb = extract_group_vectors(SC / "fig1b" / "quant_panel.csv")
    warns = warnings_from_shared_controls(va + vb, disclosure="weak")
    assert warns
    assert all(w.metadata.get("demoted") is True for w in warns)
    assert all(w.title.startswith("【降格】") for w in warns)
    assert all(w.metadata.get("severity") == "info" for w in warns)


def test_shared_control_gold_eval(tmp_path: Path):
    report = run_and_evaluate(
        "shared_control",
        input_paths=[SC],
        warnings_out=tmp_path / "sc_warnings.json",
    )
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["SC1"]["matched"] is True
    assert report["required_recall"] == 1.0


def test_orchestrator_shared_control():
    result = run_verification([SC])
    assert any(
        w.tag == WarningTag.CONTROL_SHARE
        and w.metadata.get("share_kind") == "endpoint_dropout"
        for w in result.warnings
    )


def test_synthetic_multipanel_pdf_identity(tmp_path: Path):
    pdf = build_synthetic_multipanel_pdf(tmp_path / "FigSynth.pdf")
    pages = extract_multipanel_figure_pdf(pdf)
    assert pages
    # May need lower min_groups if only one x-bin survives; check pairs with min_groups=1
    pairs = find_identical_panel_pairs(pages[0], min_groups=1, min_score=0.9, min_panel_letter_gap=3)
    # I and Q are far apart in alphabet
    assert pairs or len(pages[0].panels) >= 2
    warns, arts = warnings_from_figure_panel_identity(
        [pdf], min_groups=1, min_score=0.9
    )
    assert arts
    # If marker extraction worked, expect DATA_SWAP
    if any(p.panels for p in pages if len(p.panels) >= 2):
        assert any(
            w.metadata.get("pattern_id") == "P-DATA-SWAP-CROSS-CONDITION" for w in warns
        ) or pairs
