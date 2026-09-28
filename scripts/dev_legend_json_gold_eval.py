#!/usr/bin/env python3
"""Score Legend 読む JSON against synthetic gold (rules or LLM stub / bakeoff).

GPU 不要（rules）::

    python scripts/dev_legend_json_gold_eval.py

LLM bakeoff（CUDA）::

    TORCH_DISABLE_NATIVE_JIT=1 python scripts/dev_legend_json_gold_eval.py \\
      --prefer cuda --profile qwen2.5-7b --require-llm \\
      -o outputs/metrics/legend_json_gold_7b.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

MATRIX_DEFAULT = ROOT / "fixtures" / "gold" / "legend_json" / "legend_json_synthetic_matrix.json"


def _norm(s: str) -> str:
    t = (s or "").strip().lower()
    t = t.replace("−", "-").replace("–", "-")
    return re.sub(r"\s+", " ", t)


def _group_tokens(s: str) -> set[str]:
    t = _norm(s).replace("(", " ").replace(")", " ").replace(",", " ")
    t = t.replace("+/-", " ").replace("±", " ")
    return {x for x in re.split(r"[^\w]+", t) if x}


def _group_match(pred: str, gold: str, aliases: list[str] | None) -> bool:
    cands = [_norm(gold), *(_norm(a) for a in (aliases or []))]
    p = _norm(pred)
    if not gold and not pred:
        return True
    if p in cands:
        return True
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


def _rows_from_parsed(parsed: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in parsed.panels:
        groups = list(p.groups or [])
        if not groups:
            rows.append({"panel": p.panel.upper(), "n": p.n, "group": ""})
        else:
            for g in groups:
                rows.append({"panel": p.panel.upper(), "n": p.n, "group": str(g)})
    return rows


def _hit(expect: dict, preds: list[dict]) -> bool:
    panel = str(expect.get("panel") or "").upper()
    n = expect.get("n")
    group = str(expect.get("group") or "")
    aliases = list(expect.get("aliases_group") or [])
    for pr in preds:
        if pr["panel"] != panel or pr["n"] != n:
            continue
        if _group_match(pr["group"], group, aliases):
            return True
    return False


def score_matrix(
    matrix: dict[str, Any],
    *,
    llm_generate=None,
    prefer_llm: bool = False,
    ci_only: bool = True,
) -> dict[str, Any]:
    from pre_peer_checker.llm.legend_extract import extract_legend_json_hybrid

    details: list[dict[str, Any]] = []
    hit = 0
    total = 0
    forbid_fp = 0
    for entry in matrix.get("entries") or []:
        if ci_only and not entry.get("ci_required"):
            continue
        preds = _rows_from_parsed(
            extract_legend_json_hybrid(
                str(entry["legend_text"]),
                figure_hint=str(entry["figure"]),
                llm_generate=llm_generate,
                prefer_llm=prefer_llm and llm_generate is not None,
            )
        )
        missing = []
        for ex in entry.get("expect") or []:
            total += 1
            if _hit(ex, preds):
                hit += 1
            else:
                missing.append(ex)
        bad = []
        for fb in entry.get("forbid") or []:
            panel = str(fb.get("panel") or "").upper()
            n = fb.get("n")
            if any(pr["panel"] == panel and pr["n"] == n for pr in preds):
                forbid_fp += 1
                bad.append(fb)
        details.append(
            {
                "id": entry.get("id"),
                "ok": not missing and not bad,
                "missing": missing,
                "forbid_hit": bad,
                "preds": preds,
            }
        )
    return {
        "n_entries": len(details),
        "n_expect": total,
        "hit": hit,
        "recall": (hit / total) if total else 0.0,
        "forbid_fp": forbid_fp,
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--matrix", type=Path, default=MATRIX_DEFAULT)
    p.add_argument("--prefer", default="none", help="none|auto|mlx|cuda|transformers")
    p.add_argument("--profile", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--require-llm", action="store_true")
    p.add_argument("--all-entries", action="store_true", help="Include non-ci_required")
    p.add_argument("-o", "--out", type=Path, default=None)
    args = p.parse_args(argv)

    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    llm_generate = None
    backend_meta: dict[str, Any] | None = None
    prefer = (args.prefer or "none").lower()
    if prefer not in {"none", "off", "0", "false"}:
        from pre_peer_checker.llm.backend import select_backend

        be = select_backend(prefer, model_id=args.model, profile_id=args.profile)
        if be is None:
            print("FAIL: no text LLM backend", file=sys.stderr)
            return 2
        backend_meta = be.info().__dict__
        llm_generate = be.generate

    if args.require_llm and llm_generate is None:
        print("FAIL: --require-llm but no backend", file=sys.stderr)
        return 2

    report = score_matrix(
        matrix,
        llm_generate=llm_generate,
        prefer_llm=llm_generate is not None,
        ci_only=not args.all_entries,
    )
    report["backend"] = backend_meta
    report["prefer_llm"] = llm_generate is not None
    print(
        f"recall={report['hit']}/{report['n_expect']} "
        f"({report['recall']:.3f}) forbid_fp={report['forbid_fp']}"
    )
    for d in report["details"]:
        mark = "OK" if d["ok"] else "MISS"
        print(f"  {mark}  {d['id']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.out}")
    if report["forbid_fp"] > 0 or report["hit"] < report["n_expect"]:
        return 3
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
