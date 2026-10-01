#!/usr/bin/env python3
"""Inventory how well the data stage reads each paper's ``data/`` folder.

For every slot (``<root>/paper_XX/data``) report:

* files collected per ``FileKind`` and skipped extensions (count / bytes)
* per table file: load ok/error, seconds, group vectors, Source Data blocks

    python scripts/dev_data_read_audit.py "input/data extract" -o outputs/data_read/audit.json
    python scripts/dev_data_read_audit.py "input/data extract" --papers paper_03 paper_05
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pre_peer_checker.io_bundle import FileKind, classify, collect_inputs, is_office_temp_name  # noqa: E402


def _ext(p: Path) -> str:
    name = p.name.lower()
    if name.endswith(".gz") and "." in name[:-3]:
        return "." + ".".join(name.split(".")[-2:])
    return p.suffix.lower() or "(none)"


def _read_one(path_str: str) -> dict:
    from pre_peer_checker.data.group_vectors import extract_group_vectors
    from pre_peer_checker.data.source_data_blocks import parse_source_data_blocks
    from pre_peer_checker.data.stats_recalc import analyze_table_file

    p = Path(path_str)
    rec: dict = {"path": path_str, "bytes": p.stat().st_size}
    t0 = time.perf_counter()
    try:
        vecs = extract_group_vectors(p)
        rec["vectors"] = [{"group": v.group_key, "n": v.n} for v in vecs]
        st = analyze_table_file(p)
        rec["value_col"] = str(st.value_col)
        rec["group_col"] = None if st.group_col is None else str(st.group_col)
        rec["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
    try:
        blocks = parse_source_data_blocks(p)
        rec["source_blocks"] = len(blocks)
        rec["source_blocks_with_figure"] = sum(1 for b in blocks if b.figure is not None)
    except Exception as exc:  # noqa: BLE001
        rec["source_blocks"] = 0
        rec["source_blocks_error"] = str(exc)[:200]
    rec["seconds"] = round(time.perf_counter() - t0, 2)
    return rec


def audit_slot(slot: Path, *, workers: int, timeout: float) -> dict:
    data = slot / "data"
    skipped: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for dirpath, dirnames, filenames in os.walk(data):
        dirnames[:] = [d for d in dirnames if not is_office_temp_name(d)]
        for fn in filenames:
            if is_office_temp_name(fn):
                continue
            p = Path(dirpath) / fn
            if classify(p) is FileKind.UNKNOWN:
                s = skipped[_ext(p)]
                s[0] += 1
                try:
                    s[1] += p.stat().st_size
                except OSError:
                    pass
    bundle = collect_inputs([data], extract_zips=False)
    kinds = {k.value: len(v) for k, v in bundle.files.items()}
    tables = [str(p) for p in bundle.get(FileKind.CSV) + bundle.get(FileKind.EXCEL)]

    records: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {path: ex.submit(_read_one, path) for path in tables}
        for path, fut in futs.items():
            try:
                records.append(fut.result(timeout=timeout))
            except FutTimeout:
                records.append({"path": path, "status": "timeout"})
            except Exception as exc:  # noqa: BLE001
                records.append({"path": path, "status": "crash", "error": str(exc)[:300]})

    status = Counter(r["status"] for r in records)
    errors = Counter(
        (r.get("error") or "").split(":")[0] + ": " + (r.get("error") or "")[:80]
        for r in records
        if r["status"] != "ok"
    )
    return {
        "slot": slot.name,
        "kinds": kinds,
        "skipped_ext": {
            k: {"files": v[0], "mb": round(v[1] / 1e6, 1)}
            for k, v in sorted(skipped.items(), key=lambda kv: -kv[1][0])
        },
        "tables_n": len(tables),
        "table_status": dict(status),
        "error_kinds": dict(errors.most_common(15)),
        "vectors_n": sum(len(r.get("vectors") or []) for r in records),
        "source_blocks_n": sum(r.get("source_blocks") or 0 for r in records),
        "seconds_total": round(sum(r.get("seconds") or 0 for r in records), 1),
        "slowest": sorted(
            ({"path": r["path"], "s": r.get("seconds")} for r in records if r.get("seconds")),
            key=lambda d: -d["s"],
        )[:5],
        "tables": records,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--papers", nargs="*")
    ap.add_argument("-o", "--out", type=Path, default=REPO / "outputs/data_read/audit.json")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 4))
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    slots = sorted(p for p in args.root.iterdir() if (p / "data").is_dir())
    if args.papers:
        slots = [s for s in slots if s.name in set(args.papers)]
    report = []
    for slot in slots:
        t0 = time.perf_counter()
        res = audit_slot(slot, workers=args.workers, timeout=args.timeout)
        res["wall_s"] = round(time.perf_counter() - t0, 1)
        report.append(res)
        summary = {k: res[k] for k in ("kinds", "tables_n", "table_status", "vectors_n",
                                        "source_blocks_n", "wall_s")}
        print(f"== {slot.name}: {json.dumps(summary, ensure_ascii=False)}")
        for k, v in list(res["skipped_ext"].items())[:10]:
            print(f"   skipped {k}: {v}")
        for k, v in res["error_kinds"].items():
            print(f"   err x{v}: {k}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
