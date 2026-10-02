#!/usr/bin/env python3
"""End-to-end data-reading score: legend panel n ↔ linked data table n.

For each slot (``<root>/paper_XX`` with ``manuscript/`` and ``data/``):

1. Legend n per panel from the manuscript (rule extractor, no LLM).
2. Group vectors from every table under ``data/``.
3. ``build_n_matrix`` links each legend row to a data/plot table.

Without gold, a linked row whose n equals the legend n counts as a correct read
(``agree``). With ``fixtures/gold/data_extract/<slot>/legend_data_gold.json`` the
rows are scored against it instead: legend recall, link accuracy (file), data n
accuracy, abstention on panels without data, and legend-vs-data mismatch flags.
Survival-curve items (n printed in the figure) are scored on the Source Data
blocks directly.

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
# measure the generic reader: a local case profile tuned to one lab would leak into the score
os.environ.setdefault("PRE_PEER_CHECKER_CASE_PROFILE", "none")


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
    ranged = []
    if ms is not None:
        legends = extract_structured_legends_from_paragraphs(manuscript_paragraphs(ms))
        all_ns = all_panel_ns(legends)
        # ranges are not compared against data (as in the pipeline) but count for legend recall
        panel_ns = [pn for pn in all_ns if pn.n_max is None]
        ranged = [pn for pn in all_ns if pn.n_max is not None]
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
    from pre_peer_checker.engine.link_candidates import link_candidate_cards

    cards = link_candidate_cards(panel_ns, vectors, rows)
    card_by_key = {k: c for c in cards for k in c.keys}
    t_link = time.perf_counter() - t0 - t_legend - t_tables

    def _card(r) -> dict:
        c = card_by_key.get((r.figure, r.panel.upper(), r.group or ""))
        if c is None:
            return {}
        return {"card_kind": c.kind, "card_files": [str(x.source) for x in c.candidates]}

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
                "data_n_lower_bound": r.data.n_lower_bound,
                "data_conf": r.data.confidence,
                "plot_n": r.plot.n,
                "plot_status": r.plot.link_status,
                "plot_file": r.plot.file_display or r.plot.file,
                "plot_conf": r.plot.confidence,
                "stats_n": r.stats.n if r.stats.file and not r.input_gap else None,
                "stats_conf": r.stats.confidence,
                "mismatch": r.mismatch,
                **_card(r),
            }
            for r in rows
        ]
        + [
            {
                "figure": pn.figure,
                "panel": pn.panel,
                "group": pn.group,
                "legend_n": pn.n,
                "legend_n_max": pn.n_max,
                "data_n": None,
                "data_status": "range_not_compared",
                "data_file": None,
                "mismatch": False,
            }
            for pn in ranged
        ],
    }


GOLD_ROOT = REPO / "fixtures/gold/data_extract"


def _fig_key(fig: str) -> tuple[bool, str]:
    import re

    m = re.search(r"(extended|supplementary)?.*?(S?\d+)", fig, re.IGNORECASE)
    return (bool(m and m.group(1)), m.group(2) if m else fig)


def _norm(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9+]", "", (s or "").lower())


def _gold_ns(spec: dict | None, group: str) -> set[int]:
    if not spec or spec.get("n") is None:
        return set()
    n = spec["n"]
    if isinstance(n, dict):
        hit = [v for k, v in n.items() if _norm(k) == _norm(group)] if group else []
        return set(hit or n.values())
    return {int(n)}


def _legend_ok(item: dict, row: dict) -> bool:
    n, n_max = row["legend_n"], row.get("legend_n_max")
    if n is None:
        return False
    if "legend_n" in item:
        return n == item["legend_n"] and n_max is None
    lo, hi = item.get("legend_n_min"), item.get("legend_n_max")
    # ``n ≥ 2`` (no upper bound) is read as its minimum
    return n == lo and (n_max == hi or hi is None)


def _find_row(item: dict, rows: list[dict]) -> dict | None:
    keys = [(item["panel"], item.get("group", ""))] + [tuple(a) for a in item.get("extract_aliases", [])]
    fk = _fig_key(item["figure"])
    same_fig = [r for r in rows if _fig_key(r["figure"]) == fk]
    for panel, group in keys:
        cands = [r for r in same_fig if r["panel"].upper() == panel.upper()]
        exact = [r for r in cands if _norm(r["group"]) == _norm(group)]
        if exact:
            return exact[0]
        if not group and len(cands) == 1:
            return cands[0]
    return None


def score_with_gold(slot: Path, rows: list[dict]) -> dict | None:
    gold_path = GOLD_ROOT / slot.name / "legend_data_gold.json"
    if not gold_path.is_file():
        return None
    gold = json.loads(gold_path.read_text())
    items = [it for it in gold["items"] if it.get("n_source", "legend") == "legend"]
    c = Counter()
    misses = []
    for it in items:
        row = _find_row(it, rows)
        key = f"{it['figure']} {it['panel']} {it.get('group', '')}".strip()
        c["legend_items"] += 1
        if row is None:
            misses.append(f"legend_missed · {key}")
            continue
        c["legend_found"] += 1
        legend_ok = _legend_ok(it, row)
        c["legend_n_ok"] += legend_ok
        if not legend_ok:
            got = f"{row['legend_n']}" + (f"–{row['legend_n_max']}" if row.get("legend_n_max") else "")
            want = it.get("legend_n", f"{it.get('legend_n_min')}–{it.get('legend_n_max')}")
            misses.append(f"legend_wrong_n · {key} gold={want} got={got}")
        spec = it.get("data")
        if row["data_status"] == "range_not_compared":
            # ranges / lower bounds are not sent to the linker
            c["range_rows"] += 1
            continue
        linked = row["data_status"] == "linked" and row["data_n"] is not None
        if spec is None:
            c["no_data_items"] += 1
            if linked:
                misses.append(f"spurious_link · {key} → {row['data_file']} n={row['data_n']}")
            else:
                c["abstain_ok"] += 1
        else:
            c["data_items"] += 1
            files = [spec["file"], *spec.get("alt_files", [])]
            f_ok = linked and any((row["data_file"] or "").endswith(f) for f in files)
            n_ok = linked and row["data_n"] in _gold_ns(spec, it.get("group", ""))
            c["link_ok"] += f_ok
            c["data_n_ok"] += n_ok
            if not n_ok:
                misses.append(
                    f"{'wrong_file' if linked and not f_ok else 'wrong_n' if linked else 'unlinked'}"
                    f" · {key} gold={spec['file']} n={spec['n']} · got={row['data_file']} n={row['data_n']}"
                )
        verdict = it["verdict"]
        if verdict in {"match", "mismatch", "data_missing", "not_comparable"}:
            truth = verdict == "mismatch"
            pred = bool(row["mismatch"])
            c[("tp" if truth else "fp") if pred else ("fn" if truth else "tn")] += 1
            # info cards: a missed mismatch whose card lists the gold table is recovered
            if row.get("card_kind"):
                c["cards"] += 1
                gold_files = [spec["file"], *spec.get("alt_files", [])] if spec else []
                on_gold = any(cf.endswith(f) for cf in row["card_files"] for f in gold_files)
                if truth and not pred:
                    c["card_recovered"] += on_gold
                    if not on_gold:
                        misses.append(f"card_wrong_file · {key} · {row['card_files']}")
                elif not truth and not on_gold:
                    c["card_on_non_mismatch"] += 1
                    misses.append(f"card_noise · {key} ({verdict}) · {row['card_files']}")
    curve = score_curves(slot, gold)
    out = dict(c)
    out["flag_precision"] = round(c["tp"] / (c["tp"] + c["fp"]), 3) if c["tp"] + c["fp"] else None
    out["flag_recall"] = round(c["tp"] / (c["tp"] + c["fn"]), 3) if c["tp"] + c["fn"] else None
    if curve:
        out["curves"] = curve
    out["misses"] = misses
    return out


def score_curves(slot: Path, gold: dict) -> dict | None:
    """Survival-curve n (printed in the figure) vs n reconstructed from Source Data."""
    from pre_peer_checker.data.source_data_blocks import parse_source_data_bundle

    items = [it for it in gold["items"] if it.get("n_source") == "figure"]
    if not items:
        return None
    blocks = parse_source_data_bundle(sorted((slot / "data").rglob("*.xls*")))
    c = Counter()
    for it in items:
        ext, num = _fig_key(it["figure"])
        hits = [
            b for b in blocks
            if b.figure is not None and b.figure.number == num and b.figure.extended == ext
            and it["panel"].lower() in b.all_panels and b.curve_ns is not None
        ]
        c["items"] += 1
        got = next((b.group_n(it["group"]) for b in hits if b.group_n(it["group"])), None)
        if got is None:
            continue
        n, exact = got
        c["reconstructed"] += 1
        c["exact"] += n == it["legend_n"]
        c["consistent"] += (n == it["legend_n"]) if exact else (n <= it["legend_n"])
    return dict(c)


def pooled(report: list[dict]) -> dict:
    """Micro-averaged gold scores over every slot (one number per run to compare versions)."""
    c = Counter()
    for res in report:
        for k, v in (res.get("gold") or {}).items():
            if isinstance(v, int) and not isinstance(v, bool):
                c[k] += v
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)

    def r(a: str, b: str) -> float | None:
        return round(c[a] / c[b], 3) if c[b] else None

    return {
        "slots": sum(1 for res in report if res.get("gold")),
        "legend_recall": r("legend_found", "legend_items"),
        "legend_n_acc": r("legend_n_ok", "legend_items"),
        "link_acc": r("link_ok", "data_items"),
        "data_n_acc": r("data_n_ok", "data_items"),
        "abstain_acc": r("abstain_ok", "no_data_items"),
        "flag_tp": tp,
        "flag_fp": fp,
        "flag_fn": fn,
        "flag_precision": round(prec, 3) if prec is not None else None,
        "flag_recall": round(rec, 3) if rec is not None else None,
        "flag_f1": round(f1, 3) if f1 is not None else None,
        "cards": c["cards"],
        "card_recovered": c["card_recovered"],
        "card_on_non_mismatch": c["card_on_non_mismatch"],
    }


def remismatch(row: dict, threshold: float) -> bool:
    """Recompute the n mismatch flag at another link-confidence threshold (mirrors build_n_matrix)."""
    legend_n = row.get("legend_n")
    ns = [legend_n] if legend_n is not None else []
    for pre in ("data", "plot", "stats"):
        n = row.get(f"{pre}_n")
        if n is None or (pre != "stats" and row.get(f"{pre}_status") != "linked"):
            continue
        if float(row.get(f"{pre}_conf") or 0.0) < threshold:
            continue
        if pre == "data" and row.get("data_n_lower_bound") and legend_n is not None and legend_n >= n:
            continue
        ns.append(n)
    return len(set(ns)) > 1


def rescore(paths: list[Path], root: Path, sweep: list[float] | None = None) -> None:
    """Score saved runs against the *current* gold so older versions are comparable."""
    for path in paths:
        report = json.loads(path.read_text())
        for res in report:
            gold = score_with_gold(root / res["slot"], res["rows"])
            if gold is not None:
                res["gold"] = gold
        print(f"{path.name:<22}", json.dumps(pooled(report), ensure_ascii=False))
        has_conf = any("data_conf" in r for res in report for r in res["rows"])
        for t in sweep or []:
            if not has_conf:
                print("  (no per-cell confidence in this run; sweep skipped)")
                break
            swept = []
            for res in report:
                rows = [
                    {**r, "mismatch": remismatch(r, t)} if r.get("data_status") != "range_not_compared" else r
                    for r in res["rows"]
                ]
                gold = score_with_gold(root / res["slot"], rows)
                swept.append({**res, "gold": gold} if gold is not None else res)
            p = pooled(swept)
            keys = ("flag_tp", "flag_fp", "flag_fn", "flag_precision", "flag_recall", "flag_f1")
            print(f"  conf>={t:<5}", json.dumps({k: p[k] for k in keys}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--papers", nargs="*")
    ap.add_argument("-o", "--out", type=Path, default=REPO / "outputs/data_read/link_eval.json")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 4))
    ap.add_argument("--rescore", type=Path, nargs="+", help="re-score saved link_eval JSONs only")
    ap.add_argument(
        "--sweep", type=float, nargs="*", help="with --rescore: flag scores at these link-confidence thresholds"
    )
    args = ap.parse_args()

    if args.rescore:
        rescore(args.rescore, args.root, args.sweep)
        return

    slots = sorted(p for p in args.root.iterdir() if (p / "data").is_dir())
    if args.papers:
        slots = [s for s in slots if s.name in set(args.papers)]
    report = []
    for slot in slots:
        res = eval_slot(slot, workers=args.workers)
        gold = score_with_gold(slot, res["rows"])
        if gold is not None:
            res["gold"] = gold
        report.append(res)
        head = {k: v for k, v in res.items() if k not in {"rows", "gold"}}
        print(json.dumps(head, ensure_ascii=False))
        if gold is not None:
            print("  gold:", json.dumps({k: v for k, v in gold.items() if k != "misses"}, ensure_ascii=False))
            for m in gold["misses"]:
                print("    ", m)
    summary = pooled(report)
    print("pooled:", json.dumps(summary, ensure_ascii=False))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"wrote {args.out}")

    from pre_peer_checker.eval.history import append_history

    append_history("data_link", summary, config={"papers": [s.name for s in slots]})


if __name__ == "__main__":
    main()
