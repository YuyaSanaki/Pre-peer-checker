"""Measurement foundation: clean-corpus FP gate, metric history, pattern dashboard, layers."""

from __future__ import annotations

import json
from pathlib import Path

from pre_peer_checker.eval.clean_corpus import (
    CleanCase,
    compare_to_baseline,
    discover_cases,
    pattern_counts,
    summarize,
    unexplained,
)
from pre_peer_checker.eval.history import append_history, read_history
from pre_peer_checker.eval.layers import diagnose_item_layer, pattern_spec_ids
from pre_peer_checker.eval.metrics_suite import all_gold_cases, pattern_dashboard

ROOT = Path(__file__).resolve().parents[1]


def _w(pid: str, demoted: bool = False) -> dict:
    return {"metadata": {"pattern_id": pid}, "demoted": demoted}


def test_pattern_counts_split_active_and_demoted():
    counts = pattern_counts([_w("P-A"), _w("P-A"), _w("P-A", demoted=True), _w("P-B")])
    assert counts == {"P-A": {"active": 2, "demoted": 1}, "P-B": {"active": 1, "demoted": 0}}


def test_expected_warnings_are_not_false_positives():
    counts = {"P-A": {"active": 3, "demoted": 0}, "P-B": {"active": 1, "demoted": 2}}
    assert unexplained(counts, {"P-A": {"count": 2, "reason": "legitimate"}}) == {"P-A": 1, "P-B": 1}


def test_summary_and_baseline_regression():
    reps = [
        {"case_id": "p1", "counts": {"P-A": {"active": 2, "demoted": 1}}},
        {"case_id": "p2", "counts": {}},
    ]
    s = summarize(reps, expected={})
    assert s["fp_per_paper"] == 1.0 and s["papers_with_fp"] == 1
    base = {"summary": {"unexplained_by_case": {"p1": {"P-A": 1}, "p2": {}}}}
    cmp = compare_to_baseline(s, base)
    assert not cmp["ok"]
    assert cmp["regressions"] == [{"case_id": "p1", "pattern_id": "P-A", "baseline": 1, "now": 2}]
    better = summarize([{"case_id": "p1", "counts": {}}], expected={})
    assert compare_to_baseline(better, base)["improvements"][0]["now"] == 0


def test_discover_cases_skips_review_pngs(tmp_path: Path):
    paper = tmp_path / "paper_01"
    (paper / "figures").mkdir(parents=True)
    (paper / "review").mkdir()
    (paper / "article.pdf").write_bytes(b"%PDF-1.4")
    (paper / "review" / "Fig1.png").write_bytes(b"")
    (tmp_path / "_template").mkdir()
    (tmp_path / "empty").mkdir()
    cases = discover_cases(tmp_path)
    assert [c.case_id for c in cases] == ["paper_01"]
    assert cases[0].inputs == [paper / "article.pdf", paper / "figures"]
    assert isinstance(cases[0], CleanCase)


def test_history_append_and_read(tmp_path: Path):
    path = tmp_path / "h.jsonl"
    append_history("x", {"recall": 0.5}, path=path)
    append_history("y", {"recall": 0.7}, path=path)
    rows = read_history("x", path=path)
    assert len(rows) == 1 and rows[0]["metrics"] == {"recall": 0.5}
    assert "commit" in rows[0] and "dirty" in rows[0]


def test_layers_spec_covers_whole_catalog():
    doc = json.loads((ROOT / "fixtures/patterns/pubpeer_patterns.json").read_text(encoding="utf-8"))
    missing = {p["id"] for p in doc["patterns"]} - pattern_spec_ids()
    assert not missing, sorted(missing)


def test_layers_missing_legend_is_parser_miss():
    coverage = {
        "checks": [
            {"id": "word_legend", "status": "ran", "detail": "対象 Word 1 件 · 抽出パネル n=4"},
            {"id": "legend_found", "status": "ran", "detail": "3 Figure のうち 2 件で Legend を検出。見つからない: Fig. 5"},
            {"id": "tables", "status": "ran", "detail": ""},
        ]
    }
    diag = diagnose_item_layer(
        {"pattern_id": "P-SOURCE-DATA-LEGEND-N"}, matched=False, coverage=coverage, artifacts={}
    )
    assert diag["fail_layer"] == "parser"


def test_pattern_dashboard_counts_tp_fp_and_clean(tmp_path: Path):
    warnings = [
        {"tag": "Warning [サンプルサイズ記載誤記]", "title": "Fig. 5c", "location": "MOESM44_ESM.xlsx",
         "reason": "", "sources": ["MOESM44_ESM.xlsx"], "metadata": {"pattern_id": "P-SOURCE-DATA-LEGEND-N"}},
        {"tag": "Warning [データ取り違え]", "title": "x", "location": "y", "reason": "", "sources": [],
         "metadata": {"pattern_id": "P-DATA-SWAP-CROSS-CONDITION"}},
    ]
    (tmp_path / "source_data_warnings.json").write_text(json.dumps({"warnings": warnings}), encoding="utf-8")
    rep = {
        "case_id": "source_data",
        "status": "ok",
        "items": [{"id": "SD5", "severity": "required", "matched": True}],
    }
    dash = pattern_dashboard(
        [rep],
        out_dir=tmp_path,
        clean_summary={"summary": {"unexplained_by_pattern": {"P-DATA-SWAP-CROSS-CONDITION": 3}, "n_cases": 21}},
    )
    sd = dash["patterns"]["P-SOURCE-DATA-LEGEND-N"]
    assert sd["recall"] == 1.0 and sd["precision"] == 1.0
    swap = dash["patterns"]["P-DATA-SWAP-CROSS-CONDITION"]
    assert swap["fp_warnings"] == 1 and swap["clean_fp"] == 3
    assert "source_data" in all_gold_cases()
