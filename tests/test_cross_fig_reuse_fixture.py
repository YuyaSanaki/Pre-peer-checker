"""multi-figure multi-figure identical vector regression."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.warnings import WarningTag

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "fixtures" / "synthetic" / "cross_fig_reuse"


def test_cross_fig_reuse_emits_swap_and_shared_control():
    result = run_verification([CASE])
    tags = {w.tag for w in result.warnings}
    assert WarningTag.DATA_SWAP in tags
    assert WarningTag.CONTROL_SHARE in tags
    assert any(
        w.metadata.get("pattern_id") == "P-DATA-SWAP-CROSS-CONDITION"
        and "mut" in w.location
        for w in result.warnings
    )
    assert any(
        w.metadata.get("pattern_id") == "P-SHARED-CONTROL-UNDISCLOSED"
        and "wt" in w.location
        for w in result.warnings
    )


def test_cross_fig_reuse_gold_eval(tmp_path: Path):
    report = run_and_evaluate(
        "cross_fig_reuse",
        input_paths=[CASE],
        warnings_out=tmp_path / "cross_fig_warnings.json",
    )
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["Y1"]["matched"] is True
    assert by_id["Y2"]["matched"] is True
    assert report["required_recall"] == 1.0
