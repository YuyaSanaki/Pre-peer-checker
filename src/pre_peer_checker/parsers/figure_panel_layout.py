"""Panel letter detection and crop regions from OCR/layout boxes (raster figures)."""

from __future__ import annotations

from itertools import pairwise

_TRIM = "()[]{},.;:'\"“”‘’`´′″‴"
# Glyphs whose upper and lower case share a shape: OCR returns either case, so they
# neither vote on the figure's panel case nor get rejected for the "wrong" case.
CASELESS_LETTERS = frozenset("cosuvwxz")
_CASE_TWINS = {"l": "I", "I": "l"}


def box_center(b: list[float]) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _as_panel_case(t: str, case: str) -> str | None:
    want = str.isupper if case == "upper" else str.islower
    if want(t):
        return t
    if t.lower() in CASELESS_LETTERS:
        return t.upper() if case == "upper" else t.lower()
    twin = _CASE_TWINS.get(t)
    return twin if twin and want(twin) else None


def panel_letter_dets(
    dets: list[dict],
    *,
    case: str = "upper",
) -> list[tuple[str, list[float]]]:
    """Single-letter panel labels from OCR detections (largest box wins per letter)."""
    best: dict[str, list[float]] = {}
    for d in dets:
        for tok in str(d.get("text", "")).split():
            t = tok.strip(_TRIM)
            if len(t) != 1 or not t.isalpha():
                continue
            key = _as_panel_case(t, case)
            if key is None:
                continue
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
    legend key rather than a panel label. A run that starts at A wins over a somewhat
    longer one elsewhere (stray axis / gene-name letters such as S, T, V).
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
    longest = max(runs, key=lambda r: (len(r), -ord(r[0])))
    first = runs[0]
    if first[0] in "aA" and len(first) >= 2 and 2 * len(first) >= len(longest):
        return set(first)
    return set(longest)


def _contains(box, x: float, y: float, pad: float = 0.0) -> bool:
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


def _union(a, b) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _gap(a, b) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def _grow(box: list[float], items: list[tuple[float, list[float]]], others) -> tuple[list[float], bool]:
    grew = False
    for _d, el in sorted(items, key=lambda t: t[0]):
        cand = _union(box, el)
        if any(_contains(cand, x, y) for x, y in others):
            continue
        box = cand
        grew = True
    return box, grew


def _local_grid(area: list[float], centers: dict[str, tuple[float, float]], tol: float) -> dict[str, list[float]]:
    """Cells inside ``area`` for the labels in it: each runs right to the next label in
    its row (else the area edge) and down to the next row (else the area bottom)."""
    out: dict[str, list[float]] = {}
    for k, (cx, cy) in centers.items():
        below = [y for _j, (_x, y) in centers.items() if y > cy + tol]
        right = [x for _j, (x, y) in centers.items() if x > cx + tol and abs(y - cy) <= tol]
        out[k] = [
            max(area[0], cx - tol),
            max(area[1], cy - tol),
            min(right) - tol if right else area[2],
            min(below) - tol if below else area[3],
        ]
    return out


