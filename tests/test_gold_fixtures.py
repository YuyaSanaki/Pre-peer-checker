"""Gold fixture loader / eval tests (synthetic demo; optional private benchmark)."""

from __future__ import annotations

import json
from pathlib import Path


from pre_peer_checker.eval.gold_eval import (
    evaluate,
    load_gold,
    load_manifest,
    run_and_evaluate,
    warning_supports_item,
)

ROOT = Path(__file__).resolve().parents[1]
HYPER_GOLD = ROOT / "fixtures" / "gold" / "private_benchmark" / "gold_warnings.json"


def test_demo_manifest_and_gold_load():
    m = load_manifest("demo")
    g = load_gold("demo")
    assert m["case_id"] == "demo"
    assert g["case_id"] == "demo"
    ids = {i["id"] for i in g["items"]}
    assert {"D1", "D2", "D3"} <= ids


def test_patterns_cover_demo_and_example_pattern_ids():
    patterns = json.loads(
        (ROOT / "fixtures/patterns/pubpeer_patterns.json").read_text(encoding="utf-8")
    )
    known = {p["id"] for p in patterns["patterns"]}
    for case_file in [
        ROOT / "fixtures/gold/demo/gold_warnings.json",
        ROOT / "fixtures/gold/shared_control/gold_warnings.json",
        ROOT / "fixtures/gold/image_reuse/gold_warnings.json",
        ROOT / "fixtures/gold/cross_fig_reuse/gold_warnings.json",
        ROOT / "fixtures/gold/script_swap/gold_warnings.json",
        ROOT / "fixtures/gold/source_dup/gold_warnings.json",
        ROOT / "fixtures/gold/source_ratio/gold_warnings.json",
        ROOT / "fixtures/gold/stats_recalc/gold_warnings.json",
        ROOT / "fixtures/gold/exclusion_undeclared/gold_warnings.json",
        ROOT / "fixtures/gold/image_partial/gold_warnings.json",
        ROOT / "fixtures/gold/blot_lane/gold_warnings.json",
        ROOT / "fixtures/gold/vector_subset/gold_warnings.json",
        ROOT / "fixtures/gold/numeric_crossref/gold_warnings.json",
        ROOT / "fixtures/gold/methods_claim/gold_warnings.json",
        ROOT / "fixtures/gold/ref_biblio/gold_warnings.json",
        ROOT / "fixtures/gold/ref_claim/gold_warnings.json",
        ROOT / "fixtures/gold/errorbar_sem_sd/gold_warnings.json",
        ROOT / "fixtures/gold/multiplicity/gold_warnings.json",
        ROOT / "fixtures/gold/survival_noninteger/gold_warnings.json",
        ROOT / "fixtures/gold/scale_mag/gold_warnings.json",
        ROOT / "fixtures/gold/scale_bar_legend/gold_warnings.json",
        ROOT / "fixtures/gold/antibody_host/gold_warnings.json",
        ROOT / "fixtures/gold/count_n/gold_warnings.json",
        ROOT / "fixtures/gold/private_benchmark/gold_warnings.example.json",
    ]:
        gold = json.loads(case_file.read_text(encoding="utf-8"))
        for item in gold["items"]:
            assert item["pattern_id"] in known, item["pattern_id"]


def test_gold_eval_matches_synthetic_warning(tmp_path: Path):
    warnings = [
        {
            "tag": "Warning [サンプルサイズ記載誤記]",
            "title": "Fig.1 A1 n mismatch",
            "location": "Fig.1 group A1",
            "reason": "legend n=6 data n=5",
            "sources": ["quant_a.csv"],
            "metadata": {"pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA", "panel": "A1"},
        }
    ]
    path = tmp_path / "w.json"
    path.write_text(json.dumps(warnings), encoding="utf-8")
    report = evaluate("demo", path, corpus_present=False)
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["D2"]["matched"] is True
    assert by_id["D3"]["matched"] is True  # skipped OK without corpus
    assert by_id["D1"]["matched"] is False


def test_single_letter_panel_token_does_not_false_match():
    """Regression: bare 'g'/'o' in Japanese text must not satisfy panel G/O."""
    item = {
        "id": "H1",
        "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
        "expected_tags": ["Warning [データ取り違え]"],
        "panels": [
            {"figure": "Fig.1C", "group": "G"},
            {"figure": "Fig.1H", "group": "O"},
        ],
        "evidence_paths": [
            "Manuscript/data/Fig1/plotDump/RplotBetaExp.pdf",
            "Manuscript/data/Fig1/plotDump/RplotAlphaExp.pdf",
        ],
    }
    weak = {
        "tag": "Warning [データ取り違え]",
        "title": "作図残渣フォルダに複数実験系ラベルが混在",
        "location": "/tmp/plotdump",
        "reason": "確認してください",
        "sources": [],
        "metadata": {"pattern_id": "P-FILENAME-CONTENT-MISMATCH"},
    }
    assert warning_supports_item(item, weak) is False
    strong = {
        "tag": "Warning [データ取り違え]",
        "title": "統計残渣と作図残渣が別データセットに一致",
        "location": "BetaExpWelch ↔ graphBetaExpS / RplotBetaExp ↔ graphAlphaExp",
        "reason": "ggplot data swap",
        "sources": ["RplotBetaExp.pdf", "graphAlphaExp.xlsx"],
        "metadata": {"pattern_id": "P-DATA-SWAP-CROSS-CONDITION"},
    }
    assert warning_supports_item(item, strong) is True


def test_private_private_benchmark_gold_not_required_in_ci():
    """Filled private gold may be absent in clones; examples must exist."""
    example = ROOT / "fixtures/gold/private_benchmark/gold_warnings.example.json"
    assert example.is_file()
    filled = HYPER_GOLD
    assert filled.is_file() or not filled.exists()


def test_synthetic_demo_csvs_exist():
    demo = ROOT / "fixtures" / "synthetic" / "demo"
    assert (demo / "quant_a.csv").is_file()
    assert (demo / "quant_b.csv").is_file()
    assert (demo / "legend_n.json").is_file()


def test_demo_pipeline_gold_eval_d1(tmp_path: Path):
    """CI-safe: synthetic demo must hit D1 (cross-condition identical vectors)."""
    demo = ROOT / "fixtures" / "synthetic" / "demo"
    report = run_and_evaluate(
        "demo",
        input_paths=[demo],
        warnings_out=tmp_path / "demo_warnings.json",
    )
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["D1"]["matched"] is True
    assert by_id["D3"]["matched"] is True  # skipped without corpus
    assert report.get("recall") == 1.0
    assert "precision" in report


def test_metrics_suite_synthetic(tmp_path: Path):
    from pre_peer_checker.eval.metrics_suite import main as metrics_main

    out = tmp_path / "suite.json"
    example = tmp_path / "example.json"
    rc = metrics_main(
        [
            "--case",
            "demo",
            "--case",
            "shared_control",
            "-o",
            str(out),
            "--example-out",
            str(example),
        ]
    )
    assert rc == 0
    suite = json.loads(out.read_text(encoding="utf-8"))
    assert suite["summary"]["n_cases_ok"] == 2
    assert suite["lora_stance"]
    assert example.is_file()
