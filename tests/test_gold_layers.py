"""Unit tests for gold_eval layer diagnosis."""

from __future__ import annotations

import json
from pathlib import Path

from pre_peer_checker.eval.gold_eval import evaluate
from pre_peer_checker.eval.layers import (
    LAYER_LINKING,
    LAYER_MATCH,
    LAYER_PARSER,
    LAYER_SKIPPED,
    diagnose_item_layer,
    summarize_layers,
)


def test_diagnose_match_credits_primary():
    item = {"id": "D1", "pattern_id": "P-DATA-SWAP-CROSS-CONDITION"}
    d = diagnose_item_layer(item, matched=True)
    assert d["layer"] == LAYER_MATCH
    assert d["fail_layer"] is None


def test_diagnose_parser_when_inputs_skipped():
    item = {"id": "D2", "pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA"}
    coverage = {
        "checks": [
            {"id": "word_legend", "status": "skipped", "detail": "docx なし"},
            {"id": "tables", "status": "skipped", "detail": "csv/xlsx なし"},
            {"id": "legend_n_match", "status": "skipped", "detail": "Legend からパネル n を抽出できず"},
        ]
    }
    d = diagnose_item_layer(item, matched=False, coverage=coverage)
    assert d["fail_layer"] == LAYER_PARSER


def test_diagnose_linking_when_legend_n_match_skipped():
    item = {"id": "D2", "pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA"}
    coverage = {
        "checks": [
            {
                "id": "word_legend",
                "status": "ran",
                "detail": "対象 Word 1 件 · 抽出パネル n=3",
            },
            {"id": "tables", "status": "ran", "detail": "表ファイル 2 件 · 群ベクトル 2 件"},
            {
                "id": "legend_n_match",
                "status": "skipped",
                "detail": "比較可能な群ベクトルなし",
            },
        ]
    }
    d = diagnose_item_layer(item, matched=False, coverage=coverage)
    assert d["fail_layer"] == LAYER_LINKING


def test_diagnose_match_when_prereqs_ran():
    item = {"id": "D1", "pattern_id": "P-DATA-SWAP-CROSS-CONDITION"}
    coverage = {
        "checks": [
            {"id": "tables", "status": "ran", "detail": "ok"},
            {"id": "cross_table", "status": "ran", "detail": "群ベクトル 4 件"},
            {"id": "plot_digitize", "status": "ran", "detail": "digitized=2"},
        ]
    }
    d = diagnose_item_layer(item, matched=False, coverage=coverage)
    assert d["fail_layer"] == LAYER_MATCH


def test_diagnose_skipped_corpus():
    item = {"id": "D3", "pattern_id": "P-IMAGE-REUSE-UNCITED"}
    d = diagnose_item_layer(item, matched=False, skipped=True)
    assert d["layer"] == LAYER_SKIPPED


def test_summarize_recommends_linking():
    rows = [
        {
            "id": "A",
            "severity": "required",
            "matched": False,
            "layer": LAYER_LINKING,
            "fail_layer": LAYER_LINKING,
        },
        {
            "id": "B",
            "severity": "required",
            "matched": False,
            "layer": LAYER_LINKING,
            "fail_layer": LAYER_LINKING,
        },
        {
            "id": "C",
            "severity": "required",
            "matched": True,
            "layer": LAYER_MATCH,
            "fail_layer": None,
        },
    ]
    s = summarize_layers(rows)
    assert s["recommended_focus"] == LAYER_LINKING
    assert s["recommended_focus_required_misses"] == 2


def test_evaluate_includes_layers_from_envelope(tmp_path: Path):
    warnings_payload = {
        "warnings": [
            {
                "tag": "Warning [サンプルサイズ記載誤記]",
                "title": "Fig.1 A1 n mismatch",
                "location": "Fig.1 group A1",
                "reason": "legend n=6 data n=5",
                "sources": ["quant_a.csv"],
                "metadata": {"pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA", "panel": "A1"},
            }
        ],
        "run_coverage": {
            "checks": [
                {
                    "id": "word_legend",
                    "status": "ran",
                    "detail": "対象 Word 1 件 · 抽出パネル n=2",
                },
                {"id": "tables", "status": "ran", "detail": "表 2"},
                {"id": "legend_n_match", "status": "ran", "detail": "照合"},
                {"id": "cross_table", "status": "ran", "detail": "ok"},
                {"id": "plot_digitize", "status": "skipped", "detail": "なし"},
                {"id": "figure_pdf", "status": "skipped", "detail": "なし"},
                {"id": "scripts", "status": "skipped", "detail": "なし"},
            ]
        },
        "artifacts": {"n_matrix": []},
    }
    path = tmp_path / "w.json"
    path.write_text(json.dumps(warnings_payload), encoding="utf-8")
    report = evaluate("demo", path, corpus_present=False)
    assert "layers" in report
    assert report["layer_diagnosis_available"] is True
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["D2"]["matched"] is True
    assert by_id["D2"]["layer"] == LAYER_MATCH
    assert by_id["D1"]["matched"] is False
    # D1 miss with tables/cross_table ran → match layer
    assert by_id["D1"]["fail_layer"] == LAYER_MATCH
    assert by_id["D3"]["layer"] == LAYER_SKIPPED
