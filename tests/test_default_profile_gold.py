"""配布ホスト向けプロファイルでも必須ゴールド required_recall=1.0.

- Mac / MLX あり → ``qwen2.5-32b-mlx``
- MLX 無し（Linux / CUDA 等）→ ``qwen2.5-32b-hf`` + prefer=cuda（規則フォールバックしない）

全 matrix の LLM 通しは重いので、既定は代表ケース。
フルは ``PRE_PEER_CHECKER_FULL_DIST_GOLD=1``。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.llm.backend import MLXBackend, TransformersBackend
from pre_peer_checker.llm.registry import (
    distribution_legend_llm_kwargs,
    effective_llm_profile_id,
    load_registry,
)
from pre_peer_checker.pipeline.orchestrator import run_verification

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "fixtures" / "patterns" / "pattern_synthetic_matrix.json"

# Representative ci_required cases for default CI (real LLM inference)
_SMOKE_CASES = frozenset(
    {
        "demo",
        "shared_control",
        "image_reuse",
        "stats_recalc",
        "scale_mag",
    }
)


def _load_matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _full_gold() -> bool:
    return os.environ.get("PRE_PEER_CHECKER_FULL_DIST_GOLD", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _gold_input(case_id: str, *, corpus: bool) -> dict:
    syn = ROOT / "fixtures" / "synthetic" / case_id
    if case_id in {"image_reuse", "image_partial"}:
        return {
            "input_paths": [syn / "manuscript"],
            "corpus_paths": [syn / "corpus"] if corpus else None,
            "corpus_present": corpus,
        }
    return {"input_paths": [syn], "corpus_present": False}


def _ci_required_gold_entries() -> list[dict]:
    entries = [e for e in _load_matrix()["entries"] if e.get("ci_required")]
    if _full_gold():
        return entries
    return [
        e
        for e in entries
        if e.get("harness") == "unit" or e.get("case_id") in _SMOKE_CASES
    ]


def test_distribution_default_profiles():
    profiles, default_llm, default_vlm = load_registry()
    assert default_llm == "qwen2.5-32b-mlx"
    assert default_vlm == "qwen2.5-vl-7b"
    assert "qwen2.5-32b-hf" in profiles
    assert "qwen2.5-vl-7b" in profiles


def test_effective_llm_profile_host_aware():
    """MLX 無しなら CUDA 本線 32b-hf（prefer=none にしない）."""
    eff = effective_llm_profile_id()
    kw = distribution_legend_llm_kwargs()
    assert kw["legend_llm"] is True
    assert kw["legend_llm_prefer"] != "none"
    if MLXBackend.available():
        assert eff == "qwen2.5-32b-mlx"
        assert kw["legend_llm_profile"] == "qwen2.5-32b-mlx"
    else:
        assert eff == "qwen2.5-32b-hf"
        assert kw["legend_llm_profile"] == "qwen2.5-32b-hf"
        assert kw["legend_llm_prefer"] == "cuda"


@pytest.mark.skipif(
    not (MLXBackend.available() or TransformersBackend.available()),
    reason="no LLM backend available",
)
def test_default_profile_wiring_runs_inference(tmp_path: Path):
    ms = tmp_path / "ms"
    ms.mkdir()
    from docx import Document

    doc = Document()
    doc.add_paragraph("Figure 1. Quantification. n=5 (A).")
    doc.save(ms / "main.docx")
    kwargs = distribution_legend_llm_kwargs()
    result = run_verification([ms], **kwargs)
    metas = result.artifacts.get("legend_llm") or []
    assert metas, "legend_llm meta missing"
    assert any(m.get("legend_llm_enabled") is True for m in metas)
    assert any(m.get("llm_profile") == kwargs["legend_llm_profile"] for m in metas)


@pytest.mark.skipif(
    not (MLXBackend.available() or TransformersBackend.available()),
    reason="no LLM backend available",
)
@pytest.mark.parametrize(
    "entry",
    _ci_required_gold_entries(),
    ids=lambda e: e["pattern_id"],
)
def test_default_profile_ci_required_gold(entry: dict, tmp_path: Path):
    """必須ゴールドをホスト向け高精度プロファイルで再現."""
    if entry["harness"] == "unit":
        pytest.skip("unit harness (no gold_eval case)")
        return

    case_id = entry["case_id"]
    kwargs = _gold_input(case_id, corpus=bool(entry.get("corpus")))
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    llm_kw = distribution_legend_llm_kwargs()
    report = run_and_evaluate(
        case_id,
        warnings_out=tmp_path / f"dist_{case_id}_warnings.json",
        **kwargs,
        **llm_kw,
    )
    assert report.get("required_recall") == 1.0, {
        "case": case_id,
        "llm": llm_kw,
        "report": report,
    }
    gold_item = entry.get("gold_item_id")
    if gold_item:
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id[gold_item]["matched"] is True, by_id[gold_item]
