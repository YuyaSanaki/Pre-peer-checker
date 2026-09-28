"""Imaging loaders, duplicate scan, HTML report filters."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from pre_peer_checker.imaging.duplicate_scan import (
    DinoDuplicateScanner,
    scan_image_duplicates,
    scan_image_duplicates_auto,
)
from pre_peer_checker.imaging.microscopy import (
    export_frames_as_png,
    load_raster_image,
    try_load_frames,
)
from pre_peer_checker.imaging.microscopy_scan import scan_microscopy_duplicates
from pre_peer_checker.report.html_report import render_html_report
from pre_peer_checker.warnings import WarningItem, WarningTag


def test_load_raster_and_export(tmp_path: Path):
    img_path = tmp_path / "a.png"
    Image.fromarray(np.zeros((32, 32, 3), dtype=np.uint8)).save(img_path)
    frames = load_raster_image(img_path)
    assert len(frames) == 1
    out = export_frames_as_png(frames, tmp_path / "out")
    assert out and out[0].exists()


def test_duplicate_scan_detects_identical_copy(tmp_path: Path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    arr = np.random.default_rng(0).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    Image.fromarray(arr).save(a)
    Image.fromarray(arr).save(b)
    matches = scan_image_duplicates([a, b], threshold=0.99)
    assert matches and matches[0].likely_duplicate


def test_scan_auto_fallback_without_dino(tmp_path: Path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    Image.fromarray(np.zeros((40, 40), dtype=np.uint8)).save(a)
    Image.fromarray(np.zeros((40, 40), dtype=np.uint8)).save(b)
    matches, method = scan_image_duplicates_auto([a, b], prefer_dino=False)
    assert method.startswith("gray64+ahash16")
    assert any(m.likely_duplicate for m in matches)
    # available() is boolean regardless of prefer flag
    assert isinstance(DinoDuplicateScanner.available(), bool)


def test_try_load_lif_reports_missing_dep(tmp_path: Path):
    fake = tmp_path / "x.lif"
    fake.write_bytes(b"not-a-lif")
    frames, err = try_load_frames(fake)
    # Either parse error or missing readlif
    assert frames == []
    assert err


def test_microscopy_scan_on_rasters(tmp_path: Path):
    a = tmp_path / "fig_a.jpg"
    b = tmp_path / "fig_b.jpg"
    arr = np.random.default_rng(1).integers(0, 255, (80, 80, 3), dtype=np.uint8)
    Image.fromarray(arr).save(a, quality=95)
    Image.fromarray(arr).save(b, quality=95)
    result = scan_microscopy_duplicates([], [a, b], max_files=10, prefer_dino=False)
    assert result.artifacts.get("loaded_sources", 0) >= 2
    assert any(
        w.metadata.get("pattern_id") == "P-IMAGE-REUSE-UNCITED" for w in result.warnings
    )


def test_html_report_has_tag_filters():
    warnings = [
        WarningItem(
            tag=WarningTag.SAMPLE_SIZE,
            title="n mismatch",
            location="Fig.1 F",
            reason="legend n=12 data n=11",
            sources=["/tmp/quant.csv"],
            metadata={"pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA"},
        ),
        WarningItem(
            tag=WarningTag.DATA_SWAP,
            title="swap",
            location="I↔Q",
            reason="identical points",
            metadata={"pattern_id": "P-DATA-SWAP-CROSS-CONDITION"},
        ),
        WarningItem(
            tag=WarningTag.CONTROL_SHARE,
            title="【降格】弱い共有記載",
            location="Fig.2",
            reason="weak disclosure",
            metadata={
                "pattern_id": "P-SHARED-CONTROL-UNDISCLOSED",
                "demoted": True,
                "severity": "info",
            },
        ),
    ]
    html = render_html_report(warnings, file_summary="demo")
    assert 'data-filter="Warning [サンプルサイズ記載誤記]"' in html
    assert 'data-filter="Warning [データ取り違え]"' in html
    assert 'data-filter="__demoted__"' in html
    assert "降格・情報" in html
    assert "is-demoted" in html
    assert 'id="search"' in html
    assert "P-N-MISMATCH-LEGEND-VS-DATA" in html
    assert "warning-card" in html
    assert 'href="file:///tmp/quant.csv"' in html


def test_n_mismatch_reason_states_raw_nrows_authority():
    from pre_peer_checker.engine.n_and_names import (
        N_AUTHORITY_FOOTER,
        NMismatch,
        warnings_from_n_mismatches,
    )
    from pre_peer_checker.data.group_vectors import GroupVector
    from pre_peer_checker.parsers.legend_struct import PanelN

    pn = PanelN(panel="F", n=12, figure="Figure 1", context="n=12 (F)")
    vec = GroupVector(
        source=Path("/tmp/graph.xlsx"),
        group_key="1",
        values=(1.0,) * 11,
        n=11,
    )
    w = warnings_from_n_mismatches([NMismatch(panel_n=pn, vector=vec, data_n=11)])[0]
    assert N_AUTHORITY_FOOTER in w.reason
    assert w.metadata.get("n_authority") == "raw_data_nrows"
