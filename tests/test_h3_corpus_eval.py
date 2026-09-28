"""H3 corpus fit-rate — synthetic baseline (no real PDF ingest)."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.eval.h3_corpus_eval import evaluate_synthetic_baseline
from pre_peer_checker.imaging.corpus_scan import scan_against_corpus


def test_h3_synthetic_baseline(tmp_path: Path):
    report = evaluate_synthetic_baseline(out_dir=tmp_path / "h3")
    assert report["image_reuse"]["required_recall"] == 1.0
    assert report["image_partial"]["required_recall"] == 1.0
    assert report["citation_suppression"]["ok"] is True


def test_corpus_scan_logs_cross_match_pairs(tmp_path: Path):
    from PIL import Image
    import numpy as np

    qdir = tmp_path / "q"
    cdir = tmp_path / "c"
    qdir.mkdir()
    cdir.mkdir()
    arr = (np.random.default_rng(0).random((64, 64)) * 200 + 20).astype("uint8")
    Image.fromarray(arr).save(qdir / "a.png")
    Image.fromarray(arr).save(cdir / "b.png")
    result = scan_against_corpus(
        [qdir / "a.png"], [cdir], legend_has_citation=False, prefer_dino=False
    )
    assert result.corpus_present
    pairs = result.artifacts.get("cross_match_pairs") or []
    assert pairs
    assert pairs[0]["kind"] in {"full", "partial"}
