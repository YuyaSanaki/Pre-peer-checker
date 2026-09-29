"""本文 Fig パネル参照 ↔ 図上ラベル — P-REF-LABEL-MISMATCH."""

from __future__ import annotations

import re
from collections import defaultdict

from pre_peer_checker.warnings import WarningItem, WarningTag

_REF_RE = re.compile(
    r"(?:Fig(?:ure)?\.?\s*(?P<fig>\d+)\s*[.\-]?\s*(?P<panel>[A-Za-z])"
    r"|Figs?\.?\s*(?P<fig2>\d+)\s*\((?P<panels>[A-Za-z,\s]+)\)"
    r"|図\s*(?P<fig3>\d+)\s*(?P<panel3>[A-Za-z]))",
    re.I,
)


def extract_fig_panel_refs(texts: list[str]) -> dict[str, set[str]]:
    """Map figure number key ('1','2',…) → referenced panel letters."""
    by: dict[str, set[str]] = defaultdict(set)
    for t in texts:
        if not t:
            continue
        for m in _REF_RE.finditer(str(t)):
            fig = m.group("fig") or m.group("fig2") or m.group("fig3")
            if not fig:
                continue
            panel = m.group("panel") or m.group("panel3")
            if panel:
                by[str(int(fig))].add(panel.upper())
                continue
            panels = m.group("panels") or ""
            for p in re.findall(r"[A-Za-z]", panels):
                by[str(int(fig))].add(p.upper())
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
