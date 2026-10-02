#!/usr/bin/env python3
"""Recompute every ``data.n`` of legend/data gold files straight from the tables.

Gold lives at ``fixtures/gold/data_extract/<slot>/legend_data_gold.json`` (see the
README there). Each item's data spec names a file, sheet, group column/value and
a count rule; this script re-reads the file and fails loudly on any drift, so the
gold is checked against the data rather than against the tool under test.

    python scripts/dev_legend_gold_check.py "input/data extract"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

GOLD_ROOT = REPO / "fixtures/gold/data_extract"


def _same(cell, value) -> bool:
    if cell is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return abs(float(cell) - float(value)) < 1e-9
        except (TypeError, ValueError):
            return False
    return str(cell).strip() == str(value).strip()


def _label(cell) -> str:
    if isinstance(cell, float):
        return f"{cell:g}"
    return "" if cell is None else str(cell).strip()


def count_from_spec(data_root: Path, spec: dict) -> int:
    from pre_peer_checker.data.curve_n import curve_cohort_size
    from pre_peer_checker.data.table_grid import read_grids

    grids = [g for g in read_grids(data_root / spec["file"]) if g.sheet == spec["sheet"]]
    if not grids:
        raise ValueError(f"sheet {spec['sheet']!r} not found")
    rows = [[v for _, v in r] for r in grids[0].rows]
    rule = spec.get("count", "rows")
    if rule.startswith("km_cohort"):
        col = spec["column"]
        hdr = next(i for i, r in enumerate(rows) if col in [_label(c) for c in r])
        j = [_label(c) for c in rows[hdr]].index(col)
        vals = [r[j] for r in rows[hdr + 1 :] if j < len(r) and isinstance(r[j], (int, float))]
        n = curve_cohort_size(vals)
        if n is None:
            raise ValueError("curve does not reconstruct")
        return n
    gcol = spec["group_col"]
    hdr = next(i for i, r in enumerate(rows) if gcol in [_label(c) for c in r])
    head = [_label(c) for c in rows[hdr]]
    gi = head.index(gcol)
    body = [r for r in rows[hdr + 1 :] if gi < len(r) and _same(r[gi], spec["group_value"])]
    if rule == "rows":
        return len(body)
    if rule.startswith("sum:"):
        si = head.index(rule[4:])
        return int(round(sum(float(r[si]) for r in body if r[si] is not None)))
    raise ValueError(f"unknown count rule {rule!r}")


def check_slot(gold_path: Path, data_root: Path) -> list[str]:
    gold = json.loads(gold_path.read_text())
    problems = []
    for it in gold["items"]:
        key = f"{it['figure']} {it['panel']} {it.get('group', '')}".strip()
        spec = it.get("data")
        if spec is None or spec.get("n") is None or spec.get("count") == "manual":
            continue
        try:
            got = count_from_spec(data_root, spec)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{key}: {type(exc).__name__}: {exc}")
            continue
        if got != spec["n"]:
            problems.append(f"{key}: gold n={spec['n']} but file gives {got}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="folder holding <slot>/data")
    args = ap.parse_args()
    bad = 0
    for gold_path in sorted(GOLD_ROOT.glob("*/legend_data_gold.json")):
        slot = gold_path.parent.name
        problems = check_slot(gold_path, args.root / slot / "data")
        n_items = len(json.loads(gold_path.read_text())["items"])
        print(f"{slot}: {n_items} items, {len(problems)} problems")
        for p in problems:
            print("   ", p)
        bad += len(problems)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
