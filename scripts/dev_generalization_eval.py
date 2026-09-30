#!/usr/bin/env python3
"""dev / holdout split eval with ablations (Legend n extraction, figure panel OCR).

holdout cases are scored only after their gold is confirmed and frozen; the
report shows holdout as aggregates. ``--reveal CASE`` prints that case's errors
and moves it to dev (burn). Protocol: fixtures/gold/panel_extract/HUMAN_REVIEW.md

Examples:
  # rules / 7B minimal / prompt-minimal / current on every case with gold
  python scripts/dev_generalization_eval.py --task legend

  # guard ablation, update the rule ledger from holdout contributions
  python scripts/dev_generalization_eval.py --task legend --configs ablation,prompt_minimal \\
    --update-ledger

  # accept a change only if dev does not regress and holdout stays in tolerance
  python scripts/dev_generalization_eval.py --task legend --configs current --gate

  # OCR engines / run filter through the product path
  python scripts/dev_generalization_eval.py --task panel_ocr --configs all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pre_peer_checker.eval import generalization as gz


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=gz.TASKS, required=True)
    ap.add_argument("--split", choices=("dev", "holdout", "all"), default="all")
    ap.add_argument("--case", action="append", default=[], help="Restrict to case id (repeatable)")
    ap.add_argument(
        "--configs",
        default="default",
        help="Comma list or keywords default / ablation / all "
        "(legend: rules_only, llm_minimal, prompt_minimal, current, auto, current-minus-<guard>; "
        f"panel_ocr: {', '.join(gz.OCR_CONFIGS)})",
    )
    ap.add_argument("--prefer", default="auto", help="LLM backend: auto | mlx | cuda")
    ap.add_argument("--profile", default=None, help="LLM registry profile (default: registry default)")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--no-cache", action="store_true", help="Do not reuse cached LLM responses")
    ap.add_argument("--reveal", action="append", default=[], help="Show errors of a holdout case and burn it")
    ap.add_argument("--revise-gold", default=None, metavar="REASON", help="Accept an edited frozen holdout gold")
    ap.add_argument("--gate", action="store_true", help="Compare with the last accepted baseline")
    ap.add_argument("--gate-config", default="current")
    ap.add_argument("--rebaseline", action="store_true", help="Accept this run as the new baseline")
    ap.add_argument("--update-ledger", action="store_true", help="Write holdout contributions to rule_ledger.json")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument(
        "--legend-source",
        choices=gz.LEGEND_SOURCES,
        default="product",
        help="product: locate legends like a verification run (default); raw: eval-only pdftotext -raw splitter",
    )
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args(argv)

    configs = gz.expand_configs(args.task, args.configs)
    cases = gz.cases_with_gold(
        gz.discover_cases(split=args.split, case_ids=args.case or None), args.task
    )
    if not cases:
        print(f"no cases with {args.task} gold (split={args.split})", file=sys.stderr)
        return 2

    cache_dir = None if args.no_cache else gz.OUTPUT_ROOT / "llm_cache"

    def llm_factory():
        return gz.make_llm_generate(
            prefer=args.prefer,
            profile_id=args.profile,
            max_tokens=args.max_tokens,
            cache_dir=cache_dir,
        )

    try:
        report = gz.run_generalization(
            args.task,
            configs,
            cases,
            llm_factory=llm_factory,
            reveal=args.reveal,
            revise_reason=args.revise_gold,
            bootstrap_iters=args.bootstrap,
            legend_source=args.legend_source,
        )
    except (gz.GoldPolicyError, gz.GoldFrozenError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(gz.format_report(report))
    for cid in args.reveal:
        view = report["results"][configs[-1]]["cases"][cid]
        print(f"\n[reveal] {cid} ({configs[-1]}) -> moved to dev")
        print(json.dumps(view, ensure_ascii=False, indent=2))

    stamp = report["created_at"].replace(":", "").replace("-", "")
    out = args.output or gz.OUTPUT_ROOT / f"{args.task}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    gate = None
    if args.gate or args.rebaseline:
        summary = gz.gate_summary(report, args.gate_config)
        baseline = None if args.rebaseline else gz.load_baseline(args.task, args.gate_config)
        gate = gz.gate_check(summary, baseline)
        if args.rebaseline:
            gate["reasons"] = ["rebaseline requested"]
        print(f"\ngate ({args.gate_config}): {'PASS' if gate['passed'] else 'FAIL'}")
        for r in gate["reasons"]:
            print(f"  - {r}")
        exit_code = 0 if gate["passed"] else 1
        report["gate"] = gate
        gz.append_history(
            {
                "task": args.task,
                "created_at": report["created_at"],
                "git_rev": report["git_rev"],
                "accepted": gate["passed"],
                "rebaseline": args.rebaseline,
                "report": str(out),
                "summary": summary,
            }
        )

    if args.update_ledger:
        ledger = json.loads(gz.LEDGER_PATH.read_text(encoding="utf-8"))
        updated = gz.update_ledger(ledger, report)
        if updated:
            gz.LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"\nledger updated: {', '.join(updated)}")
        else:
            print("\nledger: nothing to update (need holdout cases and the rule's baseline+ablation configs)")

    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
