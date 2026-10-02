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
    load_gold,
    load_manifest,
    load_warnings,
    resolve_case_focus_inputs,
    run_and_evaluate,
    warning_supports_item,
)

PATTERNS_PATH = FIXTURES / "patterns" / "pubpeer_patterns.json"
MATRIX_PATH = FIXTURES / "patterns" / "pattern_synthetic_matrix.json"
CLEAN_SUMMARY_PATH = REPO_ROOT / "outputs" / "clean_corpus" / "summary.json"


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


def all_gold_cases() -> list[str]:
    """Every case with a committed ``gold_warnings.json``."""
    return sorted(
        p.parent.name for p in (FIXTURES / "gold").glob("*/gold_warnings.json") if p.is_file()
    )


def _pid(warning: dict[str, Any]) -> str:
    meta = warning.get("metadata") or {}
    return str(meta.get("pattern_id") or warning.get("pattern_id") or "unknown")


def pattern_dashboard(
    case_reports: list[dict[str, Any]],
    *,
    out_dir: Path,
    clean_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-``pattern_id`` recall / precision across gold cases + clean-corpus FP.

    - recall: required gold items of that pattern matched / total
    - precision: emitted warnings of that pattern that support any gold item of their case
    - clean_fp: unexplained active warnings on the clean corpus (false-positive candidates)
    """
    catalog = json.loads(PATTERNS_PATH.read_text(encoding="utf-8")).get("patterns") or []
    matrix = {
        e["pattern_id"]: e
        for e in json.loads(MATRIX_PATH.read_text(encoding="utf-8")).get("entries") or []
    }
    rows: dict[str, dict[str, Any]] = {
        p["id"]: {
            "required_hit": 0,
            "required_total": 0,
            "tp_warnings": 0,
            "fp_warnings": 0,
            "cases": [],
            "harness": (matrix.get(p["id"]) or {}).get("harness"),
        }
        for p in catalog
    }

    def row(pid: str) -> dict[str, Any]:
        return rows.setdefault(
            pid,
            {"required_hit": 0, "required_total": 0, "tp_warnings": 0, "fp_warnings": 0,
             "cases": [], "harness": None},
        )

    for rep in case_reports:
        if rep.get("status") != "ok":
            continue
        cid = rep["case_id"]
        gold_items = load_gold(cid).get("items") or []
        by_id = {i["id"]: i for i in gold_items}
        for it in rep.get("items") or []:
            gi = by_id.get(it["id"]) or {}
            pid = gi.get("pattern_id")
            if not pid or it.get("severity") not in {"required", "required_when_corpus_present"}:
                continue
            r = row(pid)
            r["required_total"] += 1
            r["required_hit"] += int(bool(it.get("matched")))
            if cid not in r["cases"]:
                r["cases"].append(cid)
        wpath = out_dir / f"{cid}_warnings.json"
        if not wpath.is_file():
            continue
        active = [
            i for i in gold_items
            if i.get("severity", "required") in {"required", "required_when_corpus_present", "desirable"}
        ]
        for w in load_warnings(wpath):
            r = row(_pid(w))
            if any(warning_supports_item(i, w) for i in active):
                r["tp_warnings"] += 1
            else:
                r["fp_warnings"] += 1

    clean = (clean_summary or {}).get("summary", clean_summary or {})
    clean_fp = clean.get("unexplained_by_pattern") or {}
    out: dict[str, Any] = {}
    for pid, r in sorted(rows.items()):
        emitted = r["tp_warnings"] + r["fp_warnings"]
        out[pid] = {
            **r,
            "recall": round(r["required_hit"] / r["required_total"], 3) if r["required_total"] else None,
            "precision": round(r["tp_warnings"] / emitted, 3) if emitted else None,
            "clean_fp": int(clean_fp.get(pid, 0)),
        }
    gaps = sorted(pid for pid, r in out.items() if r["required_total"] == 0)
    return {
        "patterns": out,
        "no_gold_patterns": gaps,
        "clean_corpus_cases": clean.get("n_cases"),
    }


def format_dashboard(dash: dict[str, Any]) -> str:
    def f(x: float | None) -> str:
        return "  -  " if x is None else f"{x:5.2f}"

    lines = [f"{'pattern_id':<42} {'recall':>6} {'prec':>6} {'tp':>4} {'fp':>4} {'clean':>5}"]
    for pid, r in dash["patterns"].items():
        lines.append(
            f"{pid:<42} {f(r['recall']):>6} {f(r['precision']):>6} "
            f"{r['tp_warnings']:>4} {r['fp_warnings']:>4} {r['clean_fp']:>5}"
        )
    if dash.get("no_gold_patterns"):
        lines.append("no gold: " + ", ".join(dash["no_gold_patterns"]))
    return "\n".join(lines)


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
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every case with a committed gold_warnings.json and print the pattern dashboard",
    )
    args = parser.parse_args(argv)

    if args.all:
        cases = all_gold_cases()
        if "private_benchmark" not in cases and _case_available("private_benchmark"):
            cases.append("private_benchmark")
    else:
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
    clean = (
        json.loads(CLEAN_SUMMARY_PATH.read_text(encoding="utf-8"))
        if CLEAN_SUMMARY_PATH.is_file()
        else None
    )
    suite["pattern_dashboard"] = pattern_dashboard(reports, out_dir=out_dir, clean_summary=clean)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(suite["summary"], ensure_ascii=False, indent=2))
    print(format_dashboard(suite["pattern_dashboard"]))
    print(f"Wrote {args.output}")

    from pre_peer_checker.eval.history import append_history

    dash = suite["pattern_dashboard"]["patterns"]
    append_history(
        "pattern_dashboard",
        {
            "n_cases_ok": suite["summary"]["n_cases_ok"],
            "recall": {k: v["recall"] for k, v in dash.items() if v["recall"] is not None},
            "precision": {k: v["precision"] for k, v in dash.items() if v["precision"] is not None},
            "clean_fp": {k: v["clean_fp"] for k, v in dash.items() if v["clean_fp"]},
        },
        config={"cases": cases, "legend_llm": bool(args.legend_llm), "profile": llm_profile},
    )

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
