"""Retraction Watch CSV の Reason タグ集計と pattern_id マッピング."""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pre_peer_checker.catalog.paths import RW_REASON_MAP


@dataclass
class ReasonHit:
    reason: str
    count: int
    life_science_count: int
    pattern_ids: list[str] = field(default_factory=list)
    taxonomy: list[str] = field(default_factory=list)
    note: str | None = None


def load_reason_map(path: Path | None = None) -> dict[str, Any]:
    p = path or RW_REASON_MAP
    return json.loads(p.read_text(encoding="utf-8"))


def _split_semi(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in value.split(";") if p.strip()]


def is_life_science_subject(subject: str, prefixes: Iterable[str]) -> bool:
    s = subject or ""
    return any(s.startswith(pref) or pref in s for pref in prefixes)


def mine_reasons(
    csv_path: Path,
    reason_map: dict[str, Any] | None = None,
    *,
    life_only: bool = True,
) -> dict[str, Any]:
    """CSV を読み、Reason 頻度と pattern カバレッジを返す."""
    rm = reason_map or load_reason_map()
    prefixes = rm.get("life_science_subject_prefixes") or ["(BLS)", "(HSC)"]
    by_reason = {m["reason"]: m for m in rm.get("mappings", [])}

    total = Counter()
    life = Counter()
    rows_total = 0
    rows_life = 0

    with csv_path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows_total += 1
            subjects = _split_semi(row.get("Subject"))
            is_life = any(is_life_science_subject(s, prefixes) for s in subjects)
            if is_life:
                rows_life += 1
            for reason in _split_semi(row.get("Reason")):
                total[reason] += 1
                if is_life:
                    life[reason] += 1

    hits: list[ReasonHit] = []
    unmapped_life: list[dict[str, int | str]] = []
    for reason, count in total.most_common():
        life_n = life.get(reason, 0)
        if life_only and life_n == 0:
            continue
        meta = by_reason.get(reason)
        if meta:
            hits.append(
                ReasonHit(
                    reason=reason,
                    count=count,
                    life_science_count=life_n,
                    pattern_ids=list(meta.get("pattern_ids") or []),
                    taxonomy=list(meta.get("taxonomy") or []),
                    note=meta.get("note"),
                )
            )
        elif life_n > 0:
            unmapped_life.append(
                {"reason": reason, "life_science_count": life_n, "count": count}
            )

    mapped_pattern_ids = sorted(
        {pid for h in hits for pid in h.pattern_ids if pid}
    )
    return {
        "csv": str(csv_path),
        "rows_total": rows_total,
        "rows_life_science": rows_life,
        "unique_reasons": len(total),
        "hits": [
            {
                "reason": h.reason,
                "count": h.count,
                "life_science_count": h.life_science_count,
                "pattern_ids": h.pattern_ids,
                "taxonomy": h.taxonomy,
                "note": h.note,
            }
            for h in hits
        ],
        "unmapped_life_reasons": sorted(
            unmapped_life, key=lambda x: int(x["life_science_count"]), reverse=True
        ),
        "mapped_pattern_ids": mapped_pattern_ids,
    }
