"""Benchmark 適合率・再現率スイート（check_reference 由来ゴールド相対）。

製品は任意論文向け。本モジュールは回帰用メトリクスを記録するだけで、
「100%」や集団精度を主張しない。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.eval.gold_eval import (
    FIXTURES,
    REPO_ROOT,
    load_manifest,
    resolve_case_focus_inputs,
    run_and_evaluate,
)


def _case_available(case_id: str) -> bool:
    gold = FIXTURES / "gold" / case_id / "gold_warnings.json"
    if not gold.is_file():
        return False
    try:
        manifest = load_manifest(case_id)
    except FileNotFoundError:
        return case_id == "demo"  # demo always has gold
    rel = manifest.get("root_relative")
    if not rel:
        return True
    root = REPO_ROOT / rel
    if root.exists():
        return True
    return (FIXTURES / "synthetic" / case_id).exists()


def run_case(
    case_id: str,
    *,
    out_dir: Path,
    legend_llm: bool = False,
    legend_llm_prefer: str = "auto",
    legend_llm_profile: str | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings_out = out_dir / f"{case_id}_warnings.json"
    kwargs: dict[str, Any] = {
        "warnings_out": warnings_out,
        "corpus_present": False,
        "legend_llm": legend_llm,
        "legend_llm_prefer": legend_llm_prefer,
        "legend_llm_profile": legend_llm_profile,
    }
    if case_id == "private_benchmark":
        inputs = resolve_case_focus_inputs(case_id)
        if inputs:
            kwargs["input_paths"] = inputs
    try:
        report = run_and_evaluate(case_id, **kwargs)
    except FileNotFoundError as exc:
        return {
            "case_id": case_id,
            "status": "skipped",
            "reason": str(exc),
        }
    report["status"] = "ok"
    report["legend_llm"] = legend_llm
    report["legend_llm_profile"] = legend_llm_profile
    (out_dir / f"{case_id}_metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def build_suite_report(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in case_reports if r.get("status") == "ok"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Benchmark precision/recall vs fixtures/gold (informed by check_reference/). "
            "Engine must stay pattern-generic; do not claim 100%."
        ),
        "check_reference_role": (
            "Design-time gold source only (PubPeer notes, explanation decks). "
            "Not mounted as runtime input for end users."
        ),
        "lora_stance": (
            "LoRA is NOT required for the shipping product. Core verification is "
            "deterministic patterns in fixtures/patterns/. Legend LLM uses rules + "
            "optional few-shot prompts; LoRA only if extraction quality fails on "
            "diverse public legends after rules/prompting — never for guilt scoring."
        ),
        "cases": case_reports,
        "summary": {
            "n_cases_ok": len(ok),
            "recalls": {r["case_id"]: r.get("recall") for r in ok},
            "precisions": {r["case_id"]: r.get("precision") for r in ok},
            "required_recalls": {r["case_id"]: r.get("required_recall") for r in ok},
            "recommended_focus": {
                r["case_id"]: (r.get("layers") or {}).get("recommended_focus") for r in ok
            },
            "layer_misses": {
                r["case_id"]: {
                    name: (r.get("layers") or {}).get("by_layer", {}).get(name, {}).get("misses", 0)
                    for name in ("parser", "linking", "match", "unknown")
                }
                for r in ok
            },
        },
    }


DEFAULT_CASES = (
    "demo",
    "shared_control",
    "image_reuse",
    "cross_fig_reuse",
    "script_swap",
    "private_benchmark",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record gold-relative precision/recall for available benchmark cases"
    )
    parser.add_argument(
        "--case",
        action="append",
        default=None,
        help="Case id (repeatable). Default: demo, shared_control, private_benchmark(if present)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("outputs/metrics/suite_report.json"),
    )
    parser.add_argument(
        "--example-out",
        type=Path,
        default=Path("fixtures/metrics/suite_report.example.json"),
        help="Also write a redacted/synthetic-only example for git (optional)",
    )
    parser.add_argument(
        "--legend-llm",
        action="store_true",
        help="Enable Legend LLM path (default profile unless --llm-profile)",
    )
    parser.add_argument(
        "--llm-profile",
        default=None,
        help="LLM profile id (default: registry default_llm_profile when --legend-llm)",
    )
    parser.add_argument(
        "--legend-llm-prefer",
        default="auto",
        help="auto|mlx|cuda|none (CI without MLX: use none)",
    )
    args = parser.parse_args(argv)

    cases = list(args.case) if args.case else list(DEFAULT_CASES)
    out_dir = args.output.parent
    llm_profile = args.llm_profile
    if args.legend_llm and not llm_profile:
        from pre_peer_checker.llm.registry import effective_llm_profile_id

        llm_profile = effective_llm_profile_id()
    reports: list[dict[str, Any]] = []
    for case_id in cases:
        if not _case_available(case_id):
            reports.append(
                {
                    "case_id": case_id,
                    "status": "skipped",
                    "reason": "gold or input not available in this checkout",
                }
            )
            continue
        reports.append(
            run_case(
                case_id,
                out_dir=out_dir,
                legend_llm=bool(args.legend_llm),
                legend_llm_prefer=args.legend_llm_prefer,
                legend_llm_profile=llm_profile,
            )
        )

    suite = build_suite_report(reports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(suite["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")

    # Trackable example: synthetic cases only (no private paths/numbers beyond public fixtures)
    if args.example_out:
        public = [
            r
            for r in reports
            if r.get("case_id") in {"demo", "shared_control"} and r.get("status") == "ok"
        ]
        example = build_suite_report(public)
        example["note"] = (
            "Checked-in example from public synthetic golds only. "
            "Full local suite (incl. private_benchmark) lives under outputs/metrics/."
        )
        args.example_out.parent.mkdir(parents=True, exist_ok=True)
        args.example_out.write_text(
            json.dumps(example, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote example {args.example_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
