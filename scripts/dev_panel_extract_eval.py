#!/usr/bin/env python3
"""Score legend panel extraction in warnings.json against panel_extract_gold.

Example:
  python scripts/dev_panel_extract_eval.py \\
    --gold fixtures/gold/private_benchmark/panel_extract_gold.json \\
    --warnings outputs/metrics/private_benchmark_bakeoff_qwen2.5-32b-hf_warnings.json \\
    --warnings outputs/metrics/private_benchmark_bakeoff_qwen2.5-7b-hf_warnings.json \\
    -o outputs/metrics/panel_extract_eval.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _norm(s: str) -> str:
    t = (s or "").strip().lower()
    t = t.replace("−", "-").replace("–", "-")
    t = re.sub(r"\s+", " ", t)
    return t


def _group_tokens(s: str) -> set[str]:
    t = _norm(s)
    t = t.replace("(", " ").replace(")", " ").replace(",", " ").replace("+/-", " ")
    t = t.replace("+/-", " ").replace("±", " ")
    parts = [x for x in re.split(r"[^\w]+", t) if x]
    # keep genotype-style tokens; drop pure empties
    return set(parts)


def _group_match(pred: str, gold: str, aliases: list[str] | None) -> bool:
    cands = [_norm(gold), *(_norm(a) for a in (aliases or []))]
    p = _norm(pred)
    if not gold and not pred:
        return True
    if p in cands:
        return True
    # soft contain either way for "control (early L3)" vs "control, early L3"
    if any(c and (c in p or p in c) for c in cands if c):
        return True
    pt = _group_tokens(pred)
    if not pt:
        return False
    for c in cands:
        ct = _group_tokens(c)
        if ct and (ct <= pt or pt <= ct):
            return True
    return False


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


def score_one(gold: dict[str, Any], preds: list[dict[str, Any]]) -> dict[str, Any]:
    items = gold.get("items") or []
    hit = 0
    details: list[dict[str, Any]] = []
    forbid_fp = 0
    for it in items:
        fig = str(it.get("figure") or "")
        panel = str(it.get("panel") or "").upper()
        group = str(it.get("group") or "")
        n_gold = it.get("n")
        aliases = list(it.get("aliases_group") or [])
        matched = False
        matched_row = None
        for pr in preds:
            if pr["figure"] != fig or pr["panel"] != panel:
                continue
            if pr["n"] != n_gold:
                continue
            if _group_match(pr["group"], group, aliases):
                matched = True
                matched_row = pr
                break
        if matched:
            hit += 1
        # forbid wrong panel letters (e.g. Fig5 A when gold is E)
        for bad in it.get("forbid_panels") or []:
            for pr in preds:
                if (
                    pr["figure"] == fig
                    and pr["panel"] == str(bad).upper()
                    and pr["n"] == n_gold
                ):
                    forbid_fp += 1
        details.append(
            {
                "id": it.get("id"),
                "matched": matched,
                "gold": {"figure": fig, "panel": panel, "group": group, "n": n_gold},
                "pred": matched_row,
                "difficulty": it.get("difficulty"),
            }
        )
    total = len(items)
    return {
        "n_items": total,
        "n_hit": hit,
        "recall": (hit / total) if total else None,
        "forbid_panel_fp": forbid_fp,
        "items": details,
    }


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
        print(
            f"{label}: recall={sc['recall']:.3f} ({sc['n_hit']}/{sc['n_items']}) "
            f"forbid_fp={sc['forbid_panel_fp']}"
        )

    payload = {"gold": str(args.gold), "runs": rows_out}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
