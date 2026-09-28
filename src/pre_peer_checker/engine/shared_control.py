"""共有コントロール・部分集合・端点欠落の検知（P-SHARED-CONTROL-UNDISCLOSED）."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pre_peer_checker.data.group_vectors import (
    GroupVector,
    VectorMatch,
    compare_vector_pair,
    experiments_look_distinct,
    find_cross_table_matches,
)
from pre_peer_checker.warnings import WarningItem, WarningTag


Disclosure = Literal["explicit", "weak", "none"]


@dataclass
class ShareKind:
    """How two group vectors relate for shared-control patterns."""

    kind: str  # exact | subset | endpoint_dropout | high_jaccard
    match: VectorMatch


def is_endpoint_dropout(a: GroupVector, b: GroupVector, *, decimals: int = 6) -> bool:
    """True if the shorter sorted series equals the longer with only end values removed."""
    ra = list(a.rounded(decimals))
    rb = list(b.rounded(decimals))
    if len(ra) == len(rb) or min(len(ra), len(rb)) < 3:
        return False
    short, long = (ra, rb) if len(ra) < len(rb) else (rb, ra)
    # short must be multiset-contained in long
    if Counter(short) - Counter(long):
        return False
    n_remove = len(long) - len(short)
    if n_remove < 1:
        return False
    # Contiguous middle slice after trimming left/right ends only
    for lo in range(n_remove + 1):
        hi = lo + len(short)
        if hi > len(long):
            break
        if list(long[lo:hi]) == list(short):
            trimmed_left = lo
            trimmed_right = len(long) - hi
            if trimmed_left + trimmed_right == n_remove and (
                trimmed_left > 0 or trimmed_right > 0
            ):
                return True
    return False


def classify_share(a: GroupVector, b: GroupVector, *, decimals: int = 6) -> ShareKind | None:
    m = compare_vector_pair(a, b, decimals=decimals)
    if m.exact:
        return ShareKind("exact", m)
    if is_endpoint_dropout(a, b, decimals=decimals):
        return ShareKind("endpoint_dropout", m)
    if m.is_subset and min(a.n, b.n) >= 4:
        return ShareKind("subset", m)
    if m.jaccard >= 0.95 and min(a.n, b.n) >= 5:
        return ShareKind("high_jaccard", m)
    return None


def _looks_like_control(group_key: str) -> bool:
    k = group_key.lower()
    return any(
        t in k
        for t in ("ctrl", "control", "wt", "wild", "vehicle", "untreated", "parent")
    )


_EXPLICIT_CUES = (
    "shared control",
    "shared ctrl",
    "same control",
    "identical control",
    "controls are shared",
    "control is shared",
    "common control",
    "共用 control",
    "共有コントロール",
    "共通のコントロール",
    "コントロールは共通",
    "コントロールを共有",
    "同一のコントロール",
    "同じコントロール",
    "コントロール群は共通",
)

_WEAK_CUES = (
    "same wt",
    "same wild-type",
    "same wild type",
    "identical wt",
    "wt is shared",
    "control data are from",
    "control values from",
    "re-used control",
    "reused control",
    "shown again for comparison",
)


def shared_control_disclosure(texts: list[str]) -> Disclosure:
    """How strongly the manuscript discloses shared / reused controls."""
    blob = " ".join(texts).lower()
    if any(c in blob for c in _EXPLICIT_CUES):
        return "explicit"
    if any(c in blob for c in _WEAK_CUES):
        return "weak"
    return "none"


def manuscript_claims_shared_control(texts: list[str]) -> bool:
    """True when shared control is explicitly disclosed (full FP suppression)."""
    return shared_control_disclosure(texts) == "explicit"


def warnings_from_shared_controls(
    vectors: list[GroupVector],
    *,
    min_n: int = 4,
    manuscript_mentions_shared: bool = False,
    disclosure: Disclosure | None = None,
    independence_claimed: bool = False,
) -> list[WarningItem]:
    """Emit CONTROL_SHARE (or skip/demote when manuscript discloses sharing).

    ``manuscript_mentions_shared=True`` or ``disclosure="explicit"`` → suppress.
    ``disclosure="weak"`` → emit demoted (info-level) warnings for review.
    ``independence_claimed`` + subset/endpoint → ``P-VECTOR-SUBSET-UNDISCLOSED``.
    """
    if disclosure is None:
        disclosure = "explicit" if manuscript_mentions_shared else "none"
    if disclosure == "explicit":
        return []

    warnings: list[WarningItem] = []
    seen: set[tuple[str, str, str, str]] = set()

    candidates = find_cross_table_matches(vectors, min_n=min_n, jaccard_threshold=0.95)
    for m in candidates:
        kind = classify_share(m.a, m.b)
        if kind is None:
            continue
        if kind.kind == "exact" and m.a.source.name == m.b.source.name:
            continue  # identical filename copies
        key = tuple(
            sorted(
                (
                    str(m.a.source.resolve()),
                    m.a.group_key,
                    str(m.b.source.resolve()),
                    m.b.group_key,
                )
            )
        )
        key2 = (
            min(key[0], key[2]),
            min(key[1], key[3]) if key[0] == key[2] else (key[1] if key[0] < key[2] else key[3]),
            max(key[0], key[2]),
            max(key[1], key[3]) if key[0] == key[2] else (key[3] if key[0] < key[2] else key[1]),
        )
        if key2 in seen:
            continue
        seen.add(key2)

        distinct = experiments_look_distinct(m.a.source, m.b.source)
        controlish = _looks_like_control(m.a.group_key) or _looks_like_control(m.b.group_key)

        if kind.kind == "exact" and distinct and not controlish:
            # leave to existing DATA_SWAP path for cross-condition exact
            continue

        use_vector_subset = independence_claimed and kind.kind in {
            "subset",
            "endpoint_dropout",
        }

        if kind.kind == "exact" and distinct and controlish:
            tag = WarningTag.CONTROL_SHARE
            pattern = "P-SHARED-CONTROL-UNDISCLOSED"
            title = "別実験系でコントロール群ベクトルが完全一致"
        elif use_vector_subset:
            tag = WarningTag.CONTROL_SHARE
            pattern = "P-VECTOR-SUBSET-UNDISCLOSED"
            title = {
                "endpoint_dropout": "端点欠落の部分集合なのに独立実験と読める記述",
                "subset": "群ベクトルが部分集合なのに独立実験と読める記述",
            }[kind.kind]
        elif kind.kind in {"subset", "endpoint_dropout", "high_jaccard"} or not distinct:
            tag = WarningTag.CONTROL_SHARE
            pattern = "P-SHARED-CONTROL-UNDISCLOSED"
            title = {
                "exact": "複数ファイルで同一群ベクトル（コントロール共有の可能性）",
                "endpoint_dropout": "端点欠落の部分集合（共有コントロールの疑い）",
                "subset": "群ベクトルが部分集合関係（共有コントロールの疑い）",
                "high_jaccard": "群ベクトルが高重複（共有コントロールの疑い）",
            }[kind.kind]
        else:
            continue

        reason_bits = [
            f"n={m.a.n}/{m.b.n}",
            f"kind={kind.kind}",
            f"jaccard={m.jaccard:.3f}",
        ]
        if kind.kind == "endpoint_dropout":
            reason_bits.append("長い系列の端点のみを除くと短い系列と一致")
        if use_vector_subset:
            reason_bits.append("本文が独立実験を主張するのに共有／subset の明示なし")
        demoted = disclosure == "weak"
        if demoted:
            title = "【降格】" + title
            reason_bits.append(
                "本文に共有・再利用を示唆する弱い記載あり → 重大 Warning から降格。"
            )
        elif not use_vector_subset:
            reason_bits.append("独立実験と読める場合は共有の明示を確認してください。")

        warnings.append(
            WarningItem(
                tag=tag,
                title=title,
                location=f"{m.a.source.name}[{m.a.group_key}] ↔ {m.b.source.name}[{m.b.group_key}]",
                reason="; ".join(reason_bits),
                sources=[str(m.a.source), str(m.b.source)],
                metadata={
                    "pattern_id": pattern,
                    "share_kind": kind.kind,
                    "exact": m.exact,
                    "jaccard": m.jaccard,
                    "is_subset": m.is_subset,
                    "demoted": demoted,
                    "disclosure": disclosure,
                    "independence_claimed": independence_claimed,
                    "severity": "info" if demoted else "warning",
                },
            )
        )
    return warnings
