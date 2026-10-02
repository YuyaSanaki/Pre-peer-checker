"""Candidate cards for legend rows the linker could not pin to one table.

When linking abstains (no candidate strong enough, or two that tie) the panel is not
dropped silently: the plausible tables are listed side by side in a ``severity=info``
card. Cards never set the n-matrix mismatch flag; they point the author at what to
check instead of turning an unlinked row into a hard finding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.engine.entity_link import (
    PanelKey,
    _figure_scoped,
    _panel_key,
    _profile_units,
    candidate_vectors,
    legend_n_runs,
    min_mismatch_confidence,
)
from pre_peer_checker.engine.n_and_names import (
    N_AUTHORITY_FOOTER,
    N_AUTHORITY_META,
    figure_num_from_label,
)
from pre_peer_checker.parsers.legend_struct import PanelN
from pre_peer_checker.warnings import WarningItem, WarningTag

# more look-alike tables than this and the card would not narrow anything down
MAX_CANDIDATES = 3


@dataclass
class Candidate:
    source: Path
    sheet: str
    group_ns: list[int]
    copies: int = 1

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "sheet": self.sheet,
            "group_ns": self.group_ns,
            "copies": self.copies,
        }


@dataclass
class CandidateCard:
    figure: str
    panels: list[str]
    legend_ns: list[int]
    kind: str  # "weak_link" | "statement_shape"
    candidates: list[Candidate] = field(default_factory=list)
    keys: list[PanelKey] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "figure": self.figure,
            "panels": self.panels,
            "legend_ns": self.legend_ns,
            "kind": self.kind,
            "candidates": [c.to_dict() for c in self.candidates],
            "keys": [list(k) for k in self.keys],
        }


def _near(legend: list[int], data: list[int]) -> bool:
    """Same group count, each n close but not all equal (sorted, order-free)."""
    if len(legend) != len(data):
        return False
    pairs = list(zip(sorted(legend), sorted(data)))
    if all(a == b for a, b in pairs):
        return False
    return all(abs(a - b) <= max(1, math.ceil(0.2 * a)) for a, b in pairs)


def _row_confident(row, threshold: float) -> bool:
    return any(
        cell.link_status == "linked" and cell.n is not None and cell.confidence >= threshold
        for cell in (row.data, row.plot)
    )


def _distinct_sources(pool: list[GroupVector]) -> list[Candidate]:
    """One candidate per sheet content; byte-identical copies in other folders are folded."""
    by_source: dict[tuple[Path, str], list[GroupVector]] = {}
    for v in pool:
        by_source.setdefault((v.source, v.sheet), []).append(v)
    out: dict[tuple, Candidate] = {}
    for (src, sheet) in sorted(by_source, key=lambda s: (len(str(s[0])), str(s[0]), s[1])):
        vs = by_source[(src, sheet)]
        content = tuple(sorted((v.n, v.values) for v in vs))
        if content in out:
            out[content].copies += 1
            continue
        out[content] = Candidate(source=src, sheet=sheet, group_ns=[v.n for v in vs])
    return list(out.values())


def statement_shape_cards(
    panel_ns: list[PanelN],
    vectors: list[GroupVector],
    rows: list,
    *,
    threshold: float | None = None,
) -> list[CandidateCard]:
    """Multi-panel legend statements whose rows stayed unlinked, with same-shape tables.

    ``n = 12 (E), 12 (F), 12 (G)`` against a three-group table of n 11/11/11 in the same
    figure is a likely source the exact-n linkers cannot accept.
    """
    from pre_peer_checker.data.source_data_blocks import is_source_data_workbook

    thr = min_mismatch_confidence() if threshold is None else threshold
    by_key = {(r.figure, r.panel.upper(), r.group or ""): r for r in rows}
    pool = [
        v for v in candidate_vectors(vectors, prefer_plot=None)
        if not is_source_data_workbook(v.source)
    ]
    cards: list[CandidateCard] = []
    for run in legend_n_runs(panel_ns):
        fnum = figure_num_from_label(run[0].figure)
        sources = _distinct_sources(_figure_scoped(pool, fnum))
        for unit in _profile_units(run):
            if len(unit) < 2:
                continue
            keys = [_panel_key(pn) for pn in unit]
            unit_rows = [by_key.get(k) for k in keys]
            if any(r is not None and _row_confident(r, thr) for r in unit_rows):
                continue
            legend = [pn.n for pn in unit]
            hits = [c for c in sources if _near(legend, c.group_ns)]
            if not hits or len(hits) > MAX_CANDIDATES:
                continue
            cards.append(
                CandidateCard(
                    figure=unit[0].figure,
                    panels=[pn.panel.upper() for pn in unit],
                    legend_ns=legend,
                    kind="statement_shape",
                    candidates=hits,
                    keys=keys,
                )
            )
    return cards


def weak_link_cards(rows: list, *, threshold: float | None = None) -> list[CandidateCard]:
    """Rows shown with a guessed table (path / name) whose n differs from the legend."""
    thr = min_mismatch_confidence() if threshold is None else threshold
    cards: list[CandidateCard] = []
    for r in rows:
        legend_n = r.manuscript.n
        if legend_n is None or _row_confident(r, thr):
            continue
        for cell in (r.data, r.plot):
            # a guessed table whose n is far off is more likely the wrong table than a mismatch
            if cell.link_status != "linked" or cell.n is None or not _near([legend_n], [cell.n]):
                continue
            if cell.n_lower_bound and legend_n >= cell.n:
                continue
            cards.append(
                CandidateCard(
                    figure=r.figure,
                    panels=[r.panel],
                    legend_ns=[legend_n],
                    kind="weak_link",
                    candidates=[
                        Candidate(source=Path(cell.file or ""), sheet="", group_ns=[cell.n])
                    ],
                    keys=[(r.figure, r.panel.upper(), r.group or "")],
                )
            )
            break
    return cards


def link_candidate_cards(
    panel_ns: list[PanelN], vectors: list[GroupVector], rows: list
) -> list[CandidateCard]:
    shape = statement_shape_cards(panel_ns, vectors, rows)
    covered = {k for c in shape for k in c.keys}
    weak = [c for c in weak_link_cards(rows) if not set(c.keys) & covered]
    return shape + weak


def warnings_from_candidate_cards(cards: list[CandidateCard]) -> list[WarningItem]:
    out: list[WarningItem] = []
    for c in cards:
        panels = "/".join(c.panels)
        legend = ", ".join(f"{p}={n}" for p, n in zip(c.panels, c.legend_ns))
        lines = []
        for cand in c.candidates:
            copies = f"（同一内容 {cand.copies} 件）" if cand.copies > 1 else ""
            lines.append(f"{cand.source.name}: 群 n={cand.group_ns}{copies}")
        if c.kind == "statement_shape":
            basis = (
                "Legend の同じ n 記述に並ぶパネル数と群数が一致し、n が近い表です"
                "（n 完全一致ではないため紐付けを保留）。"
            )
        else:
            basis = "パス／ファイル名からの推定でのみ紐付いた表です（内容での裏付けなし）。"
        out.append(
            WarningItem(
                tag=WarningTag.SAMPLE_SIZE,
                title=f"{c.figure} panel {panels}: 紐付け候補（要確認・参考）",
                location=f"{c.figure} ({panels})",
                reason=(
                    f"Legend n: {legend}。候補: " + " / ".join(lines) + "。"
                    + basis
                    + "どの表がこのパネルの元データか確認してください。"
                    + N_AUTHORITY_FOOTER
                ),
                sources=[str(cand.source) for cand in c.candidates],
                metadata={
                    "pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA",
                    "severity": "info",
                    "info_card": True,
                    "card_kind": c.kind,
                    "panels": c.panels,
                    "legend_ns": c.legend_ns,
                    "candidates": [cand.to_dict() for cand in c.candidates],
                    **N_AUTHORITY_META,
                },
            )
        )
    return out
