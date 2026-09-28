"""Unit tests for remaining P1/P2 planned engines."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.pipeline.orchestrator import run_verification

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "fixtures" / "synthetic"

CASES = (
    ("errorbar_sem_sd", "P-ERRORBAR-SEM-SD-MISMATCH"),
    ("multiplicity", "P-STAT-MULTIPLICITY-GAP"),
    ("survival_noninteger", "P-SURVIVAL-COUNT-NONINTEGER"),
    ("scale_mag", "P-SCALE-MAG-INCONSISTENT"),
    ("count_n", "P-COUNT-N-MISMATCH"),
)


def test_p1_p2_orchestrator_emits():
    for case, pid in CASES:
        result = run_verification([SYN / case])
        assert any(
            w.metadata.get("pattern_id") == pid for w in result.warnings
        ), (case, [w.metadata.get("pattern_id") for w in result.warnings])


def test_p1_p2_gold_eval(tmp_path: Path):
    for case, _pid in CASES:
        report = run_and_evaluate(
            case,
            input_paths=[SYN / case],
            warnings_out=tmp_path / f"{case}.json",
        )
        assert report["required_recall"] == 1.0, (case, report)
