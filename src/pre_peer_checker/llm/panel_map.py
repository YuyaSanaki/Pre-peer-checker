"""VLM パネル地図スキーマ（ベクター分割の補助・6A）.

読む＝VLM。比べる＝機械（矩形は pct→page points に変換して決定論利用）。
"""

from __future__ import annotations

import json
import re
from typing import Any

PANEL_MAP_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "FigurePanelMap",
    "type": "object",
    "required": ["panels"],
    "properties": {
        "figure": {"type": "string"},
        "page_index": {"type": "integer"},
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["panel", "left_pct", "top_pct", "width_pct", "height_pct"],
                "properties": {
                    "panel": {"type": "string"},
                    "left_pct": {"type": "number"},
                    "top_pct": {"type": "number"},
                    "width_pct": {"type": "number"},
                    "height_pct": {"type": "number"},
                    "confidence": {"type": ["number", "null"]},
                },
            },
        },
    },
}

PANEL_MAP_SYSTEM_PROMPT = """You locate figure panel letters on a multipanel scientific figure image.
Return ONLY valid JSON (no markdown):
{
  "figure": "Figure 1",
  "page_index": 0,
  "panels": [
    {"panel": "A", "left_pct": 2.0, "top_pct": 3.0, "width_pct": 48.0, "height_pct": 45.0, "confidence": 0.85},
    {"panel": "B", "left_pct": 52.0, "top_pct": 3.0, "width_pct": 46.0, "height_pct": 45.0, "confidence": 0.85}
  ]
}
Rules:
- Percentages are relative to the full page/image (0–100).
- panel is a single uppercase letter (A–Z) matching visible labels.
- Boxes should cover the panel content roughly; do not invent panels.
- If unsure, omit the panel rather than guess.
"""


def build_panel_map_prompt(*, figure_hint: str | None = None, page_index: int = 0) -> str:
    fig = figure_hint or "Figure"
    return (
        f"{PANEL_MAP_SYSTEM_PROMPT}\n"
        f"figure_hint={fig}\n"
        f"page_index={page_index}\n"
        "Locate every visible panel letter and return the JSON object."
    )


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _clamp_pct(v: Any, *, lo: float = 0.0, hi: float = 100.0) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return max(lo, min(hi, x))


def coerce_panel_map_dict(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    soft: list[str] = []
    panels_in = data.get("panels") if isinstance(data.get("panels"), list) else []
    panels_out: list[dict[str, Any]] = []
    for raw in panels_in:
        if not isinstance(raw, dict):
            soft.append("skip_non_object_panel")
            continue
        letter = str(raw.get("panel") or "").strip().upper()
        if len(letter) != 1 or not letter.isalpha():
            soft.append(f"skip_bad_panel:{raw.get('panel')!r}")
            continue
        left = _clamp_pct(raw.get("left_pct"))
        top = _clamp_pct(raw.get("top_pct"))
        width = _clamp_pct(raw.get("width_pct"))
        height = _clamp_pct(raw.get("height_pct"))
        if None in (left, top, width, height):
            soft.append(f"skip_bad_bbox:{letter}")
            continue
        assert left is not None and top is not None and width is not None and height is not None
        if width < 3.0 or height < 3.0:
            soft.append(f"skip_tiny:{letter}")
            continue
        if left + width > 100.5:
            width = max(3.0, 100.0 - left)
        if top + height > 100.5:
            height = max(3.0, 100.0 - top)
        conf = raw.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf_f = None
        if conf_f is not None:
            conf_f = max(0.0, min(1.0, conf_f))
        panels_out.append(
            {
                "panel": letter,
                "left_pct": round(left, 3),
                "top_pct": round(top, 3),
                "width_pct": round(width, 3),
                "height_pct": round(height, 3),
                "confidence": conf_f,
            }
        )
    page_index = data.get("page_index", 0)
    try:
        page_i = int(page_index)
    except (TypeError, ValueError):
        page_i = 0
        soft.append("page_index_coerced")
    out = {
        "figure": str(data.get("figure") or "Figure"),
        "page_index": page_i,
        "panels": panels_out,
    }
    return out, soft


def parse_panel_map_response(text: str) -> dict[str, Any] | None:
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    data: dict[str, Any] | None = None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            data = obj
    except json.JSONDecodeError:
        m = _JSON_RE.search(raw)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    data = obj
            except json.JSONDecodeError:
                data = None
    if not data:
        return None
    coerced, _soft = coerce_panel_map_dict(data)
    if not coerced["panels"]:
        return None
    return coerced


def panel_map_to_region_dicts(
    panel_map: dict[str, Any],
    *,
    page_w: float,
    page_h: float,
    source: str,
    source_name: str,
) -> list[dict[str, Any]]:
    """Convert coerced panel map (pct) into figure_panel_regions-compatible dicts."""
    if page_w <= 0 or page_h <= 0:
        return []
    page_index = int(panel_map.get("page_index") or 0)
    out: list[dict[str, Any]] = []
    for p in panel_map.get("panels") or []:
        left = float(p["left_pct"]) / 100.0 * page_w
        top = float(p["top_pct"]) / 100.0 * page_h
        width = float(p["width_pct"]) / 100.0 * page_w
        height = float(p["height_pct"]) / 100.0 * page_h
        x0, y0 = left, top
        x1, y1 = left + width, top + height
        out.append(
            {
                "panel": p["panel"],
                "page_index": page_index,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "label_x": x0 + min(12.0, width * 0.1),
                "label_y": y0 + min(12.0, height * 0.1),
                "font_size": 0.0,
                "pct": {
                    "left_pct": p["left_pct"],
                    "top_pct": p["top_pct"],
                    "width_pct": p["width_pct"],
                    "height_pct": p["height_pct"],
                },
                "source": source,
                "source_name": source_name,
                "geometry_source": "vlm",
                "confidence": p.get("confidence"),
            }
        )
    return out
