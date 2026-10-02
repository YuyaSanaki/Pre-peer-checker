"""Demote n checks on panels that show pictures, not plotted samples.

A legend n under a representative image / blot counts experiments or animals behind the
picture, so a data-row comparison for that panel is not evidence of a mismatch.
"""

from __future__ import annotations

import re

from pre_peer_checker.engine.n_matrix import _fig_match_key
from pre_peer_checker.parsers.plot_marks import PICTURE_KINDS
from pre_peer_checker.warnings import WarningItem

N_PATTERNS = frozenset(
    {
        "P-N-MISMATCH-LEGEND-VS-DATA",
        "P-EXCLUSION-UNDECLARED",
        "P-COUNT-N-MISMATCH",
        "P-SOURCE-DATA-LEGEND-N",
    }
)
_FIG_PANEL_RE = re.compile(r"^(.*?(?:Fig(?:ure)?\.?\s*S?\d+))\s*(?:panels?\s+)?\(?([A-Za-z])\b")


def panel_kinds(figure_panel_arts: list[dict]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for art in figure_panel_arts or []:
        fkey = _fig_match_key(art.get("figure_id") or art.get("figure"))
        if not fkey:
            continue
        for p in art.get("panels") or []:
            kind = p.get("kind")
            if kind:
                out.setdefault((fkey, str(p.get("panel") or "").upper()), kind)
    return out


def _warning_panel(w: WarningItem) -> tuple[str, str] | None:
    meta = w.metadata or {}
    panel = str(meta.get("panel") or "").upper()
    m = _FIG_PANEL_RE.match(w.title or "") or _FIG_PANEL_RE.match(w.location or "")
    fkey = _fig_match_key(m.group(1)) if m else None
    if not panel and m:
        panel = m.group(2).upper()
    return (fkey, panel) if fkey and panel else None


def demote_picture_panel_warnings(
    warnings: list[WarningItem], figure_panel_arts: list[dict]
) -> int:
    """Demote n-pattern warnings whose panel was read as an image / blot. Returns the count."""
    kinds = panel_kinds(figure_panel_arts)
    if not kinds:
        return 0
    n = 0
    for w in warnings:
        meta = w.metadata or {}
        if meta.get("pattern_id") not in N_PATTERNS or w.is_demoted():
            continue
        key = _warning_panel(w)
        kind = kinds.get(key) if key else None
        if kind in PICTURE_KINDS:
            meta["demoted"] = True
            meta["demoted_reason"] = f"panel_kind={kind}"
            w.metadata = meta
            n += 1
    return n
