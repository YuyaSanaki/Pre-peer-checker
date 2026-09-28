#!/usr/bin/env python3
"""H3 実コーパス適合率を計測して JSON に記録する.

::

    # 合成のみ（高速・CI 相当）
    python scripts/dev_h3_corpus_eval.py --synthetic-only

    # 合成 + check_reference PubPeer PDF を cache/past_papers に取込して計測
    python scripts/dev_h3_corpus_eval.py

レポート: outputs/metrics/h3_corpus_report.json（git 外想定）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pre_peer_checker.eval.h3_corpus_eval import (  # noqa: E402
    build_h3_report,
    evaluate_synthetic_baseline,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--synthetic-only",
        action="store_true",
        help="skip check_reference PDF ingest / private_benchmark real corpus",
    )
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "metrics",
    )
    p.add_argument(
        "--library-root",
        type=Path,
        default=None,
        help="override cache/past_papers",
    )
    p.add_argument(
        "--heavy",
        action="store_true",
        help="also run full private_benchmark gold_eval with corpus (slow)",
    )
    args = p.parse_args(argv)

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    if args.synthetic_only:
        syn = evaluate_synthetic_baseline(out_dir=out / "h3_synthetic")
        report = {
            "schema_version": "1.0",
            "mode": "synthetic_only",
            "synthetic": syn,
            "summary": {
                "synthetic_reuse_required_recall": syn["image_reuse"].get(
                    "required_recall"
                ),
                "synthetic_partial_required_recall": syn["image_partial"].get(
                    "required_recall"
                ),
                "citation_suppression_ok": syn["citation_suppression"].get("ok"),
            },
        }
        path = out / "h3_corpus_report.json"
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report["report_path"] = str(path)
    else:
        report = build_h3_report(
            out_dir=out,
            ingest=True,
            library_root=args.library_root,
            heavy_gold=args.heavy,
            reuse_library=True,
        )

    summary = report.get("summary") or {}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {report.get('report_path')}")

    # Soft gate: synthetic must stay green; real corpus is measurement-only
    syn_ok = (
        summary.get("synthetic_reuse_required_recall") == 1.0
        and summary.get("synthetic_partial_required_recall") == 1.0
        and summary.get("citation_suppression_ok") is True
    )
    return 0 if syn_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
