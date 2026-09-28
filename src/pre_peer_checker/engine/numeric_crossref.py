"""本文・Legend ↔ 表／Fig 数値クロス参照 — P-NUMERIC-CROSSREF-MISMATCH."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.warnings import WarningItem, WarningTag

_MEAN_RE = re.compile(
    r"(?:(?:mean|average)\s*(?:intensity|value|fluorescence)?\s*(?:of\s+\w+\s+)?[=≈:]?\s*"
    r"|mean\s*[±\+\-]\s*)"
    r"(?P<val>\d+(?:\.\d+)?)",
    re.I,
)
_BARE_MEAN_RE = re.compile(
    r"\b(?:was|were|is|are)\s+(?P<val>\d+(?:\.\d+)?)\s*(?:±|\+/-)",
    re.I,
)


@dataclass
class NumericClaim:
    value: float
    span: str
    source_hint: str = "manuscript"


def extract_mean_claims(texts: list[str]) -> list[NumericClaim]:
    claims: list[NumericClaim] = []
    seen: set[tuple[float, str]] = set()
    for t in texts:
        if not t or not str(t).strip():
            continue
        blob = str(t)
        for rx in (_MEAN_RE, _BARE_MEAN_RE):
            for m in rx.finditer(blob):
                val = float(m.group("val"))
                span = m.group(0).strip()
                key = (round(val, 6), span.lower())
                if key in seen:
                    continue
                seen.add(key)
                claims.append(NumericClaim(value=val, span=span))
    return claims


def _group_means(vectors: list[GroupVector]) -> list[tuple[GroupVector, float]]:
    out: list[tuple[GroupVector, float]] = []
    for v in vectors:
        if v.n < 2 or not v.values:
            continue
        out.append((v, sum(v.values) / len(v.values)))
    return out


def warnings_from_numeric_crossref(
    texts: list[str],
    vectors: list[GroupVector],
    *,
    rel_tol: float = 0.08,
    abs_tol: float = 0.15,
    min_rel_gap: float = 0.12,
) -> list[WarningItem]:
    """Flag manuscript mean claims that disagree with table group means.

    Requires a clear gap (not mere rounding): best table mean must be outside
    tolerance AND relative gap ≥ min_rel_gap vs the claim.
    """
    claims = extract_mean_claims(texts)
    means = _group_means(vectors)
    if not claims or not means:
        return []

    warnings: list[WarningItem] = []
    used_claims: set[float] = set()
    for claim in claims:
        # Find nearest table mean
        best_v, best_mean, best_gap = None, None, float("inf")
        for v, mean in means:
            gap = abs(claim.value - mean)
            if gap < best_gap:
                best_gap, best_v, best_mean = gap, v, mean
        assert best_v is not None and best_mean is not None
        tol = max(abs_tol, abs(best_mean) * rel_tol)
        rel_gap = best_gap / max(abs(claim.value), abs(best_mean), 1e-9)
        if best_gap <= tol or rel_gap < min_rel_gap:
            continue
        if claim.value in used_claims:
            continue
        used_claims.add(claim.value)
        warnings.append(
            WarningItem(
                tag=WarningTag.STATS_MISMATCH,
                title="本文／Legend の数値と表の平均が不一致",
                location=f"{best_v.source.name}[{best_v.group_key}]",
                reason=(
                    f"記載「{claim.span}」→ {claim.value}；"
                    f"表グループ平均 ≈ {best_mean:.4g}（差 {best_gap:.4g}）。"
                    "転記ミスまたは別群参照の可能性。"
                ),
                sources=[str(best_v.source)],
                metadata={
                    "pattern_id": "P-NUMERIC-CROSSREF-MISMATCH",
                    "claim_value": claim.value,
                    "table_mean": best_mean,
                    "gap": best_gap,
                    "claim_span": claim.span,
                    "group_key": best_v.group_key,
                },
            )
        )
    return warnings
