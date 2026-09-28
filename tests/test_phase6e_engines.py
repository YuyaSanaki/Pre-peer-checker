"""§6E: microscopy meta, deferred STAT/CONFIG/REF, partial rot, G13 extensions."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.engine.multiplicity import (
    enumerate_pairwise_groups,
    extract_claimed_pairs,
    warnings_from_multiplicity_gap,
)
from pre_peer_checker.engine.exclusion_trace import (
    compare_exclusion_id_sets,
    extract_declared_exclusion_ids,
    warnings_from_exclusion_id_trace,
)
from pre_peer_checker.engine.microscopy_meta import warnings_from_microscopy_meta
from pre_peer_checker.engine.survival_count import audit_survival_curve
from pre_peer_checker.imaging.lightglue_match import (
    MIN_MATCHES_BY_FEATURES,
    verify_image_pair,
)
from pre_peer_checker.imaging.partial_match import (
    _scale_grid,
    partial_containment_score,
)
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.data.group_vectors import GroupVector

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "fixtures" / "synthetic"

CASES = (
    ("microscopy_meta", "P-SCALE-MAG-INCONSISTENT"),
    ("stat_method", "P-STAT-METHOD-INCONSISTENT"),
    ("config_annotation", "P-CONFIG-ANNOTATION-MISMATCH"),
    ("ref_label", "P-REF-LABEL-MISMATCH"),
    ("exclusion_id_trace", "P-EXCLUSION-UNDECLARED"),
    ("exclusion_id_phantom", "P-EXCLUSION-UNDECLARED"),
)


@pytest.mark.parametrize("case,pid", CASES, ids=[c[0] for c in CASES])
def test_6e_orchestrator_emits(case: str, pid: str):
    result = run_verification([SYN / case])
    assert any(
        w.metadata.get("pattern_id") == pid for w in result.warnings
    ), (case, [w.metadata.get("pattern_id") for w in result.warnings])


@pytest.mark.parametrize("case,_pid", CASES, ids=[c[0] for c in CASES])
def test_6e_gold_eval(case: str, _pid: str, tmp_path: Path):
    report = run_and_evaluate(
        case,
        input_paths=[SYN / case],
        warnings_out=tmp_path / f"{case}.json",
    )
    assert report["required_recall"] == 1.0, report


def test_microscopy_meta_unit(tmp_path: Path):
    png = tmp_path / "x.png"
    Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8)).save(png)
    (tmp_path / "x.meta.json").write_text(
        '{"objective_magnification": 40}', encoding="utf-8"
    )
    warns = warnings_from_microscopy_meta(
        ["Acquired at 63× magnification."], [png]
    )
    assert warns
    assert warns[0].metadata["grounding"] == "microscopy_meta"


def test_multiplicity_pair_enumeration():
    pairs = enumerate_pairwise_groups({"ctrl", "mutA", "mutB"})
    assert len(pairs) == 3
    claimed = extract_claimed_pairs(["ctrl vs mutA and ctrl vs mutB"])
    assert len(claimed) >= 2
    vecs = [
        GroupVector(source=Path("/t.csv"), group_key=g, values=(1.0, 2.0), n=2)
        for g in ("ctrl", "mutA", "mutB")
    ]
    warns = warnings_from_multiplicity_gap(
        ["Student's t-tests for ctrl vs mutA and ctrl vs mutB."], vecs
    )
    assert warns
    assert len(warns[0].metadata["pairs"]) == 3
    assert warns[0].metadata["claimed_pairs"]


def test_partial_rotated_containment(tmp_path: Path):
    rng = np.random.default_rng(7)
    large = (rng.random((160, 160)) * 200 + 20).astype(np.uint8)
    patch = large[20:90, 30:100].copy()  # ~19% area
    rotated = np.asarray(Image.fromarray(patch).rotate(90, expand=True))
    full = tmp_path / "full.png"
    crop = tmp_path / "crop_rot.png"
    Image.fromarray(large).save(full)
    Image.fromarray(rotated).save(crop)
    m = partial_containment_score(crop, full, confirm_precise=False)
    assert m is not None
    assert m.score >= 0.92
    assert "rot" in m.method


def test_partial_continuous_scale_off_grid(tmp_path: Path):
    """Embedded shrink of query peaks at scale 0.80 (between old discrete 0.85 and 0.7)."""
    rng = np.random.default_rng(11)
    large = (rng.random((220, 220)) * 30 + 10).astype(np.uint8)
    query = (rng.random((100, 100)) * 180 + 40).astype(np.uint8)
    query[15:40, 15:40] = 250
    query[55:85, 50:90] = 5
    query[10:20, 70:95] = 200
    target = 0.80
    embedded = np.asarray(
        Image.fromarray(query).resize(
            (int(100 * target), int(100 * target)), Image.Resampling.BILINEAR
        )
    )
    assert embedded.shape == (80, 80)
    large[50:130, 60:140] = embedded

    full = tmp_path / "full.png"
    crop = tmp_path / "crop_up.png"
    Image.fromarray(large).save(full)
    Image.fromarray(query).save(crop)

    grid = _scale_grid(scale_min=0.55, scale_max=1.0, scale_step=0.05)
    assert 0.80 in grid
    assert len(grid) > 3

    m = partial_containment_score(
        crop,
        full,
        confirm_precise=False,
        try_rotations=False,
        scale_step=0.05,
    )
    assert m is not None
    assert m.score >= 0.95
    assert m.best_scale is not None
    assert abs(m.best_scale - 0.80) < 0.06

    # Discrete-only trio should be weaker than continuous peak
    m_disc = partial_containment_score(
        crop,
        full,
        confirm_precise=False,
        try_rotations=False,
        scale_min=0.7,
        scale_max=1.0,
        scale_step=0.15,  # → ~{1.0, 0.85, 0.7}
    )
    assert m_disc is not None
    assert m.score >= m_disc.score - 1e-6


def test_exclusion_id_trace_unit():
    path = SYN / "exclusion_id_trace" / "fig1a" / "animals.csv"
    warns = warnings_from_exclusion_id_trace(
        ["Animals were excluded from analysis."],
        [path],
        exclusion_mentioned=True,
    )
    assert warns
    assert warns[0].metadata["check"] == "exclusion_id_trace"
    assert "F5" in warns[0].metadata["undeclared_ids"]
    assert warns[0].metadata["set_equality"] is False


def test_exclusion_id_set_equality_phantom():
    declared = extract_declared_exclusion_ids(
        ["Excluded animals: F5, F7 based on criteria."]
    )
    assert declared == {"F5", "F7"}
    diff = compare_exclusion_id_sets(declared, {"F5", "F6"})
    assert diff["undeclared"] == ["F6"]
    assert diff["phantom"] == ["F7"]
    assert diff["matched"] == ["F5"]

    path = SYN / "exclusion_id_phantom" / "fig1a" / "animals.csv"
    warns = warnings_from_exclusion_id_trace(
        ["Animals were excluded from analysis: F5, F7."],
        [path],
        exclusion_mentioned=True,
    )
    assert warns
    meta = warns[0].metadata
    assert "F6" in meta["undeclared_ids"]
    assert "F7" in meta["phantom_ids"]
    assert meta["set_equality"] is False


def test_lightglue_require_and_default_threshold(tmp_path: Path):
    rng = np.random.default_rng(3)
    a = (rng.random((128, 128)) * 200 + 20).astype(np.uint8)
    a[20:60, 20:60] = 240
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    Image.fromarray(a).save(p1)
    Image.fromarray(a).save(p2)
    missing = verify_image_pair(
        p1, p2, prefer_lightglue=True, require_lightglue=True
    )
    # Without LightGlue installed → explicit missing; with it → verified or count
    if missing.method == "lightglue-required-missing":
        assert missing.verified is False
    else:
        assert missing.method in {
            "lightglue+superpoint",
            "lightglue+aliked",
            "lightglue-required-failed",
        }
    assert MIN_MATCHES_BY_FEATURES == {"superpoint": 35, "aliked": 50}


def test_survival_curve_recalc_audit():
    path = SYN / "survival_noninteger" / "survival.csv"
    issues = audit_survival_curve(path, 20)
    assert issues
    assert any(i.get("noninteger") or i.get("curve_mismatch") for i in issues)
