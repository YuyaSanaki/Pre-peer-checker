#!/usr/bin/env python3
"""Clean-corpus false-positive eval: run published papers, count unexplained warnings.

    # run every paper under input/panel_extract and compare with the frozen baseline
    python scripts/dev_clean_corpus_eval.py
    # re-score cached warnings only (no pipeline run)
    python scripts/dev_clean_corpus_eval.py --cached
    # freeze the current counts as the new baseline
    python scripts/dev_clean_corpus_eval.py --cached --freeze
    # CI-style gate: exit 1 if any (case, pattern) rose above the baseline
    python scripts/dev_clean_corpus_eval.py --cached --gate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pre_peer_checker.eval.clean_corpus import (  # noqa: E402
    BASELINE_PATH,
    DEFAULT_ROOT,
    compare_to_baseline,
    discover_cases,
    freeze,
    load_cached,
    run_case,
    summarize,
)
from pre_peer_checker.eval.history import append_history  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--case", action="append", default=None, help="limit to slot(s)")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "outputs" / "clean_corpus")
    ap.add_argument("--cached", action="store_true", help="score cached warnings, run only missing")
    ap.add_argument("--legend-llm", default="off", help="off|auto|on")
    ap.add_argument("--llm-profile", default=None)
    ap.add_argument("--full", action="store_true", help="keep raster/scale-bar OCR on (slow)")
    ap.add_argument("--freeze", action="store_true", help=f"write {BASELINE_PATH.name}")
    ap.add_argument("--gate", action="store_true", help="exit 1 on regression vs baseline")
    args = ap.parse_args(argv)

    legend_llm: bool | str = False if args.legend_llm == "off" else args.legend_llm
    cases = discover_cases(args.root, args.case)
    if not cases:
        print(f"no cases under {args.root}", file=sys.stderr)
        return 2
    reports = []
    for case in cases:
        rep = load_cached(args.out_dir, case.case_id) if args.cached else None
        if rep is None:
            print(f"[run] {case.case_id}", file=sys.stderr, flush=True)
            try:
                rep = run_case(
                    case,
                    out_dir=args.out_dir,
                    legend_llm=legend_llm,
                    legend_llm_profile=args.llm_profile,
                    fast=not args.full,
                )
            except Exception as exc:  # one broken paper must not hide the rest
                print(f"[error] {case.case_id}: {exc}", file=sys.stderr)
                continue
        reports.append(rep)

    summary = summarize(reports)
    config = {"legend_llm": args.legend_llm, "profile": args.llm_profile, "fast": not args.full}
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    base = json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.is_file() else None
    cmp = compare_to_baseline(summary, base) if base else None
    if cmp:
        for r in cmp["regressions"]:
            print(f"REGRESSION {r['case_id']} {r['pattern_id']}: {r['baseline']} -> {r['now']}")
        for r in cmp["improvements"]:
            print(f"improved   {r['case_id']} {r['pattern_id']}: {r['baseline']} -> {r['now']}")

    append_history(
        "clean_corpus",
        {
            "n_cases": summary["n_cases"],
            "fp_per_paper": summary["fp_per_paper"],
            "papers_with_fp": summary["papers_with_fp"],
            "by_pattern": summary["unexplained_by_pattern"],
            "regressions": len(cmp["regressions"]) if cmp else None,
        },
        config=config,
    )
    (args.out_dir / "summary.json").write_text(
        json.dumps({"summary": summary, "baseline_compare": cmp}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.freeze:
        print(f"froze {freeze(summary, config=config)}")
    if args.gate and cmp and not cmp["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
