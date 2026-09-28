"""3群以上 × 無補正対比較 t — P-STAT-MULTIPLICITY-GAP."""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.warnings import WarningItem, WarningTag

_UNCORRECTED_T = re.compile(
    r"(?:student'?s?\s*t[- ]?tests?|unpaired\s+t[- ]?tests?|two[- ]sample\s+t[- ]?tests?|"
    r"\bt[- ]?tests?\b(?!\s+with\s+correction))",
    re.I,
)
_CORRECTION = re.compile(
    r"(?:anova|tukey|dunnett|bonferroni|holm|sidak|šidák|fdr|benjamini|"
    r"multiple[- ]comparison|post[- ]hoc|多重比較)",
    re.I,
)
_PAIR_CLAIM = re.compile(
    r"(?P<a>[A-Za-z][A-Za-z0-9_+/-]{0,24})\s+(?:vs\.?|versus|compared\s+with|対)\s+"
    r"(?P<b>[A-Za-z][A-Za-z0-9_+/-]{0,24})",
    re.I,
)


def _group_keys_by_source(vectors: list[GroupVector], *, min_n: int = 2) -> dict[Path, set[str]]:
    by: dict[Path, set[str]] = defaultdict(set)
    for v in vectors:
        if v.n < min_n:
            continue
        by[v.source.resolve()].add(v.group_key)
    return by


def _group_counts_by_source(vectors: list[GroupVector], *, min_n: int = 2) -> dict[Path, int]:
    return {p: len(keys) for p, keys in _group_keys_by_source(vectors, min_n=min_n).items()}


def enumerate_pairwise_groups(group_keys: set[str] | list[str]) -> list[dict[str, str]]:
    """All unordered pairs among group labels (A–B, A–C, …)."""
    keys = sorted({str(k) for k in group_keys})
    return [{"a": a, "b": b} for a, b in combinations(keys, 2)]


def extract_claimed_pairs(texts: list[str]) -> list[dict[str, str]]:
    """Parse explicit 'A vs B' style comparison claims."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for t in texts:
        if not t:
            continue
        for m in _PAIR_CLAIM.finditer(str(t)):
            a, b = m.group("a"), m.group("b")
            if a.lower() in {"the", "and", "with", "from"} or b.lower() in {
                "the",
                "and",
                "with",
                "from",
            }:
                continue
            key = tuple(sorted((a, b), key=str.lower))
            if key in seen:
                continue
            seen.add(key)
            out.append({"a": key[0], "b": key[1], "span": m.group(0)})
    return out


def warnings_from_multiplicity_gap(
    texts: list[str],
    vectors: list[GroupVector],
    *,
    min_groups: int = 3,
) -> list[WarningItem]:
    """Emit when ≥3 groups coexist with uncorrected pairwise t claims.

    Metadata always includes enumerated pairs (group×group) and any text-claimed pairs.
    """
    blob = "\n".join(t for t in texts if t)
    if not blob.strip():
        return []
    if not _UNCORRECTED_T.search(blob):
        return []
    if _CORRECTION.search(blob):
        return []

    by_keys = _group_keys_by_source(vectors)
    claimed_pairs = extract_claimed_pairs(texts)
    offenders = [(p, keys) for p, keys in by_keys.items() if len(keys) >= min_groups]
    if not offenders:
        return []

    warnings: list[WarningItem] = []
    for path, keys in offenders:
        pairs = enumerate_pairwise_groups(keys)
        m = _UNCORRECTED_T.search(blob)
        span = m.group(0) if m else "t-test"
        n_groups = len(keys)
        warnings.append(
            WarningItem(
                tag=WarningTag.STAT_METHOD,
                title="多重比較補正なしの対比較 t（群数≥3）",
                location=f"{path.name} ({n_groups} groups, {len(pairs)} pairs)",
                reason=(
                    f"表に {n_groups} 群（対比較候補 {len(pairs)} 組）ある一方、"
                    f"Legend／Methods に「{span}」があり、"
                    "ANOVA／Tukey／Bonferroni 等の多重比較記載が見つかりません。"
                    + (
                        f" 本文の明示比較: "
                        + ", ".join(f"{p['a']} vs {p['b']}" for p in claimed_pairs[:6])
                        + "。"
                        if claimed_pairs
                        else ""
                    )
                ),
                sources=[str(path)],
                metadata={
                    "pattern_id": "P-STAT-MULTIPLICITY-GAP",
                    "n_groups": n_groups,
                    "test_span": span,
                    "pairs": pairs,
                    "claimed_pairs": claimed_pairs,
                    "groups": sorted(keys),
                },
            )
        )
    return warnings
