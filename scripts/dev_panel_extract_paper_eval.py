#!/usr/bin/env python3
"""7B (+ rules-lock) panel extract eval for thin paper PDF cases.

Uses ``pdftotext -raw`` captions (column order) so two-column interleaving
does not mix neighboring panels' N values.

Example:
  python scripts/dev_panel_extract_paper_eval.py \\
    --case paper_01 --case paper_02 \\
    --profile qwen2.5-7b-hf \\
    -o outputs/metrics/panel_extract_paper_7b_ruleslock.json

Prints individual misses, so it only accepts dev cases. holdout cases and
ablations go through scripts/dev_generalization_eval.py.
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
from pre_peer_checker.eval import panel_extract_score as ev
from pre_peer_checker.llm.backend import select_backend
from pre_peer_checker.llm.legend_extract import extract_check_items_from_chunk
from pre_peer_checker.parsers.figure_chunks import FigureChunk
from pre_peer_checker.parsers.legend_struct import (
    extract_figure_captions_from_pdf,
    parse_panel_ns,
)


def _dev_case(case_id: str) -> gz.Case:
    case = gz.discover_cases(case_ids=[case_id])[0]
    if case.split != "dev":
        raise SystemExit(
            f"{case_id} is holdout; use scripts/dev_generalization_eval.py (aggregates only)"
        )
    return case


def _case_pdf(case: gz.Case) -> Path:
    src = case.legend_source()
    if src is None or src[0] != "pdf":
        raise SystemExit(f"{case.case_id}: expected one PDF under {case.input_dir}/")
    return src[1]


def _style_bucket(item: dict) -> str:
    group = (item.get("group") or "").strip()
    diff = item.get("difficulty") or ""
    if diff == "multi_group_timepoint" or group.lower().startswith("day"):
        return "timepoint_list"
    if diff == "shared_n":
        return "shared_n"
    if group:
        return "postfix_n_or_named"
    return "panel_level_n"


def run_case(
    case: str,
    *,
    profile: str,
    prefer: str,
    rules_only: bool,
    max_tokens: int,
) -> dict:
    c = _dev_case(case)
    pdf = _case_pdf(c)
    gold = c.load_gold("legend")
    if gold is None:
        raise SystemExit(f"{case}: missing {c.gold_path('legend')}")
    pairs = extract_figure_captions_from_pdf(pdf, keep=c.figures_in_scope)
    if not pairs:
        raise SystemExit(f"{case}: no captions from {pdf}")

    gen = None
    backend_info = None
    if not rules_only:
        backend = select_backend(prefer, profile_id=profile)
        if backend is None:
            raise SystemExit(f"no backend for {profile}")
        backend_info = backend.info()
        print(f"{case}: backend {backend_info}", flush=True)

        def gen(prompt: str) -> str:
            return backend.generate(prompt, max_tokens=max_tokens)

    rows: list[dict] = []
    for fig, legend in pairs:
        print(f"{case}: extract {fig} chars={len(legend)}", flush=True)
        if rules_only:
            for pn in parse_panel_ns(fig, legend):
                rows.append(
                    {
                        "figure": fig,
                        "panel": pn.panel,
                        "group": pn.group or "",
                        "manuscript": {"n": pn.n, "detail": (pn.context or "")[:180]},
                        "extractor": "rules",
                    }
                )
            continue
        ch = FigureChunk(
            figure_id=fig, figure_num=fig.split()[-1], legend=legend
        )
        item = extract_check_items_from_chunk(
            ch,
            llm_generate=gen,
            prefer_llm=True,
            legend_only_prompt=True,
        )
        print(
            f"  extractor={item.extractor} panels={len(item.panels)}",
            flush=True,
        )
        for p in item.panels:
            if p.n is None:
                continue
            rows.append(
                {
                    "figure": item.figure or fig,
                    "panel": p.panel,
                    "group": (p.groups[0] if p.groups else ""),
                    "manuscript": {
                        "n": p.n,
                        "detail": (p.evidence_span or "")[:180],
                    },
                    "extractor": item.extractor,
                }
            )

    preds = [
        {
            "figure": r["figure"],
            "panel": str(r["panel"]).upper(),
            "group": r.get("group") or "",
            "n": (r.get("manuscript") or {}).get("n"),
        }
        for r in rows
    ]
    sc = ev.score_one(gold, preds)
    by_style: dict[str, dict[str, int]] = {}
    id_to_gold = {i.get("id"): i for i in (gold.get("items") or [])}
    for detail in sc["items"]:
        gitem = id_to_gold.get(detail.get("id")) or {}
        bucket = _style_bucket(gitem)
        slot = by_style.setdefault(bucket, {"hit": 0, "n": 0})
        slot["n"] += 1
        if detail["matched"]:
            slot["hit"] += 1

    warnings_path = (
        ROOT
        / "outputs/metrics"
        / f"{case}_7b_{'rules_only' if rules_only else 'ruleslock'}_warnings.json"
    )
    warnings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_coverage": {"n_matrix": rows},
        "artifacts": {
            "legend_llm_status": {
                "status": "rules_only" if rules_only else "active",
                "backends": (
                    []
                    if rules_only
                    else [f"{backend_info.name} ({backend_info.model_id})"]
                ),
                "caption_source": "pdftotext -raw",
            }
        },
    }
    warnings_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{ev.format_score(case, sc)} -> {warnings_path}", flush=True)
    return {
        "case": case,
        "warnings": str(warnings_path),
        "backend": None if rules_only else str(backend_info),
        "n_captions": len(pairs),
        "by_style": by_style,
        **{
            k: sc[k]
            for k in (
                "n_items",
                "n_hit",
                "recall",
                "n_pred",
                "n_pred_hit",
                "precision",
                "f1",
                "forbid_panel_fp",
            )
        },
        "misses": [
            {"id": d["id"], "gold": d["gold"], "difficulty": d.get("difficulty")}
            for d in sc["items"]
            if not d["matched"]
        ],
        "false_positives": sc["false_positives"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", action="append", required=True, help="dev case id (repeatable)")
    ap.add_argument("--profile", default="qwen2.5-7b-hf")
    ap.add_argument("--prefer", default="cuda", help="LLM backend: auto | mlx | cuda")
    ap.add_argument(
        "--rules-only",
        action="store_true",
        help="Skip LLM; score published-style rules alone",
    )
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=ROOT / "outputs/metrics/panel_extract_paper_7b_ruleslock.json",
    )
    args = ap.parse_args(argv)

    runs = [
        run_case(
            c,
            profile=args.profile,
            prefer=args.prefer,
            rules_only=args.rules_only,
            max_tokens=args.max_tokens,
        )
        for c in args.case
    ]
    out = {"profile": args.profile, "rules_only": args.rules_only, "runs": runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
