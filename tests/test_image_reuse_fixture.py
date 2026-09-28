"""H3 synthetic corpus regression (PubPeer-inspired, anonymous pixels)."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.warnings import WarningTag

ROOT = Path(__file__).resolve().parents[1]
MS = ROOT / "fixtures" / "synthetic" / "image_reuse" / "manuscript"
CORPUS = ROOT / "fixtures" / "synthetic" / "image_reuse" / "corpus"
CITED = ROOT / "fixtures" / "synthetic" / "image_reuse" / "manuscript_cited"


def test_image_reuse_flags_uncited_with_corpus():
    result = run_verification([MS], corpus=[CORPUS])
    assert any(
        w.tag == WarningTag.IMAGE_REUSE
        and w.metadata.get("pattern_id") == "P-IMAGE-REUSE-UNCITED"
        and w.metadata.get("corpus_match")
        for w in result.warnings
    ), [w.to_dict() for w in result.warnings]


def test_image_reuse_suppressed_when_cited():
    result = run_verification([CITED], corpus=[CORPUS])
    assert not any(w.tag == WarningTag.IMAGE_REUSE for w in result.warnings)
    cov = result.artifacts.get("run_coverage") or {}
    cited = cov.get("cited_image_matches") or []
    assert cited, "cited matches should surface in coverage for the report"
    by_id = {c["id"]: c for c in cov.get("checks") or []}
    assert by_id.get("image_reuse_cited", {}).get("status") == "ran"


def test_cited_image_matches_in_html_report():
    from pre_peer_checker.report.html_report import render_html_report

    html = render_html_report(
        [],
        coverage={
            "checks": [],
            "notes": [],
            "cited_image_matches": [
                {"a": "/m/fig.png", "b": "/c/prior.png", "similarity": 0.99}
            ],
        },
    )
    assert "画像再利用（出典あり・情報）" in html
    assert "出典あり一致" in html
    assert "fig.png" in html


def test_image_reuse_gold_eval(tmp_path: Path):
    report = run_and_evaluate(
        "image_reuse",
        input_paths=[MS],
        corpus_paths=[CORPUS],
        warnings_out=tmp_path / "ir_warnings.json",
    )
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["IR1"]["matched"] is True
    assert report["required_recall"] == 1.0
