"""原稿内参考文献メタ整合 — P-REF-MISSING-ENTRY / DUPLICATE-KEY / META-INCONSISTENT.

P-REF-ORPHAN-ENTRY は初期は coverage note のみ（Hard Warning にしない）。
"""

from __future__ import annotations

from collections import Counter

from pre_peer_checker.parsers.references import (
    ReferenceBundle,
    ReferenceEntry,
    normalize_title,
)
from pre_peer_checker.warnings import WarningItem, WarningTag


def _entry_index(entries: list[ReferenceEntry]) -> dict[str, list[ReferenceEntry]]:
    idx: dict[str, list[ReferenceEntry]] = {}
    for e in entries:
        idx.setdefault(e.key, []).append(e)
        if e.style == "author_year" and e.authors and e.year:
            first = e.authors.split(",")[0].split()[0].lower()
            alt = f"{first}_{e.year}"
            if alt != e.key:
                idx.setdefault(alt, []).append(e)
    return idx


def orphan_entry_keys(bundle: ReferenceBundle) -> list[str]:
    """Bibliography keys never cited in text (for coverage notes)."""
    cited: set[str] = set()
    for c in bundle.in_text:
        cited.update(c.keys)
    orphans: list[str] = []
    for e in bundle.entries:
        keys = {e.key}
        if e.authors and e.year:
            first = e.authors.split(",")[0].split()[0].lower()
            keys.add(f"{first}_{e.year}")
        if keys.isdisjoint(cited):
            orphans.append(e.key)
    return orphans


def warnings_from_ref_biblio(
    bundle: ReferenceBundle,
    *,
    max_warnings: int = 20,
    emit_orphans: bool = False,
) -> list[WarningItem]:
    """Deterministic bibliography integrity warnings."""
    if not bundle.entries and not bundle.in_text:
        return []

    warnings: list[WarningItem] = []
    key_counts = Counter(e.key for e in bundle.entries)
    for key, n in sorted(key_counts.items()):
        if n < 2:
            continue
        warnings.append(
            WarningItem(
                tag=WarningTag.REF_INCONSISTENCY,
                title=f"参考文献キー重複: {key}",
                location=f"References[{key}]",
                reason=f"References にキー {key} が {n} 件あります。",
                sources=[],
                metadata={
                    "pattern_id": "P-REF-DUPLICATE-KEY",
                    "key": key,
                    "count": n,
                },
            )
        )
        if len(warnings) >= max_warnings:
            return warnings

    import re

    _paren_year = re.compile(r"\(((?:19|20)\d{2})[a-z]?\)")
    for e in bundle.entries:
        paren_years = {int(y) for y in _paren_year.findall(e.raw or "")}
        if len(paren_years) >= 2:
            warnings.append(
                WarningItem(
                    tag=WarningTag.REF_INCONSISTENCY,
                    title=f"参考文献メタ自己矛盾: {e.key}",
                    location=f"References[{e.key}]",
                    reason=(
                        f"エントリ {e.key} に括弧付きの年が複数"
                        f"（{', '.join(str(x) for x in sorted(paren_years))}）あります。"
                    ),
                    sources=[],
                    metadata={
                        "pattern_id": "P-REF-META-INCONSISTENT",
                        "key": e.key,
                        "years": sorted(paren_years),
                        "doi": e.doi,
                    },
                )
            )
            if len(warnings) >= max_warnings:
                return warnings

        # DOI present but neither authors nor title could be parsed
        if e.doi and not (e.authors or e.title):
            warnings.append(
                WarningItem(
                    tag=WarningTag.REF_INCONSISTENCY,
                    title=f"参考文献メタ不足: {e.key}",
                    location=f"References[{e.key}]",
                    reason=f"DOI はあるが著者・タイトルが取れないエントリです（{e.doi}）。",
                    sources=[],
                    metadata={
                        "pattern_id": "P-REF-META-INCONSISTENT",
                        "key": e.key,
                        "doi": e.doi,
                    },
                )
            )
            if len(warnings) >= max_warnings:
                return warnings

    idx = _entry_index(bundle.entries)
    missing_seen: set[str] = set()
    for cite in bundle.in_text:
        for key in cite.keys:
            if key in idx:
                continue
            if key in missing_seen:
                continue
            missing_seen.add(key)
            warnings.append(
                WarningItem(
                    tag=WarningTag.REF_INCONSISTENCY,
                    title=f"本文引用に対応する参考文献が無い: {key}",
                    location=cite.span,
                    reason=(
                        f"本文の引用 {cite.span} にキー {key} がありますが、"
                        f"References に対応エントリがありません。"
                    ),
                    sources=[],
                    metadata={
                        "pattern_id": "P-REF-MISSING-ENTRY",
                        "key": key,
                        "span": cite.span,
                        "paragraph": cite.paragraph[:240],
                    },
                )
            )
            if len(warnings) >= max_warnings:
                return warnings

    if emit_orphans:
        for key in orphan_entry_keys(bundle):
            warnings.append(
                WarningItem(
                    tag=WarningTag.REF_INCONSISTENCY,
                    title=f"未引用の参考文献: {key}",
                    location=f"References[{key}]",
                    reason=f"References の {key} は本文で一度も引用されていません。",
                    sources=[],
                    metadata={
                        "pattern_id": "P-REF-ORPHAN-ENTRY",
                        "key": key,
                        "severity": "info",
                    },
                )
            )
            if len(warnings) >= max_warnings:
                break

    return warnings


def titles_similar(a: str | None, b: str | None, *, min_token_overlap: float = 0.6) -> bool:
    """Token Jaccard-ish overlap on normalized titles."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    return inter / min(len(ta), len(tb)) >= min_token_overlap
