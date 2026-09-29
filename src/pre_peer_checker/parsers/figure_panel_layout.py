"""Panel letter detection and crop regions from OCR/layout boxes (raster figures)."""

from __future__ import annotations

from itertools import pairwise

_TRIM = "()[]{},.;:'\"“”‘’"


def box_center(b: list[float]) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def panel_letter_dets(
    dets: list[dict],
    *,
    case: str = "upper",
) -> list[tuple[str, list[float]]]:
    """Single-letter panel labels from OCR detections (largest box wins per letter)."""
    want = str.isupper if case == "upper" else str.islower
    best: dict[str, list[float]] = {}
    for d in dets:
        for tok in str(d.get("text", "")).split():
            t = tok.strip(_TRIM)
            if len(t) != 1 or not t.isalpha() or not want(t):
                continue
            key = t.upper() if case == "upper" else t.lower()
            box = list(d["box"])
            if key not in best or (box[2] - box[0]) * (box[3] - box[1]) > (
                best[key][2] - best[key][0]
            ) * (best[key][3] - best[key][1]):
                best[key] = box
    return sorted(best.items(), key=lambda x: (box_center(x[1])[1], box_center(x[1])[0]))


def dominant_panel_run(letters, *, max_gap: int = 1) -> set[str]:
    """Longest near-contiguous letter run; drops isolated OCR noise.

    Panel letters in a figure run A..N (an occasional letter may be missed), so a
    letter separated from the run by more than ``max_gap`` gaps is axis text or a
    legend key rather than a panel label.
    """
    ls = sorted(c for c in letters if c.isascii() and c.isalpha() and len(c) == 1)
    if not ls:
        return set()
    runs: list[list[str]] = [[ls[0]]]
    for prev, cur in pairwise(ls):
        if ord(cur) - ord(prev) - 1 <= max_gap:
            runs[-1].append(cur)
        else:
            runs.append([cur])
    return set(max(runs, key=lambda r: (len(r), -ord(r[0]))))


def crops_from_panel_letters(
    width: int,
    height: int,
    letters: list[tuple[str, list[float]]],
) -> list[dict]:
    """Split figure into panel crops from letter anchor boxes."""
    if not letters:
        return [{"panel": "*", "box": [0.0, 0.0, float(width), float(height)]}]
    centers = [(box_center(b)[0], box_center(b)[1], ch, b) for ch, b in letters]
    row_eps = max(8.0, height * 0.04)
    rows: list[list[tuple]] = []
    for cx, cy, ch, b in sorted(centers, key=lambda t: (t[1], t[0])):
        placed = False
        for row in rows:
            if abs(cy - row[0][1]) <= row_eps:
                row.append((cx, cy, ch, b))
                placed = True
                break
        if not placed:
            rows.append([(cx, cy, ch, b)])
    for row in rows:
        row.sort(key=lambda t: t[0])
    rows.sort(key=lambda r: r[0][1])
    row_ys = [sum(c[1] for c in r) / len(r) for r in rows]
    out: list[dict] = []
    for ri, row in enumerate(rows):
        y_top = 0.0 if ri == 0 else (row_ys[ri - 1] + row_ys[ri]) / 2
        y_bot = float(height) if ri == len(rows) - 1 else (row_ys[ri] + row_ys[ri + 1]) / 2
        for ci, (cx, _cy, ch, b) in enumerate(row):
            x_left = 0.0 if ci == 0 else (row[ci - 1][0] + cx) / 2
            x_right = float(width) if ci == len(row) - 1 else (cx + row[ci + 1][0]) / 2
            pad = 4.0
            x0 = max(0.0, min(b[0], x_left) - pad)
            y0 = max(0.0, min(b[1], y_top) - pad)
            x1 = min(float(width), max(b[2], x_right) + pad)
            y1 = min(float(height), max(b[3], y_bot) + pad)
            if x1 - x0 > 20 and y1 - y0 > 20:
                out.append({"panel": ch, "box": [x0, y0, x1, y1]})
    return out or [{"panel": "*", "box": [0.0, 0.0, float(width), float(height)]}]


def region_dicts_from_crops(
    path,
    crops: list[dict],
    width: float,
    height: float,
    *,
    page_index: int = 0,
    geometry_source: str = "raster_ocr",
) -> list[dict]:
    """Convert letter-anchor crops to ``figure_panel_regions`` dicts.

    Whole-image fallback crops (panel ``*``) are skipped — they are not a split.
    """
    from pathlib import Path

    path = Path(path)
    if width <= 0 or height <= 0:
        return []
    out: list[dict] = []
    for ent in crops:
        panel = str(ent.get("panel") or "").strip()
        if not panel or panel == "*":
            continue
        box = ent.get("box") or []
        if len(box) < 4:
            continue
        x0, y0, x1, y1 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        if x1 - x0 <= 20 or y1 - y0 <= 20:
            continue
        x0 = max(0.0, min(x0, width))
        y0 = max(0.0, min(y0, height))
        x1 = max(0.0, min(x1, width))
        y1 = max(0.0, min(y1, height))
        out.append(
            {
                "panel": panel.upper() if panel.isascii() else panel,
                "page_index": page_index,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "label_x": x0 + min(12.0, (x1 - x0) * 0.1),
                "label_y": y0 + min(12.0, (y1 - y0) * 0.1),
                "font_size": 0.0,
                "pct": {
                    "left_pct": round(100.0 * x0 / width, 3),
                    "top_pct": round(100.0 * y0 / height, 3),
                    "width_pct": round(100.0 * (x1 - x0) / width, 3),
                    "height_pct": round(100.0 * (y1 - y0) / height, 3),
                },
                "source": str(path),
                "source_name": path.name,
                "geometry_source": geometry_source,
            }
        )
    return out
