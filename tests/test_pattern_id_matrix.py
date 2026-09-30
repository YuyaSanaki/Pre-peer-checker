"""pattern_id 1:1 合成 CI（Phase 5 残 / Phase 6D）。

カタログ全 pattern_id が matrix に載り、ci_required 行は harness で再現する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.pipeline.orchestrator import run_verification

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "fixtures" / "patterns" / "pattern_synthetic_matrix.json"
CATALOG_PATH = ROOT / "fixtures" / "patterns" / "pubpeer_patterns.json"


def _load_matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _catalog_ids() -> set[str]:
    doc = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {p["id"] for p in doc["patterns"]}


def test_matrix_covers_entire_catalog():
    matrix = _load_matrix()
    catalog = _catalog_ids()
    matrix_ids = {e["pattern_id"] for e in matrix["entries"]}
    missing = catalog - matrix_ids
    extra = matrix_ids - catalog
    assert not missing, f"matrix missing catalog ids: {sorted(missing)}"
    assert not extra, f"matrix has unknown ids: {sorted(extra)}"


def test_ci_required_entries_have_harness():
    for e in _load_matrix()["entries"]:
        if not e.get("ci_required"):
            assert e.get("harness") in {"deferred", "planned"}, e
            assert e.get("defer_reason") or e.get("fixture_policy"), e
            continue
        assert e.get("harness") in {"gold_eval", "unit"}, e
        if e["harness"] == "gold_eval":
            assert e.get("case_id"), e
        if e["harness"] == "unit":
            assert e.get("unit"), e


def _run_h2b_unit() -> None:
    from pre_peer_checker.engine.n_and_names import warnings_inconsistent_n_identical_plots
    from pre_peer_checker.parsers.legend_struct import PanelN
    from pre_peer_checker.parsers.pdf_plot_digitize import DigitizedGroup, DigitizedPlot

    shared = tuple(float(i) for i in range(1, 12))
    plots = [
        DigitizedPlot(
            Path("/x/RplotAlphaExp.pdf"),
            [DigitizedGroup("1", shared)],
        ),
        DigitizedPlot(
            Path("/x/RplotBetaExp.pdf"),
            [DigitizedGroup("1", shared)],
        ),
    ]
    panel_ns = [
        PanelN(panel="C", n=9, figure="Figure 1", context="n=9 (C)"),
        PanelN(panel="G", n=14, figure="Figure 1", context="n=14 (G)"),
    ]
    warns = warnings_inconsistent_n_identical_plots(panel_ns, [], plots)
    assert any(
        w.metadata.get("pattern_id") == "P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS"
        for w in warns
    )


def _run_source_data_unit(pid: str, tmp_path: Path) -> None:
    from test_source_data_blocks import _blocks

    from pre_peer_checker.engine.source_data_checks import (
        warnings_from_source_data_panels,
        warnings_from_source_data_reuse,
        warnings_from_source_data_summaries,
    )
    from pre_peer_checker.parsers.legend_struct import PanelN

    blocks, _, _ = _blocks(tmp_path)
    panel_ns = [
        PanelN(panel="P", n=30, figure="Figure 4", context="n (n = 30)", group="n"),
        PanelN(panel="H", n=9, figure="Figure 4", context="d (n = 9)", group="d"),
        PanelN(panel="H", n=7, figure="Figure 4", context="g (n = 7)", group="g"),
    ]
    warns = (
        warnings_from_source_data_reuse(blocks)
        + warnings_from_source_data_summaries(blocks)
        + warnings_from_source_data_panels(blocks, panel_ns)
    )
    assert pid in {w.metadata.get("pattern_id") for w in warns}


def _gold_input(case_id: str, *, corpus: bool) -> dict:
    syn = ROOT / "fixtures" / "synthetic" / case_id
    if case_id in {"image_reuse", "image_partial"}:
        return {
            "input_paths": [syn / "manuscript"],
            "corpus_paths": [syn / "corpus"] if corpus else None,
            "corpus_present": corpus,
        }
    return {"input_paths": [syn], "corpus_present": False}


@pytest.mark.parametrize(
    "entry",
    [e for e in _load_matrix()["entries"] if e.get("ci_required")],
    ids=lambda e: e["pattern_id"],
)
def test_ci_required_pattern_emits(entry: dict, tmp_path: Path, request: pytest.FixtureRequest):
    pid = entry["pattern_id"]
    if entry["harness"] == "unit":
        if entry["unit"] == "source_data_synthetic":
            _run_source_data_unit(pid, tmp_path)
            return
        assert entry["unit"] == "h2b_synthetic"
        request.getfixturevalue("sided_profile")
        _run_h2b_unit()
        return

    case_id = entry["case_id"]
    kwargs = _gold_input(case_id, corpus=bool(entry.get("corpus")))
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    report = run_and_evaluate(
        case_id,
        warnings_out=tmp_path / f"{case_id}_warnings.json",
        **kwargs,
    )
    assert report.get("required_recall") == 1.0, report
    by_id = {i["id"]: i for i in report["items"]}
    gold_item = entry.get("gold_item_id")
    if gold_item:
        assert by_id[gold_item]["matched"] is True, by_id[gold_item]
    # Also confirm emitted pattern_id appears in run warnings
    warns = json.loads((tmp_path / f"{case_id}_warnings.json").read_text(encoding="utf-8"))
    if isinstance(warns, dict):
        warns = warns.get("warnings") or []
    emitted = {
        (w.get("metadata") or {}).get("pattern_id")
        for w in warns
        if isinstance(w, dict)
    }
    assert pid in emitted, sorted(x for x in emitted if x)


def test_source_dup_and_ratio_orchestrator_smoke():
    dup = run_verification([ROOT / "fixtures/synthetic/source_dup"])
    assert any(
        w.metadata.get("pattern_id") == "P-SOURCE-DUPLICATE-VALUES" for w in dup.warnings
    )
    ratio = run_verification([ROOT / "fixtures/synthetic/source_ratio"])
    assert any(
        w.metadata.get("pattern_id") == "P-SOURCE-RATIO-ARTIFACT" for w in ratio.warnings
    )


def test_stats_recalc_orchestrator_smoke():
    result = run_verification([ROOT / "fixtures/synthetic/stats_recalc"])
    assert any(
        w.metadata.get("pattern_id") == "P-STATS-RECALC-MISMATCH" for w in result.warnings
    ), [w.title for w in result.warnings]
