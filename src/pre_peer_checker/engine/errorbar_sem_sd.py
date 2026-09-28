"""Legend 誤差棒種別 vs 生データ再計算 — P-ERRORBAR-SEM-SD-MISMATCH."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.llm.legend_schema import detect_error_bar_type
from pre_peer_checker.warnings import WarningItem, WarningTag

_PM_RE = re.compile(
    r"(?:mean|average)?\s*(?P<mean>\d+(?:\.\d+)?)\s*[±\+\-]\s*(?P<err>\d+(?:\.\d+)?)",
    re.I,
)


@dataclass
class ErrorBarClaim:
    mean: float
    err: float
    span: str
    claimed_type: str | None  # sem|sd|…


def extract_errorbar_claims(texts: list[str]) -> list[ErrorBarClaim]:
    claims: list[ErrorBarClaim] = []
    seen: set[tuple[float, float]] = set()
    for t in texts:
        if not t or not str(t).strip():
            continue
        blob = str(t)
        ebt = detect_error_bar_type(blob)
        for m in _PM_RE.finditer(blob):
            mean = float(m.group("mean"))
            err = float(m.group("err"))
            key = (round(mean, 6), round(err, 6))
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                ErrorBarClaim(mean=mean, err=err, span=m.group(0).strip(), claimed_type=ebt)
            )
    return claims


def _group_sd_sem(v: GroupVector) -> tuple[float, float] | None:
    if v.n < 2 or not v.values:
        return None
    mean = sum(v.values) / len(v.values)
    var = sum((x - mean) ** 2 for x in v.values) / (len(v.values) - 1)
    sd = math.sqrt(var)
    sem = sd / math.sqrt(len(v.values))
    return sd, sem


def warnings_from_errorbar_sem_sd(
    texts: list[str],
    vectors: list[GroupVector],
    *,
    rel_tol: float = 0.08,
    abs_tol: float = 0.05,
) -> list[WarningItem]:
    """Flag when claimed type is SEM/SD but numeric ± matches the other."""
    claims = extract_errorbar_claims(texts)
    # Also allow type-only from whole blob when mean±err present nearby
    blob = "\n".join(texts)
    global_type = detect_error_bar_type(blob)
    if not claims:
        return []

    warnings: list[WarningItem] = []
    for claim in claims:
        ctype = claim.claimed_type or global_type
        if ctype not in {"sem", "sd"}:
            continue
        best: tuple[GroupVector, float, float, float] | None = None
        # best = (vector, sd, sem, mean_gap)
        for v in vectors:
            stats = _group_sd_sem(v)
            if stats is None:
                continue
            sd, sem = stats
            mean = sum(v.values) / len(v.values)
            mean_gap = abs(mean - claim.mean)
            if best is None or mean_gap < best[3]:
                best = (v, sd, sem, mean_gap)
        if best is None:
            continue
        v, sd, sem, mean_gap = best
        # Require mean roughly aligned so we compare the right group
        mean_tol = max(abs_tol * 5, abs(claim.mean) * 0.15)
        if mean_gap > mean_tol:
            continue

        def _close(a: float, b: float) -> bool:
            return abs(a - b) <= max(abs_tol, abs(b) * rel_tol)

        matches_sd = _close(claim.err, sd)
        matches_sem = _close(claim.err, sem)
        if matches_sd and matches_sem:
            continue  # n≈1 edge / ambiguous
        if ctype == "sem" and matches_sd and not matches_sem:
            wrong, right = "SD", "SEM"
        elif ctype == "sd" and matches_sem and not matches_sd:
            wrong, right = "SEM", "SD"
        else:
            continue

        warnings.append(
            WarningItem(
                tag=WarningTag.STATS_MISMATCH,
                title="誤差棒種別（SEM/SD）と数値が矛盾",
                location=f"{v.source.name}[{v.group_key}]",
                reason=(
                    f"記載「{claim.span}」は {ctype.upper()} と読めるが、"
                    f"生データでは誤差 {claim.err:g} が {wrong}（{sd if wrong=='SD' else sem:.4g}）に近く、"
                    f"{right}（{sem if right=='SEM' else sd:.4g}）とは一致しません。"
                ),
                sources=[str(v.source)],
                metadata={
                    "pattern_id": "P-ERRORBAR-SEM-SD-MISMATCH",
                    "claimed_type": ctype,
                    "claimed_err": claim.err,
                    "sd": sd,
                    "sem": sem,
                    "group_key": v.group_key,
                },
            )
        )
    return warnings
