"""Script DAG swap fixture: ggplot AlphaExp DF + ggsave RplotBetaExp name."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.parsers.r_treesitter import build_r_dag
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.warnings import WarningTag

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "fixtures" / "synthetic" / "script_swap"


def test_script_swap_r_dag_single_plot():
    dag = build_r_dag((CASE / "plot_fig.R").read_text(encoding="utf-8"))
    assert len(dag.plots) == 1
    assert dag.plots[0].data_expr == "df_alphaexp"
    assert dag.saves and dag.saves[0].path == "RplotBetaExp.pdf"


def test_script_swap_emits_filename_mismatch():
    result = run_verification([CASE])
    assert any(
        w.tag == WarningTag.DATA_SWAP
        and w.metadata.get("pattern_id") == "P-FILENAME-CONTENT-MISMATCH"
        for w in result.warnings
    ), [w.to_dict() for w in result.warnings]
    # Same-line ggplot must not fake "multiple ggplot" noise
    assert not any("複数 ggplot" in w.title for w in result.warnings)


def test_script_swap_gold_eval(tmp_path: Path):
    report = run_and_evaluate(
        "script_swap",
        input_paths=[CASE],
        warnings_out=tmp_path / "script_warnings.json",
    )
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["S1"]["matched"] is True
    assert report["required_recall"] == 1.0
