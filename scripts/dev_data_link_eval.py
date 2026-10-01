#!/usr/bin/env python3
"""End-to-end data-reading score: legend panel n ↔ linked data table n.

For each slot (``<root>/paper_XX`` with ``manuscript/`` and ``data/``):

1. Legend n per panel from the manuscript (rule extractor, no LLM).
2. Group vectors from every table under ``data/``.
3. ``build_n_matrix`` links each legend row to a data/plot table.

Published papers are assumed mostly self-consistent, so a linked row whose n
equals the legend n counts as a correct read; ``agree`` is the headline number.

    python scripts/dev_data_link_eval.py "input/data extract" --papers paper_03 paper_05
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def _vectors_for(path_str: str):
    import warnings

    warnings.simplefilter("ignore")
    from pre_peer_checker.data.group_vectors import extract_group_vectors

    try:
        return path_str, extract_group_vectors(Path(path_str)), None
    except Exception as exc:  # noqa: BLE001
        return path_str, [], f"{type(exc).__name__}: {exc}"[:200]


def _manuscript(slot: Path) -> Path | None:
    ms = slot / "manuscript"
    for pat in ("*.docx", "*.pdf"):
        found = sorted(p for p in ms.glob(pat) if not p.name.startswith((".", "~$")))
        if found:
            return found[0]
    return None


def eval_slot(slot: Path, *, workers: int) -> dict:
    from pre_peer_checker.engine.n_matrix import build_n_matrix
    from pre_peer_checker.io_bundle import FileKind, collect_inputs
    from pre_peer_checker.parsers.legend_struct import (
        all_panel_ns,
        extract_structured_legends_from_paragraphs,
    )
    from pre_peer_checker.parsers.manuscript_text import manuscript_paragraphs

    t0 = time.perf_counter()
    ms = _manuscript(slot)
    panel_ns = []
    if ms is not None:
        legends = extract_structured_legends_from_paragraphs(manuscript_paragraphs(ms))
        panel_ns = [pn for pn in all_panel_ns(legends) if pn.n_max is None]
    t_legend = time.perf_counter() - t0

    bundle = collect_inputs([slot / "data"], extract_zips=False)
    tables = bundle.get(FileKind.CSV) + bundle.get(FileKind.EXCEL)
    vectors = []
    errors = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for _p, vecs, err in ex.map(_vectors_for, [str(p) for p in tables]):
            vectors.extend(vecs)
            if err:
                errors.append(err)
    t_tables = time.perf_counter() - t0 - t_legend

    rows = build_n_matrix(panel_ns, vectors, table_paths=tables, case_roots=[slot])
    t_link = time.perf_counter() - t0 - t_legend - t_tables

    def _linked(cell) -> bool:
        return cell.link_status == "linked" and cell.n is not None

    n_rows = len(rows)
    linked = [r for r in rows if _linked(r.data) or _linked(r.plot)]
    agree = [
        r
        for r in linked
        if (r.manuscript.n is not None)
        and ((_linked(r.data) and r.data.n == r.manuscript.n)
             or (_linked(r.plot) and r.plot.n == r.manuscript.n))
    ]
    figs = {r.figure for r in rows}
    figs_linked = {r.figure for r in linked}
    status = Counter(r.data_link_status for r in rows)
    return {
        "slot": slot.name,
        "manuscript": ms.name if ms else None,
        "legend_rows": n_rows,
        "linked": len(linked),
        "agree": len(agree),
        "link_rate": round(len(linked) / n_rows, 3) if n_rows else None,
        "agree_rate": round(len(agree) / n_rows, 3) if n_rows else None,
        "agree_given_linked": round(len(agree) / len(linked), 3) if linked else None,
        "figures": len(figs),
        "figures_linked": len(figs_linked),
        "data_status": dict(status),
        "tables": len(tables),
        "vectors": len(vectors),
        "table_errors": len(errors),
        "seconds": {"legend": round(t_legend, 1), "tables": round(t_tables, 1),
                    "link": round(t_link, 1)},
        "rows": [
            {
                "figure": r.figure,
                "panel": r.panel,
                "group": r.group,
                "legend_n": r.manuscript.n,
                "data_n": r.data.n,
                "data_status": r.data.link_status,
                "data_file": r.data.file_display or r.data.file,
                "data_detail": r.data.detail,
                "plot_n": r.plot.n,
                "plot_status": r.plot.link_status,
                "plot_file": r.plot.file_display or r.plot.file,
            }
            for r in rows
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--papers", nargs="*")
    ap.add_argument("-o", "--out", type=Path, default=REPO / "outputs/data_read/link_eval.json")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 4))
    args = ap.parse_args()

    slots = sorted(p for p in args.root.iterdir() if (p / "data").is_dir())
    if args.papers:
        slots = [s for s in slots if s.name in set(args.papers)]
    report = []
    for slot in slots:
        res = eval_slot(slot, workers=args.workers)
        report.append(res)
        head = {k: v for k, v in res.items() if k != "rows"}
        print(json.dumps(head, ensure_ascii=False))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