def panel_boxes_from_elements(
    labels: dict[str, list[float]],
    elements: list[list[float]],
    opaque: list[list[float]] | None = None,
) -> dict[str, list[float]]:
    """Panel letter -> box covering the artwork it labels (letters without artwork are absent).

    A label sits at the top-left of its panel, either just outside the artwork or
    printed over its top-left corner. Large elements (photos, plot frames) go to the
    nearest label up-left of them, rows weighing more than columns; small ones (text,
    ticks, genotype headers) join the nearest large element, preferring the one below
    since headers sit above their panel. Elements enclosing two or more labels (page
    backgrounds, one bitmap holding every panel) are ignored, and a panel box never
    grows over another panel's label. ``opaque`` areas hold artwork that could not be
    split per panel; labels inside them without artwork of their own get a letter grid
    cell of that area.
    """
    if not labels:
        return {}
    centers = {k: box_center(b) for k, b in labels.items()}
    heights = sorted(b[3] - b[1] for b in labels.values())
    label_h = heights[len(heights) // 2]
    core_side = 2.0 * label_h
    reach = label_h + 2.0

    def owner(el, columns: dict[str, list[float]] | None = None) -> tuple[float, str] | None:
        """Nearest label up-left of ``el``; with ``columns``, a label that already has
        artwork only owns elements within that artwork's horizontal span."""
        inside = [k for k, (cx, cy) in centers.items() if _contains(el, cx, cy, 1.0)]
        if len(inside) >= 2:
            return None
        if inside:
            return 0.0, inside[0]
        mid_x = (el[0] + el[2]) / 2
        best: tuple[float, str] | None = None
        for k, (cx, cy) in centers.items():
            lb = labels[k]
            if cx > el[0] + 1.5 * (lb[2] - lb[0]) + 2.0 or cy > el[1] + (lb[3] - lb[1]) + 2.0:
                continue
            col = (columns or {}).get(k)
            if col is not None and not col[0] - reach <= mid_x <= col[2] + reach:
                continue
            d = max(0.0, el[0] - cx) ** 2 + (3.0 * max(0.0, el[1] - cy)) ** 2
            if best is None or d < best[0]:
                best = (d, k)
        return best

    core: dict[str, list[tuple[float, list[float]]]] = {k: [] for k in labels}
    small: list[list[float]] = []
    for el in elements:
        if min(el[2] - el[0], el[3] - el[1]) >= core_side:
            hit = owner(el)
            if hit is not None:
                core[hit[1]].append((hit[0], el))
        else:
            small.append(el)

    core_boxes: dict[str, list[float]] = {}
    for k, items in core.items():
        others = [c for j, c in centers.items() if j != k]
        box, grew = _grow(list(labels[k]), items, others)
        if grew:
            core_boxes[k] = box

    extra: dict[str, list[tuple[float, list[float]]]] = {k: [] for k in labels}
    for el in small:
        if sum(_contains(el, cx, cy, 1.0) for cx, cy in centers.values()) >= 2:
            continue
        own = owner(el, core_boxes)
        if own is not None and own[1] not in core_boxes:
            extra[own[1]].append((own[0], el))
            continue
        near: tuple[float, str] | None = None
        for k, box in core_boxes.items():
            g = _gap(el, box)
            if el[3] <= box[1] + 1.0:
                g -= 2.0
            if g <= reach and (near is None or g < near[0]):
                near = (g, k)
        if near is None:
            near = own
        if near is not None:
            extra[near[1]].append((max(near[0], 0.0), el))

    cells: dict[str, list[float]] = {}
    for area in opaque or []:
        beside = [area[0] - 1.5 * label_h, area[1] - 1.5 * label_h, area[2], area[3]]
        inside = {
            k: c for k, c in centers.items() if k not in core_boxes and _contains(beside, *c)
        }
        cells.update(_local_grid(area, inside, label_h))

    out: dict[str, list[float]] = {}
    for k, label_box in labels.items():
        others = [c for j, c in centers.items() if j != k]
        box = core_boxes.get(k, list(label_box))
        if k in cells:
            box = _union(box, cells[k])
        box, grew = _grow(box, extra[k], others)
        if grew or k in core_boxes or k in cells:
            out[k] = box
    return out


def panel_boxes_from_photos(
    labels: dict[str, list[float]],
    photos: list[list[float]],
    ink: list[list[float]] | None = None,
) -> dict[str, list[float]]:
    """Panel boxes for a raster figure from its whitespace-split photos and ink blobs.

    A photo that two or more labels sit on or just beside could not be split per
    panel and becomes an opaque area (letter grid inside it). ``ink`` blobs (text
    lines, plots, diagrams) are elements like vector drawings; the label glyphs
    themselves are dropped. Letters without any artwork are absent so the caller
    keeps its own box for them.
    """
    if not labels or not (photos or ink):
        return {}
    heights = sorted(b[3] - b[1] for b in labels.values())
    label_h = heights[len(heights) // 2]
    margin = 1.5 * label_h
    centers = [box_center(b) for b in labels.values()]
    elements: list[list[float]] = []
    opaque: list[list[float]] = []
    for p in photos:
        n = sum(
            p[0] - margin <= cx <= p[2] and p[1] - margin <= cy <= p[3] for cx, cy in centers
        )
        (opaque if n >= 2 else elements).append(list(p))
    pad = 0.5 * label_h
    for b in ink or []:
        if any(
            lb[0] - pad <= b[0] and lb[1] - pad <= b[1] and b[2] <= lb[2] + pad and b[3] <= lb[3] + pad
            for lb in labels.values()
        ):
            continue
        elements.append(list(b))
    return panel_boxes_from_elements(labels, elements, opaque)


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
