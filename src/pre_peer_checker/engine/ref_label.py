"""本文 Fig パネル参照 ↔ 図上ラベル — P-REF-LABEL-MISMATCH."""

from __future__ import annotations

import re
from collections import defaultdict

from pre_peer_checker.warnings import WarningItem, WarningTag

_LETTER = r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])"
_PANEL_SPAN_RE = re.compile(rf"({_LETTER})(?:\s*[–—-]\s*({_LETTER}))?")
_REF_RE = re.compile(
    r"(?:(?P<pre>Extended\s+Data\s+|Supplementa(?:ry|l)\s+)?"
    r"Fig(?:ure)?s?\.?\s*(?P<fig>S?\d+)"
    rf"(?:[.\-]?(?P<panel>{_LETTER})(?:\s*[–—-]\s*(?P<panel_end>{_LETTER}))?"
    rf"|\s*\((?P<panels>{_LETTER}(?:\s*(?:,|and|[–—-])\s*{_LETTER})*)\))"
    rf"|図\s*(?P<fig3>\d+)\s*(?P<panel3>{_LETTER}))",
    re.IGNORECASE,
)


def _figure_ref_key(prefix: str | None, num: str) -> str:
    """'1' / 'S2' / 'ED3' — same keys as ``figure_num_key`` for figure files."""
    p = (prefix or "").lower()
    digits = str(int(num.lstrip("Ss")))
    if p.startswith("extended"):
        return f"ED{digits}"
    if p.startswith("supplement") or num[:1] in "Ss":
        return f"S{digits}"
    return digits


def _letter_span(first: str, last: str | None) -> list[str]:
    a, b = first.upper(), (last or first).upper()
    if ord(b) < ord(a) or ord(b) - ord(a) > 25:
        return [a]
    return [chr(c) for c in range(ord(a), ord(b) + 1)]


def extract_fig_panel_refs(texts: list[str]) -> dict[str, set[str]]:
    """Map figure key ('1', 'S2', 'ED3', …) → referenced panel letters."""
    by: dict[str, set[str]] = defaultdict(set)
    for t in texts:
        if not t:
            continue
        for m in _REF_RE.finditer(str(t)):
            if m.group("fig3"):
                by[str(int(m.group("fig3")))].add(m.group("panel3").upper())
                continue
            key = _figure_ref_key(m.group("pre"), m.group("fig"))
            if m.group("panel"):
                by[key].update(_letter_span(m.group("panel"), m.group("panel_end")))
                continue
            for first, last in _PANEL_SPAN_RE.findall(m.group("panels") or ""):
                by[key].update(_letter_span(first, last or None))
    return dict(by)


def warnings_from_ref_labels(
    texts: list[str],
    labels_by_figure: dict[str, list[str]],
    *,
    max_warnings: int = 5,
) -> list[WarningItem]:
    """Body/Legend cites a panel letter that does not appear on the figure."""
    if not labels_by_figure:
        return []
    refs = extract_fig_panel_refs(texts)
    if not refs:
        return []

    warnings: list[WarningItem] = []
    for fig, cited in sorted(refs.items()):
        present = {x.upper() for x in (labels_by_figure.get(fig) or [])}
        if not present:
            # Fall back to wildcard key used when PDF name lacks FigN
            present = {x.upper() for x in (labels_by_figure.get("*") or [])}
        if not present:
            continue
        missing = sorted(cited - present)
        if not missing:
            continue
        warnings.append(
            WarningItem(
                tag=WarningTag.REF_INCONSISTENCY,
                title=f"Figure {fig}: 本文参照パネルが図のラベルに無い",
                location=f"Fig.{fig}{''.join(missing)}",
                reason=(
                    f"本文／Legend が Figure {fig} のパネル "
                    f"{', '.join(missing)} を参照していますが、"
                    f"出版図上のラベルは {', '.join(sorted(present))} のみです。"
                ),
                sources=[],
                metadata={
                    "pattern_id": "P-REF-LABEL-MISMATCH",
                    "figure": fig,
                    "cited_panels": sorted(cited),
                    "pdf_panels": sorted(present),
                    "figure_panels": sorted(present),
                    "missing_panels": missing,
                },
            )
        )
        if len(warnings) >= max_warnings:
            break
    return warnings
