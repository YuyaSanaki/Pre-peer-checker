#!/usr/bin/env python3
"""Panel box accuracy on dev figures (IoU against ``panel_boxes_gold.json``).

  predict     run the product panel regions (vector + raster OCR) and save them
  score       score saved predictions against the box gold (fast; rerun after gold edits)
  draft-gold  write a draft gold from saved predictions for a human to correct (dev only)

Examples:
  python scripts/dev_panel_box_eval.py predict -o tmp/eval/panel_boxes_pred.json
  python scripts/dev_panel_box_eval.py score --preds tmp/eval/panel_boxes_pred.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pre_peer_checker.eval import panel_boxes as pb
from pre_peer_checker.eval.generalization import discover_cases


def _cases(args):
    return [c for c in discover_cases(split="dev", case_ids=args.case or None) if c.figure_files()]


def cmd_predict(args) -> int:
    only = set(args.figure or []) or None
    preds = {}
    for c in _cases(args):
        print(f"[predict] {c.case_id}", flush=True)
        preds[c.case_id] = pb.predict_case(c, only=only)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.merge and out.is_file():
        old = json.loads(out.read_text(encoding="utf-8"))
        for cid, figs in preds.items():
            old.setdefault(cid, {}).update(figs)
        preds = old
    out.write_text(json.dumps(preds, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def cmd_score(args) -> int:
    preds = json.loads(Path(args.preds).read_text(encoding="utf-8"))
    scores = []
    for c in _cases(args):
        if pb.load_gold(c) is None:
            continue
        sc = pb.score_case(c, preds.get(c.case_id, {}))
        scores.append(sc)
        print(
            f"  {c.case_id:<12} hit@{pb.HIT_IOU}={sc['n_hit']}/{sc['n_gold']}"
            f" mean_iou={sc['mean_iou'] or 0:.3f} precision={sc['precision'] or 0:.3f}"
        )
        if args.verbose:
            for it in sc["items"]:
                if it["low"] or it["extra"]:
                    print(f"      {it['figure']}: low={it['low']} extra={it['extra']}")
    agg = pb.aggregate(scores)
    print(
        f"  {'ALL':<12} hit@{pb.HIT_IOU}={agg['n_hit']}/{agg['n_gold']}"
        f" mean_iou={agg['mean_iou'] or 0:.3f} precision={agg['precision'] or 0:.3f}"
    )
    return 0


def cmd_draft(args) -> int:
    preds = json.loads(Path(args.preds).read_text(encoding="utf-8"))
    for c in _cases(args):
        p = pb.gold_path(c)
        if p.exists() and not args.force:
            print(f"skip {c.case_id}: {p} exists")
            continue
        p.write_text(json.dumps(pb.draft_gold(c, preds.get(c.case_id, {})), indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {p}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--case", action="append")
    p.add_argument("--figure", action="append", help="figure key (1, ED3, ...); repeatable")
    p.add_argument("--merge", action="store_true", help="update figures in an existing output")
    p.set_defaults(fn=cmd_predict)
    s = sub.add_parser("score")
    s.add_argument("--preds", required=True)
    s.add_argument("--case", action="append")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_score)
    d = sub.add_parser("draft-gold")
    d.add_argument("--preds", required=True)
    d.add_argument("--case", action="append")
    d.add_argument("--force", action="store_true")
    d.set_defaults(fn=cmd_draft)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
