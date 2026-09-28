#!/usr/bin/env python3
"""Run Legend LLM gold_eval across text profiles (DGX Spark bakeoff).

Example:
  TORCH_DISABLE_NATIVE_JIT=1 python scripts/dev_legend_llm_bakeoff.py \\
    --profiles qwen2.5-3b-hf,qwen2.5-7b-hf,qwen2.5-32b-hf \\
    -o outputs/metrics/legend_llm_bakeoff.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure CUDA path does not trip Triton native JIT rebuilds.
os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.llm.registry import get_profile

# Lightweight alias not in registry (Spark CPU/CUDA smoke size).
EXTRA_MODELS: dict[str, dict[str, str]] = {
    "qwen2.5-3b-hf": {
        "model_id": "Qwen/Qwen2.5-3B-Instruct",
        "prefer": "cuda",
        "family": "qwen",
        "size_hint": "3B",
        "label": "Qwen2.5-3B Instruct (HF)",
    },
}


def _resolve(pid: str) -> dict[str, Any]:
    if pid in EXTRA_MODELS:
        return {"profile_id": pid, **EXTRA_MODELS[pid]}
    p = get_profile(pid)
    if p is None:
        raise SystemExit(f"unknown profile: {pid}")
    return {
        "profile_id": pid,
        "model_id": p.model_id,
        "prefer": "cuda" if p.prefer in {"transformers", "cuda", "hf", "auto"} else p.prefer,
        "family": p.family,
        "size_hint": p.size_hint,
        "label": p.label,
    }


def run_one(case_id: str, spec: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    pid = spec["profile_id"]
    warnings_out = out_dir / f"{case_id}_bakeoff_{pid}_warnings.json"
    t0 = time.perf_counter()
    err: str | None = None
    report: dict[str, Any] | None = None
    try:
        report = run_and_evaluate(
            case_id,
            warnings_out=warnings_out,
            legend_llm=True,
            legend_llm_prefer=str(spec.get("prefer") or "cuda"),
            legend_llm_model=str(spec["model_id"]),
            legend_llm_profile=None if pid in EXTRA_MODELS else pid,
        )
    except Exception as exc:  # noqa: BLE001 — bakeoff must continue
        err = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.perf_counter() - t0, 1)

    row: dict[str, Any] = {
        **spec,
        "elapsed_s": elapsed,
        "error": err,
        "warnings_path": str(warnings_out) if warnings_out.exists() else None,
    }
    if report is None:
        return row

    llm = None
    # Prefer status embedded by latest warnings payload via evaluating path
    if warnings_out.exists():
        try:
            payload = json.loads(warnings_out.read_text(encoding="utf-8"))
            arts = payload.get("artifacts") or {}
            llm = arts.get("legend_llm_status")
            if llm is None:
                cov = payload.get("run_coverage") or {}
                llm = cov.get("legend_llm_status")
        except Exception:
            llm = None

    items = {
        it["id"]: {
            "matched": it.get("matched"),
            "fail_layer": it.get("fail_layer"),
            "layer": it.get("layer"),
        }
        for it in (report.get("items") or [])
        if isinstance(it, dict) and it.get("id")
    }
    row.update(
        {
            "required_recall": report.get("required_recall"),
            "required_hit": report.get("required_hit"),
            "required_total": report.get("required_total"),
            "precision": report.get("precision"),
            "n_warnings_emitted": report.get("n_warnings_emitted"),
            "recommended_focus": (report.get("layers") or {}).get("recommended_focus"),
            "items": items,
            "legend_llm_status": llm,
        }
    )
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Legend LLM profile bakeoff on gold case")
    parser.add_argument("--case", default="private_benchmark")
    parser.add_argument(
        "--profiles",
        default="qwen2.5-3b-hf,qwen2.5-7b-hf,qwen2.5-32b-hf",
        help="Comma-separated profile IDs",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("outputs/metrics/legend_llm_bakeoff.json"),
    )
    args = parser.parse_args(argv)

    ids = [x.strip() for x in args.profiles.split(",") if x.strip()]
    specs = [_resolve(pid) for pid in ids]
    out_dir = args.output.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for spec in specs:
        print(f"=== bakeoff {spec['profile_id']} ({spec['model_id']}) ===", flush=True)
        row = run_one(args.case, spec, out_dir)
        rows.append(row)
        print(
            f"  recall={row.get('required_recall')} precision={row.get('precision')} "
            f"elapsed={row.get('elapsed_s')}s err={row.get('error')}",
            flush=True,
        )
        # Free GPU memory between models
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_id": args.case,
        "host_notes": [
            "Text Legend extraction only (VLM not wired).",
            "prefer=cuda on Spark; TORCH_DISABLE_NATIVE_JIT=1 recommended.",
            "Mac 16GB deploy targets remain MLX 7B/3B after teacher bakeoff.",
        ],
        "n_profiles": len(rows),
        "rows": rows,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
