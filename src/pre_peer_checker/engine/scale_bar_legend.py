"""図中スケールバー表記 ↔ Legend のスケールバー長 — P-SCALE-BAR-LEGEND-MISMATCH."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.parsers.figure_chunks import figure_label, figure_num_key
from pre_peer_checker.parsers.figure_panel_labels import _figure_num_from_pdf_name
from pre_peer_checker.parsers.scale_labels import (
    FigureScaleLabel,
    lengths_in_text,
    raster_scale_labels,
    vector_scale_labels_from_pdf,
)
from pre_peer_checker.warnings import WarningItem, WarningTag

# "Scale bars, 50 µm" / "Scale bar = 10 μm" / "Bars, 20 µm" / "(bar: 5 µm)" — not "error bars".
_ANCHOR_RE = re.compile(
    r"\bscale[\s-]*bars?\b"
    r"|(?:^|(?<=[.;(\[]\s)|(?<=[.;(\[]))bars?\s*(?:[,:=]|represents?\b|indicates?\b|denotes?\b|are\b|is\b)",
    re.IGNORECASE,
)
_CLAUSE_END_RE = re.compile(r"(?<!\d)\.(?!\d)|\n")
_CLAUSE_MAX = 260
# Legend allows figure-specific values that it does not list.
_FLEXIBLE_RE = re.compile(
    r"unless\s+(?:otherwise\s+)?(?:indicated|stated|noted|specified|mentioned)"
    r"|(?:as|where|if)\s+(?:indicated|shown|labell?ed|marked)"
    r"|(?:indicated|shown|labell?ed)\s+(?:in|on|within)\s+(?:the\s+|each\s+)?(?:images?|panels?|figures?|micrographs?)",
    re.IGNORECASE,
)
_REL_TOL = 1e-6


@dataclass
class LegendScaleClaim:
    values_um: list[float] = field(default_factory=list)
    spans: list[str] = field(default_factory=list)
    flexible: bool = False


def legend_scale_claim(text: str) -> LegendScaleClaim:
    """Scale-bar lengths stated in one legend."""
    claim = LegendScaleClaim()
    blob = text or ""
    pos = 0
    while True:
        m = _ANCHOR_RE.search(blob, pos)
        if m is None:
            break
        end_m = _CLAUSE_END_RE.search(blob, m.end())
        end = end_m.start() if end_m else len(blob)
        end = min(end, m.start() + _CLAUSE_MAX)
        clause = blob[m.start() : end]
        lengths = lengths_in_text(clause)
        if lengths:
            claim.spans.append(clause.strip())
            for value, _unit, _raw in lengths:
                if not _has_value(claim.values_um, value):
                    claim.values_um.append(value)
            if _FLEXIBLE_RE.search(clause):
                claim.flexible = True
        pos = max(end, m.end())
    return claim


def legend_scale_claims_by_figure(
    legends: Iterable[tuple[str, str]],
) -> dict[str, LegendScaleClaim]:
    """``(figure name, legend text)`` pairs → figure key ('1', 'S2', 'ED3') → claim."""
    out: dict[str, LegendScaleClaim] = {}
    for figure, text in legends:
        claim = legend_scale_claim(text)
        if not claim.values_um:
            continue
        key = figure_num_key(figure)
        prev = out.get(key)
        if prev is None:
            out[key] = claim
            continue
        for v in claim.values_um:
            if not _has_value(prev.values_um, v):
                prev.values_um.append(v)
        prev.spans.extend(s for s in claim.spans if s not in prev.spans)
        prev.flexible = prev.flexible or claim.flexible
    return out


def _has_value(values: Iterable[float], v: float) -> bool:
    return any(abs(x - v) <= _REL_TOL * max(abs(x), abs(v), 1.0) for x in values)


def _label_matches(label: FigureScaleLabel, legend_values: list[float]) -> bool:
    if _has_value(legend_values, label.value_um):
        return True
    # Symbol-font µ often extracts (or OCRs) as a plain "m": "50 mm" for 50 µm.
    return label.unit == "mm" and _has_value(legend_values, label.value_um / 1000.0)


def _scale_bar_ocr_enabled() -> bool:
    mode = (os.environ.get("PRE_PEER_CHECKER_SCALE_BAR_OCR") or "auto").strip().lower()
    if mode in {"0", "false", "no", "off"}:
        return False
    from pre_peer_checker.parsers.raster_figure_panel_ocr import raster_panel_ocr_enabled

    return raster_panel_ocr_enabled()


def _fmt_um(v: float) -> str:
    if v >= 1000:
        return f"{v / 1000:g} mm"
    if v < 1:
        return f"{v * 1000:g} nm"
    return f"{v:g} µm"


def figure_scale_labels(
    fig_files: Iterable[Path | str],
    keys: set[str],
    *,
    ocr: bool | None = None,
    on_item: Callable[[int, int, str], None] | None = None,
) -> dict[str, list[FigureScaleLabel]]:
    """Figure key → scale-bar labels drawn in that figure (only for ``keys``)."""
    use_ocr = _scale_bar_ocr_enabled() if ocr is None else ocr
    targets: list[tuple[str, Path]] = []
    for raw in fig_files:
        p = Path(raw)
        key = _figure_num_from_pdf_name(p)
        if key and key in keys:
            targets.append((key, p))
    out: dict[str, list[FigureScaleLabel]] = {}
    for i, (key, p) in enumerate(targets):
        if on_item:
            on_item(i, len(targets), p.name)
        labels: list[FigureScaleLabel] = []
        if p.suffix.lower() == ".pdf":
            labels = vector_scale_labels_from_pdf(p)
        if not labels and use_ocr:
            try:
                labels = raster_scale_labels(p)
            except Exception:  # noqa: BLE001 — OCR backends are optional
                labels = []
        if labels:
            out.setdefault(key, []).extend(labels)
    if on_item and targets:
        on_item(len(targets), len(targets), "")
    return out


def warnings_from_scale_bar_labels(
    claims: dict[str, LegendScaleClaim],
    labels_by_figure: dict[str, list[FigureScaleLabel]],
) -> list[WarningItem]:
    """Emit when a length printed in the figure is not among the legend's scale bars."""
    warnings: list[WarningItem] = []
    for key in sorted(labels_by_figure):
        claim = claims.get(key)
        labels = labels_by_figure[key]
        if claim is None or not claim.values_um or claim.flexible or not labels:
            continue
        unmatched = [lb for lb in labels if not _label_matches(lb, claim.values_um)]
        if not unmatched:
            continue
        fig_vals: list[float] = []
        for lb in unmatched:
            if not _has_value(fig_vals, lb.value_um):
                fig_vals.append(lb.value_um)
        ocr_only = all(lb.origin == "raster_ocr" for lb in unmatched)
        sources = sorted({lb.source for lb in unmatched})
        fig_txt = "、".join(_fmt_um(v) for v in fig_vals)
        leg_txt = "、".join(_fmt_um(v) for v in claim.values_um)
        reason = (
            f"図中のスケールバー表記「{fig_txt}」が、Legend のスケールバー記載"
            f"（{leg_txt}）にありません。Legend: 「{claim.spans[0][:120]}」。"
        )
        if ocr_only:
            reason += "図中の値は画像 OCR の読み取りです（誤読の可能性があるため目視確認してください）。"
        warnings.append(
            WarningItem(
                tag=WarningTag.CONFIG_MISMATCH,
                title="図中のスケールバー表記と Legend の記載が不一致",
                location=f"{figure_label(key)}: 図中 {fig_txt} ↔ Legend {leg_txt}",
                reason=reason,
                sources=sources,
                metadata={
                    "pattern_id": "P-SCALE-BAR-LEGEND-MISMATCH",
                    "figure": key,
                    "figure_values_um": fig_vals,
                    "legend_values_um": list(claim.values_um),
                    "figure_labels": [lb.to_dict() for lb in unmatched],
                    "legend_spans": list(claim.spans),
                    "origin": "raster_ocr" if ocr_only else "vector",
                    "needs_review": ocr_only,
                },
            )
        )
    return warnings


def warnings_from_scale_bar_legend(
    legends: Iterable[tuple[str, str]],
    fig_files: Iterable[Path | str],
    *,
    ocr: bool | None = None,
    on_item: Callable[[int, int, str], None] | None = None,
) -> tuple[list[WarningItem], dict]:
    """Legend scale-bar lengths vs the labels drawn in the matching figure files."""
    claims = legend_scale_claims_by_figure(legends)
    keys = {k for k, c in claims.items() if not c.flexible}
    labels = figure_scale_labels(fig_files, keys, ocr=ocr, on_item=on_item) if keys else {}
    artifact = {
        "legend_claims": {
            k: {"values_um": c.values_um, "flexible": c.flexible} for k, c in claims.items()
        },
        "figure_labels": {
            k: [lb.to_dict() for lb in v] for k, v in labels.items()
        },
    }
    return warnings_from_scale_bar_labels(claims, labels), artifact
