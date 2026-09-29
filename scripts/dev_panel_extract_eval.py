#!/usr/bin/env python3
"""Score legend panel extraction in warnings.json against panel_extract_gold.

Example:
  python scripts/dev_panel_extract_eval.py \\
    --gold fixtures/gold/private_benchmark/panel_extract_gold.json \\
    --warnings outputs/metrics/private_benchmark_bakeoff_qwen2.5-32b-hf_warnings.json \\
    --warnings outputs/metrics/private_benchmark_bakeoff_qwen2.5-7b-hf_warnings.json \\
    -o outputs/metrics/panel_extract_eval.json

Split-aware (dev / holdout) and ablation runs: scripts/dev_generalization_eval.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pre_peer_checker.eval.panel_extract_score import (
    format_score,
    score_one,
    score_precision,
)

__all__ = ["format_score", "load_pred_rows", "score_one", "score_precision"]


def load_pred_rows(warnings_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(warnings_path.read_text(encoding="utf-8"))
    nm = (payload.get("run_coverage") or {}).get("n_matrix") or (
        (payload.get("artifacts") or {}).get("n_matrix") or []
    )
    rows: list[dict[str, Any]] = []
    for r in nm:
        if not isinstance(r, dict):
            continue
        ms = r.get("manuscript") or {}
        rows.append(
            {
                "figure": str(r.get("figure") or ""),
                "panel": str(r.get("panel") or "").upper(),
                "group": str(r.get("group") or ""),
                "n": (ms.get("n") if isinstance(ms, dict) else None),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Panel extraction gold eval")
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument(
        "--warnings",
        type=Path,
        action="append",
        required=True,
        help="Bakeoff/run warnings.json (repeatable)",
    )
    ap.add_argument("-o", "--output", type=Path, default=Path("outputs/metrics/panel_extract_eval.json"))
    args = ap.parse_args(argv)

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    rows_out: list[dict[str, Any]] = []
    for wp in args.warnings:
        preds = load_pred_rows(wp)
        sc = score_one(gold, preds)
        label = wp.stem.replace("private_benchmark_bakeoff_", "").replace("_warnings", "")
        rows_out.append({"label": label, "warnings": str(wp), **sc})
        print(format_score(label, sc))

    payload = {"gold": str(args.gold), "runs": rows_out}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
