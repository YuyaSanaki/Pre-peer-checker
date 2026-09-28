#!/usr/bin/env python3
"""配布ホスト向けプロファイルで必須ゴールドを通す受入スクリプト.

- Mac / MLX → ``qwen2.5-7b-mlx``
- それ以外 → ``qwen2.5-7b-hf`` + CUDA（規則フォールバックしない）

::

    python scripts/dev_default_profile_gold.py
    python scripts/dev_default_profile_gold.py --case demo
    PRE_PEER_CHECKER_LLM_PROFILE=qwen2.5-32b-hf python scripts/dev_default_profile_gold.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pre_peer_checker.eval.gold_eval import run_and_evaluate  # noqa: E402
from pre_peer_checker.llm.registry import distribution_legend_llm_kwargs  # noqa: E402

MATRIX = ROOT / "fixtures" / "patterns" / "pattern_synthetic_matrix.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", action="append", default=None, help="gold case id (repeatable)")
    p.add_argument(
        "--prefer",
        default=None,
        help="override prefer (default: host-aware from distribution_legend_llm_kwargs)",
    )
    p.add_argument("--llm-profile", default=None, help="override profile id")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("outputs/default_profile_gold.json"),
    )
    args = p.parse_args(argv)

    llm_kw = distribution_legend_llm_kwargs()
    if args.llm_profile:
        llm_kw["legend_llm_profile"] = args.llm_profile
    if args.prefer:
        llm_kw["legend_llm_prefer"] = args.prefer

    if args.case:
        cases = list(args.case)
    else:
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        cases = []
        for e in matrix["entries"]:
            if e.get("ci_required") and e.get("harness") == "gold_eval":
                cid = e["case_id"]
                if cid not in cases:
                    cases.append(cid)

    out: dict = {
        "llm": llm_kw,
        "cases": [],
    }
    failed = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"profile={llm_kw['legend_llm_profile']} prefer={llm_kw['legend_llm_prefer']} "
        f"n_cases={len(cases)}"
    )
    for case_id in cases:
        report = run_and_evaluate(
            case_id,
            warnings_out=args.output.parent / f"dist_{case_id}_warnings.json",
            **llm_kw,
        )
        row = {
            "case_id": case_id,
            "required_recall": report.get("required_recall"),
            "false_negative_item_ids": report.get("false_negative_item_ids"),
        }
        out["cases"].append(row)
        ok = report.get("required_recall") == 1.0
        print(f"{'OK' if ok else 'FAIL'} {case_id} required_recall={report.get('required_recall')}")
        if not ok:
            failed += 1

    out["n_failed"] = failed
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
