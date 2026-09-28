"""LIF UTF-16 XML acquisition meta + confocal grounding."""

from __future__ import annotations

from pathlib import Path


from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.engine.microscopy_meta import warnings_from_microscopy_meta
from pre_peer_checker.imaging.lif_meta import (
    parse_lif_acquisition_meta,
    write_minimal_lif_fixture,
)
from pre_peer_checker.pipeline.orchestrator import run_verification

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "fixtures" / "synthetic" / "lif_meta"


def test_parse_minimal_lif_fixture(tmp_path: Path):
    lif = write_minimal_lif_fixture(tmp_path / "a.lif", magnification=20)
    meta = parse_lif_acquisition_meta(lif)
    assert meta is not None
    assert meta.magnification == 20.0
    assert meta.pixel_size_um is not None
    assert meta.field_of_view_um is not None
    assert "20" in (meta.objective_name or "") or meta.magnification == 20


def test_lif_meta_orchestrator_and_gold(tmp_path: Path):
    result = run_verification([SYN])
    hits = [
        w
        for w in result.warnings
        if w.metadata.get("pattern_id") == "P-SCALE-MAG-INCONSISTENT"
    ]
    assert hits
    assert any(
        w.metadata.get("grounding") in {"lif_xml", "lif_fov_vs_scale_bar"}
        for w in hits
    )
    arts = result.artifacts.get("lif_acquisition_meta") or []
    assert arts and arts[0].get("magnification") == 40.0

    report = run_and_evaluate(
        "lif_meta",
        input_paths=[SYN],
        warnings_out=tmp_path / "lif_meta.json",
    )
    assert report["required_recall"] == 1.0, report


def test_lif_scale_bar_exceeds_fov(tmp_path: Path):
    lif = write_minimal_lif_fixture(
        tmp_path / "tiny.lif",
        magnification=20,
        field_of_view_um=100.0,
        n_pixels=256,
    )
    warns = warnings_from_microscopy_meta(
        ["Scale bars, 500 µm."],
        [lif],
    )
    assert any(w.metadata.get("grounding") == "lif_fov_vs_scale_bar" for w in warns)
