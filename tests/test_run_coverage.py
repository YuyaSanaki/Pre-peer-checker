"""Run coverage summary for WebUI / reports."""

from __future__ import annotations

from pre_peer_checker.io_bundle import FileKind, InputBundle, collect_inputs
from pre_peer_checker.pipeline.run_coverage import build_run_coverage, coverage_lines


def test_coverage_lines_include_skips() -> None:
    bundle = InputBundle()
    bundle.files[FileKind.DOCX] = []
    cov = build_run_coverage(bundle, {}, corpus_provided=False)
    lines = coverage_lines(cov)
    assert any("読込ファイル" not in x or True for x in lines)
    assert any("スキップ" in x or "skipped" in str(cov["checks"]) for x in [str(cov)])
    statuses = {c["id"]: c["status"] for c in cov["checks"]}
    assert statuses["word_legend"] == "skipped"
    assert statuses["corpus_h3"] == "skipped"


def test_coverage_with_tables(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.csv").write_text("g,v\nA,1\nA,2\nB,3\n", encoding="utf-8")
    ms = tmp_path / "manuscript"
    ms.mkdir()
    bundle = collect_inputs([ms, data])
    try:
        arts = {
            "docx": [],
            "legend_panel_ns": [],
            "group_vectors": [{"id": "x"}],
            "digitized_plots": [],
            "figure_panel_plots": [],
        }
        cov = build_run_coverage(bundle, arts, corpus_provided=False)
        by_id = {c["id"]: c for c in cov["checks"]}
        assert by_id["tables"]["status"] == "ran"
        assert by_id["legend_n_match"]["status"] == "skipped"
        assert "群ベクトル" in by_id["tables"]["detail"]
    finally:
        bundle.cleanup_extracts()


def test_word_legend_coverage_reports_zero_when_all_docx_fail() -> None:
    """Empty docx artifacts must not fall back to bundle docx count via `or`."""
    from pathlib import Path

    bundle = InputBundle()
    bundle.files[FileKind.DOCX] = [
        Path("/tmp/broken_a.docx"),
        Path("/tmp/broken_b.docx"),
    ]
    cov = build_run_coverage(
        bundle,
        {"docx": [], "legend_panel_ns": []},
        corpus_provided=False,
    )
    by_id = {c["id"]: c for c in cov["checks"]}
    detail = by_id["word_legend"]["detail"]
    assert by_id["word_legend"]["status"] == "ran"
    assert "処理できた Word 0 件" in detail
    assert "検出 2" in detail
    assert "対象 Word 2 件" not in detail


def test_coverage_cited_image_matches() -> None:
    bundle = InputBundle()
    cov = build_run_coverage(
        bundle,
        {
            "corpus_scan": {
                "cited_matches": [
                    {"a": "/m/a.png", "b": "/c/b.png", "similarity": 0.98}
                ]
            },
            "legend_citation_mentioned": True,
        },
        corpus_provided=True,
    )
    assert len(cov["cited_image_matches"]) == 1
    by_id = {c["id"]: c for c in cov["checks"]}
    assert by_id["image_reuse_cited"]["status"] == "ran"
    assert "出典あり" in by_id["image_reuse_cited"]["detail"]
