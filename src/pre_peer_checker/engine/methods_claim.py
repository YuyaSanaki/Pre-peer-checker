"""Methods／本文主張 ↔ 図・Legend 矛盾 — P-METHODS-CLAIM-MISMATCH."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pre_peer_checker.warnings import WarningItem, WarningTag

_RATIO_RE = re.compile(
    r"(?:(?:ratio|stoichiometry|combined?\s+at|mixed?\s+at|at\s+a\s+ratio\s+of)\s*)?"
    r"(?P<a>\d+)\s*:\s*(?P<b>\d+)",
    re.I,
)

_METHODS_HINT = re.compile(r"\bmethods?\b|\bexperimental\s+procedures?\b", re.I)
_FIGURE_HINT = re.compile(
    r"\b(?:figure|fig\.?|extended\s+data|legend)\b",
    re.I,
)


@dataclass(frozen=True)
class RatioClaim:
    a: int
    b: int
    span: str
    section: str  # methods | figure | other


def _section_of(text: str) -> str:
    head = text[:80]
    if _METHODS_HINT.search(head) or text.lstrip().lower().startswith("methods"):
        return "methods"
    if _FIGURE_HINT.search(head) or text.lstrip().lower().startswith("figure"):
        return "figure"
    return "other"


def extract_ratio_claims(texts: list[str]) -> list[RatioClaim]:
    out: list[RatioClaim] = []
    seen: set[tuple[str, int, int]] = set()
    for t in texts:
        if not t or not str(t).strip():
            continue
        blob = str(t)
        section = _section_of(blob)
        for m in _RATIO_RE.finditer(blob):
            a, b = int(m.group("a")), int(m.group("b"))
            if a <= 0 or b <= 0:
                continue
            # skip n=3:1 style false friends when preceded by n=
            start = m.start()
            prefix = blob[max(0, start - 3) : start].lower()
            if "n=" in prefix or "n =" in prefix:
                continue
            span = m.group(0).strip()
            key = (section, a, b)
            if key in seen:
                continue
            seen.add(key)
            out.append(RatioClaim(a=a, b=b, span=span, section=section))
    return out


def warnings_from_methods_claims(texts: list[str]) -> list[WarningItem]:
    """Emit when Methods ratio disagrees with Figure/Legend ratio."""
    claims = extract_ratio_claims(texts)
    methods = [c for c in claims if c.section == "methods"]
    figures = [c for c in claims if c.section == "figure"]
    if not methods or not figures:
        # Fallback: if unlabeled blobs carry different ratios and both cues exist
        if len(claims) < 2:
            return []
        blob = "\n".join(texts)
        if not (_METHODS_HINT.search(blob) and _FIGURE_HINT.search(blob)):
            return []
        # Use first two distinct ratios from whole text
        distinct: list[RatioClaim] = []
        for c in claims:
            if not any(c.a == d.a and c.b == d.b for d in distinct):
                distinct.append(c)
            if len(distinct) >= 2:
                break
        if len(distinct) < 2:
            return []
        methods, figures = [distinct[0]], [distinct[1]]

    warnings: list[WarningItem] = []
    seen: set[tuple[int, int, int, int]] = set()
    for m in methods:
        for f in figures:
            if m.a == f.a and m.b == f.b:
                continue
            key = (m.a, m.b, f.a, f.b)
            if key in seen:
                continue
            seen.add(key)
            warnings.append(
                WarningItem(
                    tag=WarningTag.CONFIG_MISMATCH,
                    title="Methods の数量主張と図／Legend が矛盾",
                    location="Methods ↔ Figure 1 legend",
                    reason=(
                        f"Methods「{m.span}」({m.a}:{m.b}) と "
                        f"図側「{f.span}」({f.a}:{f.b}) が不一致。"
                    ),
                    sources=[],
                    metadata={
                        "pattern_id": "P-METHODS-CLAIM-MISMATCH",
                        "methods_ratio": [m.a, m.b],
                        "figure_ratio": [f.a, f.b],
                        "methods_span": m.span,
                        "figure_span": f.span,
                    },
                )
            )
    return warnings
