"""ソース表の同一値・定数比指紋（P-SOURCE-DUPLICATE-VALUES / P-SOURCE-RATIO-ARTIFACT）。

読む＝ラベル／独立主張は後続。ここでは決定論の「比べる」のみ。
有罪断定はしない — Warning は要確認。
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.engine.n_and_names import is_plot_quant_table as _is_plot_quant
from pre_peer_checker.warnings import WarningItem, WarningTag


def _controlish(key: str) -> bool:
    k = key.lower()
    return any(t in k for t in ("ctrl", "control", "wt", "wild", "vehicle", "baseline"))


def find_within_table_duplicate_groups(
    vectors: list[GroupVector],
    *,
    min_n: int = 4,
    decimals: int = 6,
) -> list[tuple[GroupVector, GroupVector]]:
    """同一表内で別群ラベルなのに値が厳密一致するペア。"""
    by_src: dict[Path, list[GroupVector]] = defaultdict(list)
    for v in vectors:
        if v.n < min_n:
            continue
        by_src[v.source.resolve()].append(v)

    out: list[tuple[GroupVector, GroupVector]] = []
    for vs in by_src.values():
        for a, b in combinations(vs, 2):
            if a.group_key == b.group_key:
                continue
            if a.rounded(decimals) == b.rounded(decimals):
                out.append((a, b))
    return out


def _integer_ratio_k(
    a: tuple[float, ...],
    b: tuple[float, ...],
    *,
    max_k: int = 100,
    rel_tol: float = 1e-6,
    abs_tol: float = 1e-9,
) -> int | None:
    """If every paired value satisfies b≈k*a (or a≈k*b) for integer k≥2, return k."""
    if len(a) != len(b) or not a:
        return None
    # Prefer a as smaller-magnitude base
    sa, sb = a, b
    swapped = False
    if sum(abs(x) for x in b) < sum(abs(x) for x in a):
        sa, sb = b, a
        swapped = True

    ratios: list[float] = []
    for x, y in zip(sa, sb):
        if abs(x) < abs_tol:
            if abs(y) < abs_tol:
                continue  # 0→0 ok, skip
            return None
        ratios.append(y / x)
    if not ratios:
        return None
    # All ratios must agree
    med = sorted(ratios)[len(ratios) // 2]
    if med < 1.5:
        return None
    k = int(round(med))
    if k < 2 or k > max_k:
        return None
    for r in ratios:
        if abs(r - k) > max(abs_tol, rel_tol * abs(k)):
            return None
    return k


def find_within_table_ratio_artifacts(
    vectors: list[GroupVector],
    *,
    min_n: int = 4,
    decimals: int = 6,
) -> list[tuple[GroupVector, GroupVector, int]]:
    """同一表内で全要素が整数倍関係の別群ペア。"""
    by_src: dict[Path, list[GroupVector]] = defaultdict(list)
    for v in vectors:
        if v.n < min_n:
            continue
        by_src[v.source.resolve()].append(v)

    out: list[tuple[GroupVector, GroupVector, int]] = []
    for vs in by_src.values():
        for a, b in combinations(vs, 2):
            if a.group_key == b.group_key:
                continue
            if a.n != b.n:
                continue
            # Exact duplicates are G9, not ratio
            if a.rounded(decimals) == b.rounded(decimals):
                continue
            k = _integer_ratio_k(a.rounded(decimals), b.rounded(decimals))
            if k is not None:
                out.append((a, b, k))
    return out


def warnings_from_source_duplicates(
    vectors: list[GroupVector],
    *,
    min_n: int = 4,
    plot_only: bool = True,
) -> list[WarningItem]:
    """P-SOURCE-DUPLICATE-VALUES: 独立群ラベルなのにソース値が完全一致。"""
    pool = [v for v in vectors if (not plot_only) or _is_plot_quant(v.source)]
    out: list[WarningItem] = []
    seen: set[tuple[str, str, str, str]] = set()
    for a, b in find_within_table_duplicate_groups(pool, min_n=min_n):
        key = (
            str(a.source.resolve()),
            a.group_key,
            b.group_key,
            "dup",
        )
        if key in seen:
            continue
        seen.add(key)
        # Shared-control exact match is a different pattern; skip ctrl↔non-ctrl? No —
        # G9 targets copy-paste between *independent* condition labels (e.g. geneA vs geneB).
        # Skip only when both look like controls (legitimate shared ctrl column reuse).
        if _controlish(a.group_key) and _controlish(b.group_key):
            continue
        out.append(
            WarningItem(
                tag=WarningTag.STATS_MISMATCH,
                title="ソース表内で独立群の値が完全一致",
                location=f"{a.source.name}[{a.group_key}] ↔ [{b.group_key}]",
                reason=(
                    f"n={a.n}/{b.n} の群ベクトルが厳密一致。"
                    "独立標本のはずの値がコピーされていないか確認してください"
                    "（機器分解能・離散カウントのみでは断定しない）。"
                ),
                sources=[str(a.source)],
                metadata={
                    "pattern_id": "P-SOURCE-DUPLICATE-VALUES",
                    "group_a": a.group_key,
                    "group_b": b.group_key,
                    "n": a.n,
                },
            )
        )
    return out


def warnings_from_source_ratio_artifacts(
    vectors: list[GroupVector],
    *,
    min_n: int = 4,
    plot_only: bool = True,
) -> list[WarningItem]:
    """P-SOURCE-RATIO-ARTIFACT: 別条件なのに全行が整数 k 倍。"""
    pool = [v for v in vectors if (not plot_only) or _is_plot_quant(v.source)]
    out: list[WarningItem] = []
    for a, b, k in find_within_table_ratio_artifacts(pool, min_n=min_n):
        out.append(
            WarningItem(
                tag=WarningTag.STATS_MISMATCH,
                title=f"ソース表内で群値が正確に {k} 倍",
                location=f"{a.source.name}[{a.group_key}] ↔ [{b.group_key}]",
                reason=(
                    f"全要素が整数倍 k={k}。"
                    "スケール違いのコピーや比指紋の可能性を確認してください"
                    "（単発の 2 倍や ΔΔCt の正当な計算だけでは十分でない）。"
                ),
                sources=[str(a.source)],
                metadata={
                    "pattern_id": "P-SOURCE-RATIO-ARTIFACT",
                    "group_a": a.group_key,
                    "group_b": b.group_key,
                    "k": k,
                    "n": a.n,
                },
            )
        )
    return out
