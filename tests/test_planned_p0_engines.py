"""Unit tests for remaining planned-P0 engines (G1/G2/G3/G6/G12)."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.data.group_vectors import extract_group_vectors
from pre_peer_checker.engine.methods_claim import warnings_from_methods_claims
from pre_peer_checker.engine.numeric_crossref import warnings_from_numeric_crossref
from pre_peer_checker.engine.shared_control import warnings_from_shared_controls
from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.imaging.blot_lane import scan_blot_lane_reuse
from pre_peer_checker.imaging.partial_match import partial_containment_score
from pre_peer_checker.pipeline.orchestrator import run_verification

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "fixtures" / "synthetic"


def test_partial_containment_fixture():
    m = partial_containment_score(
        SYN / "image_partial" / "manuscript" / "panel_s1.png",
        SYN / "image_partial" / "corpus" / "past_paper_crop.png",
    )
    assert m is not None
    assert m.score >= 0.92


def test_image_partial_orchestrator():
    result = run_verification(
        [SYN / "image_partial" / "manuscript"],
        corpus=[SYN / "image_partial" / "corpus"],
    )
    assert any(
        w.metadata.get("pattern_id") == "P-IMAGE-PARTIAL-REUSE" for w in result.warnings
    )


def test_blot_lane_orchestrator():
    result = run_verification([SYN / "blot_lane"])
    assert any(
        w.metadata.get("pattern_id") == "P-BLOT-LANE-REUSE" for w in result.warnings
    )
    scan = scan_blot_lane_reuse(
        [SYN / "blot_lane" / "panel_a.png", SYN / "blot_lane" / "panel_b.png"]
    )
    assert len(scan.warnings) == 1


def test_vector_subset_independence_claim():
    va = extract_group_vectors(SYN / "vector_subset" / "fig1a" / "quant_panel.csv")
    vb = extract_group_vectors(SYN / "vector_subset" / "fig1b" / "quant_panel.csv")
    warns = warnings_from_shared_controls(va + vb, independence_claimed=True)
    assert any(
        w.metadata.get("pattern_id") == "P-VECTOR-SUBSET-UNDISCLOSED" for w in warns
    )
    # Without independence claim, same vectors stay SHARED
    shared = warnings_from_shared_controls(va + vb, independence_claimed=False)
    assert any(
        w.metadata.get("pattern_id") == "P-SHARED-CONTROL-UNDISCLOSED" for w in shared
    )


def test_vector_subset_orchestrator():
    result = run_verification([SYN / "vector_subset"])
    assert any(
        w.metadata.get("pattern_id") == "P-VECTOR-SUBSET-UNDISCLOSED"
        for w in result.warnings
    )


def test_numeric_crossref_orchestrator():
    result = run_verification([SYN / "numeric_crossref"])
    assert any(
        w.metadata.get("pattern_id") == "P-NUMERIC-CROSSREF-MISMATCH"
        for w in result.warnings
    )
    texts = [
        "Figure 1. The mean intensity of mutant was 12.5 ± 0.4.",
    ]
    vecs = extract_group_vectors(SYN / "numeric_crossref" / "fig1a" / "quant_panel.csv")
    asserts = warnings_from_numeric_crossref(texts, vecs)
    assert asserts


def test_methods_claim_unit_and_orchestrator():
    texts = [
        "Methods. Complexes were combined at a ratio of 1:1 prior to imaging.",
        "Figure 1. Binding assay. Complexes assembled at 1:2 stoichiometry.",
    ]
    warns = warnings_from_methods_claims(texts)
    assert any(
        w.metadata.get("pattern_id") == "P-METHODS-CLAIM-MISMATCH" for w in warns
    )
    result = run_verification([SYN / "methods_claim"])
    assert any(
        w.metadata.get("pattern_id") == "P-METHODS-CLAIM-MISMATCH"
        for w in result.warnings
    )


def test_p0_gold_eval_smoke(tmp_path: Path):
    for case in (
        "image_partial",
        "blot_lane",
        "vector_subset",
        "numeric_crossref",
        "methods_claim",
    ):
        kwargs: dict = {"warnings_out": tmp_path / f"{case}.json"}
        if case == "image_partial":
            kwargs["input_paths"] = [SYN / "image_partial" / "manuscript"]
            kwargs["corpus_paths"] = [SYN / "image_partial" / "corpus"]
        else:
            kwargs["input_paths"] = [SYN / case]
        report = run_and_evaluate(case, **kwargs)
        assert report["required_recall"] == 1.0, (case, report)
