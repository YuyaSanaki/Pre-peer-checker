"""CZI UTF-8 XML acquisition meta grounding."""

from __future__ import annotations

from pathlib import Path

import pytest

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.engine.microscopy_meta import warnings_from_microscopy_meta
from pre_peer_checker.imaging.czi_meta import (
    collect_czi_acquisition_meta,
    parse_czi_acquisition_meta,
    write_minimal_czi_fixture,
)
from pre_peer_checker.pipeline.orchestrator import run_verification

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "fixtures" / "synthetic" / "czi_meta"
CZI_DIR = ROOT / "input" / "data" / "czi"


def test_parse_minimal_czi_fixture(tmp_path: Path):
    czi = write_minimal_czi_fixture(tmp_path / "a.czi", magnification=20)
    meta = parse_czi_acquisition_meta(czi)
    assert meta is not None
    assert meta.magnification == 20.0
    assert meta.pixel_size_um is not None
    assert meta.field_of_view_um is not None
    assert "20" in (meta.objective_name or "") or meta.magnification == 20


def test_czi_meta_orchestrator_and_gold(tmp_path: Path):
    result = run_verification([SYN])
    hits = [
        w
        for w in result.warnings
        if w.metadata.get("pattern_id") == "P-SCALE-MAG-INCONSISTENT"
    ]
    assert hits
    assert any(
        w.metadata.get("grounding") in {"czi_xml", "czi_fov_vs_scale_bar"}
        for w in hits
    )
    arts = result.artifacts.get("czi_acquisition_meta") or []
    assert arts and arts[0].get("magnification") == 40.0

    report = run_and_evaluate(
        "czi_meta",
        input_paths=[SYN],
        warnings_out=tmp_path / "czi_meta.json",
    )
    assert report["required_recall"] == 1.0, report


def test_czi_scale_bar_exceeds_fov(tmp_path: Path):
    czi = write_minimal_czi_fixture(
        tmp_path / "tiny.czi",
        magnification=10,
        n_pixels=256,
        pixel_size_um=0.2,  # FOV ≈ 51.2 µm
    )
    warns = warnings_from_microscopy_meta(
        ["Scale bars, 500 µm."],
        [czi],
    )
    assert any(w.metadata.get("grounding") == "czi_fov_vs_scale_bar" for w in warns)


@pytest.mark.skipif(not CZI_DIR.is_dir(), reason="input/data/czi not mounted")
def test_real_czi_meta_extract():
    czis = sorted(CZI_DIR.rglob("*.czi"))
    assert czis, CZI_DIR
    metas = collect_czi_acquisition_meta(czis, max_files=4)
    assert metas
    one = parse_czi_acquisition_meta(czis[0])
    assert one is not None
    assert one.magnification == 10.0
    assert one.objective_name and "10x" in one.objective_name.replace(" ", "")
    assert one.pixel_size_um and one.pixel_size_um > 0
    assert one.field_of_view_um and one.field_of_view_um > 0


@pytest.mark.skipif(not CZI_DIR.is_dir(), reason="input/data/czi not mounted")
def test_real_czi_grounding_smoke():
    """CZI-only: meta extracted; no mag Warning without contradicting Legend."""
    result = run_verification([CZI_DIR])
    arts = result.artifacts.get("czi_acquisition_meta") or []
    assert arts and arts[0].get("magnification") == 10.0
    mag_warns = [
        w
        for w in result.warnings
        if w.metadata.get("pattern_id") == "P-SCALE-MAG-INCONSISTENT"
        and w.metadata.get("grounding") == "czi_xml"
    ]
    assert mag_warns == []
