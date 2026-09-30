"""Panel box accuracy on dev figures: product panel regions vs hand-checked boxes.

Gold lives next to the other panel_extract gold as ``panel_boxes_gold.json``::

    {"role": "panel_boxes_gold", "review": {"status": "draft"},
     "figures": [{"figure": "Figure 1", "file": "Fig1.pdf",
                  "panels": {"A": [x0, y0, x1, y1], ...}}]}

Boxes are fractions (0-1) of the first page of ``input/<case>/figures/<file>``
(the image itself for PNG/JPEG). A panel scores a hit when the predicted box for
the same letter overlaps its gold box with IoU >= ``HIT_IOU``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pre_peer_checker.eval.generalization import Case, _now, figure_key

GOLD_FILE = "panel_boxes_gold.json"
HIT_IOU = 0.5


def gold_path(case: Case) -> Path:
    return case.gold_dir / GOLD_FILE


def load_gold(case: Case) -> dict[str, Any] | None:
    p = gold_path(case)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _frac_box(region: dict[str, Any]) -> list[float] | None:
    pct = region.get("pct") or {}
    try:
        x0, y0 = float(pct["left_pct"]) / 100.0, float(pct["top_pct"]) / 100.0
        return [x0, y0, x0 + float(pct["width_pct"]) / 100.0, y0 + float(pct["height_pct"]) / 100.0]
    except (KeyError, TypeError, ValueError):
        return None


def product_regions(path: Path) -> list[dict[str, Any]]:
    """Panel regions the report would draw for one figure file (vector, then raster OCR)."""
    from pre_peer_checker.parsers.figure_panel_labels import (
        is_raster_figure_path,
        panel_labels_from_figure_detailed,
    )

    regions: list[dict[str, Any]] = []
    if not is_raster_figure_path(path):
        from pre_peer_checker.parsers.pdf_panel_geometry import extract_panel_regions_from_pdf

        regions = extract_panel_regions_from_pdf(path, max_pages=4)
    covered = {(str(r.get("panel") or "").upper(), int(r.get("page_index") or 0)) for r in regions}
    _labels, meta = panel_labels_from_figure_detailed(path)
    for r in meta.regions:
        key = (str(r.get("panel") or "").upper(), int(r.get("page_index") or 0))
        if key not in covered:
            regions.append(r)
    return regions


def predict_case(case: Case, *, only: set[str] | None = None) -> dict[str, dict[str, list[float]]]:
    """Figure key -> {panel (upper) -> fractional box on page 0}."""
    out: dict[str, dict[str, list[float]]] = {}
    scope = case.figures_in_scope
    for key, path in case.figure_files().items():
        if (scope and key.upper() not in scope) or (only and key not in only):
            continue
        boxes: dict[str, list[float]] = {}
        for r in product_regions(path):
            if int(r.get("page_index") or 0) != 0:
                continue
            b = _frac_box(r)
            if b is not None:
                boxes.setdefault(str(r.get("panel") or "").upper(), b)
        out[key] = boxes
    return out


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def score_case(case: Case, preds: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    gold = load_gold(case) or {}
    n_gold = n_hit = n_pred = n_pred_hit = 0
    iou_sum = 0.0
    items: list[dict[str, Any]] = []
    for f in gold.get("figures") or []:
        key = figure_key(f.get("figure", ""))
        gt = {str(k).upper(): v for k, v in (f.get("panels") or {}).items()}
        pr = preds.get(key, {})
        per = {k: round(iou(b, pr[k]), 3) if k in pr else 0.0 for k, b in gt.items()}
        hits = {k for k, v in per.items() if v >= HIT_IOU}
        n_gold += len(gt)
        n_hit += len(hits)
        iou_sum += sum(per.values())
        n_pred += len(pr)
        n_pred_hit += len(hits)
        items.append(
            {
                "figure": f.get("figure"),
                "n_gold": len(gt),
                "n_hit": len(hits),
                "low": {k: v for k, v in sorted(per.items()) if v < HIT_IOU},
                "extra": sorted(set(pr) - set(gt)),
            }
        )
    return {
        "n_gold": n_gold,
        "n_hit": n_hit,
        "recall": n_hit / n_gold if n_gold else None,
        "mean_iou": iou_sum / n_gold if n_gold else None,
        "n_pred": n_pred,
        "precision": n_pred_hit / n_pred if n_pred else None,
        "items": items,
    }


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    n_gold = sum(s["n_gold"] for s in scores)
    n_hit = sum(s["n_hit"] for s in scores)
    n_pred = sum(s["n_pred"] for s in scores)
    iou_sum = sum((s["mean_iou"] or 0.0) * s["n_gold"] for s in scores)
    return {
        "n_gold": n_gold,
        "n_hit": n_hit,
        "recall": n_hit / n_gold if n_gold else None,
        "mean_iou": iou_sum / n_gold if n_gold else None,
        "n_pred": n_pred,
        "precision": n_hit / n_pred if n_pred else None,
    }


def draft_gold(case: Case, preds: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    """Starting point for a human to correct; dev cases only (holdout gold is made blind)."""
    if case.split != "dev":
        raise ValueError(f"{case.case_id}: box gold for {case.split} cases must not start from tool output")
    files = case.figure_files()
    figures = []
    for key in sorted(preds, key=lambda k: (k.startswith("ED"), len(k), k)):
        name = f"Extended Data Figure {key[2:]}" if key.startswith("ED") else f"Figure {key}"
        figures.append(
            {
                "figure": name,
                "file": files[key].name,
                "panels": {k: [round(v, 4) for v in b] for k, b in sorted(preds[key].items())},
            }
        )
    return {
        "schema_version": "1.0",
        "case_id": case.case_id,
        "role": "panel_boxes_gold",
        "description": "Panel boxes (fractions of page 0 of figures/<file>), drafted from tool output and corrected by eye.",
        "review": {"status": "draft", "reviewed_by": "", "reviewed_at": "", "drafted_at": _now()},
        "figures": figures,
    }
