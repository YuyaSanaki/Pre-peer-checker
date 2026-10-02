"""Per-panel mark reading on publication figure pages: what each panel draws.

Complements the filled-marker reader in ``pdf_panel_plots``:

- hollow (stroke-only) markers, bars, error bars and boxes in vector drawings;
- dots inside raster panels (difference-of-Gaussians + connected components);
- a lower bound when marks overlap (merged blobs / touching markers);
- a panel kind (plot / image / blot / survival / heatmap / flow) from the marks.

Everything here is reference information for the report: visible marks undercount
overlapping jitter, so counts never feed the n mismatch flag.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import fitz
import numpy as np

PANEL_KINDS = ("plot", "image", "blot", "survival", "heatmap", "flow", "unknown")
# kinds that show pictures, not plotted samples: their legend n is not a data-row count
PICTURE_KINDS = frozenset({"image", "blot"})

_MAX_MARK = 15.0  # pt; larger closed shapes are bars / boxes / frames
_MIN_MARK = 1.2
_RASTER_ZOOM = 4.0
_FLOW_DOTS = 400


@dataclass
class PanelMarks:
    panel: str
    rect: tuple[float, float, float, float]
    kind: str = "unknown"
    dots: list[tuple[float, float]] = field(default_factory=list)
    hollow: int = 0
    bars: int = 0
    errorbars: int = 0
    boxes: int = 0
    steps: int = 0
    tiles: int = 0
    raster_cover: float = 0.0
    source: str = "vector"  # vector | raster
    n_lower_bound: bool = False
    n_estimate: int = 0  # dots + extra marks hidden in merged blobs (raster)

    def to_dict(self) -> dict:
        return {
            "panel": self.panel,
            "kind": self.kind,
            "rect": [round(v, 1) for v in self.rect],
            "n_dots": len(self.dots),
            "n_estimate": self.n_estimate,
            "n_lower_bound": self.n_lower_bound,
            "hollow": self.hollow,
            "bars": self.bars,
            "errorbars": self.errorbars,
            "boxes": self.boxes,
            "steps": self.steps,
            "tiles": self.tiles,
            "raster_cover": round(self.raster_cover, 2),
            "source": self.source,
        }


# --------------------------------------------------------------------------- panel regions


def panel_rects(
    labels: dict[str, tuple[float, float, float]], page_rect: fitz.Rect, *, bottom: float | None = None
) -> dict[str, fitz.Rect]:
    """Region of each panel: from its label to the next label right (same row) / below.

    ``bottom`` (the caption head) closes the last row instead of the page edge.
    """
    if bottom is not None and bottom < page_rect.y1:
        page_rect = fitz.Rect(page_rect.x0, page_rect.y0, page_rect.x1, bottom)
    items = sorted(labels.items(), key=lambda kv: (kv[1][2], kv[1][1]))
    out: dict[str, fitz.Rect] = {}
    for letter, (size, cx, cy) in items:
        x0, y0 = cx - max(size, 6.0), cy - max(size, 6.0)
        row_tol = max(size * 2.5, 20.0)
        right = [ox - osz for _l, (osz, ox, oy) in items if abs(oy - cy) < row_tol and ox > cx + 5]
        x1 = min(right) if right else page_rect.x1
        below = [
            oy - osz
            for _l, (osz, ox, oy) in items
            if oy > cy + row_tol and ox < x1 - 5 and ox > x0 - 40
        ]
        y1 = min(below) if below else page_rect.y1
        r = fitz.Rect(max(x0, page_rect.x0), max(y0, page_rect.y0), x1, y1)
        if r.width > 10 and r.height > 10:
            out[letter] = r
    return out


# --------------------------------------------------------------------------- vector shapes


def _pts(d: dict) -> list[tuple[float, float]]:
    out = []
    for it in d.get("items") or []:
        for p in it[1:]:
            if hasattr(p, "x"):
                out.append((float(p.x), float(p.y)))
            elif isinstance(p, fitz.Rect):
                out += [(p.x0, p.y0), (p.x1, p.y1)]
    # consecutive segments share an endpoint
    return [p for i, p in enumerate(out) if i == 0 or abs(p[0] - out[i - 1][0]) + abs(p[1] - out[i - 1][1]) > 1e-3]


def _is_white(c) -> bool:
    return c is not None and all(v > 0.97 for v in c)


@dataclass
class _Shape:
    kind: str  # dot | hollow | bar | vline | hline | box | step | curve | tile
    bbox: fitz.Rect
    fill: tuple | None = None
    start: tuple[float, float] | None = None


def _monotone(pts: list[tuple[float, float]]) -> bool:
    """x never goes back and y moves one way only (survival / cumulative incidence)."""
    if len(pts) < 3 or any(b[0] < a[0] - 0.3 for a, b in zip(pts, pts[1:])):
        return False
    dys = [b[1] - a[1] for a, b in zip(pts, pts[1:])]
    return all(dy >= -0.3 for dy in dys) or all(dy <= 0.3 for dy in dys)


def _survival_paths(shapes: list["_Shape"]) -> int:
    """Strict step paths, or >= 2 monotone curves leaving the same origin (time 0, 100 %)."""
    steps = sum(1 for s in shapes if s.kind == "step")
    curves = [s.start for s in shapes if s.kind == "curve" and s.start]
    shared = max(
        (sum(1 for q in curves if abs(q[0] - p[0]) < 1.0 and abs(q[1] - p[1]) < 1.0) for p in curves),
        default=0,
    )
    return steps + (shared if shared >= 2 else 0)


def classify_drawings(drawings: list[dict]) -> list[_Shape]:
    """Vector drawings as plot marks (path geometry only; no text)."""
    shapes: list[_Shape] = []
    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        r = fitz.Rect(rect)
        w, h = r.width, r.height
        fill, stroke = d.get("fill"), d.get("color")
        items = d.get("items") or []
        ops = [it[0] for it in items if it]
        has_fill = fill is not None and not _is_white(fill)
        small = w <= _MAX_MARK and h <= _MAX_MARK
        # outlined text glyphs are small filled paths too: markers are isotropic and simple
        if small and has_fill and max(w, h) >= _MIN_MARK * 0.5:
            if 0.6 <= (w / h if h else 0) <= 1.67 and len(ops) <= 12:
                shapes.append(_Shape("dot", r, tuple(fill)))
            continue
        if small and not has_fill and stroke is not None and max(w, h) >= _MIN_MARK:
            closed = d.get("closePath") or ops.count("c") >= 3 or "re" in ops or ops.count("l") >= 3
            if closed and 0.5 <= (w / h if h else 0) <= 2.0:
                shapes.append(_Shape("hollow", r))
                continue
        if ops and all(o == "l" for o in ops):
            if len(ops) == 1:
                if w < 0.6 and h >= 3:
                    shapes.append(_Shape("vline", r))
                elif h < 0.6 and w >= 1.5:
                    shapes.append(_Shape("hline", r))
                continue
            if len(ops) >= 6:
                pts = _pts(d)
                if _monotone(pts):
                    turns = sum(
                        1
                        for (ax, ay), (bx, by) in zip(pts, pts[1:])
                        if (abs(ax - bx) < 0.3) != (abs(ay - by) < 0.3)
                    )
                    if turns >= 6 and turns >= 0.8 * (len(pts) - 1):
                        shapes.append(_Shape("step", r, start=pts[0]))
                        continue
                    if len(pts) >= 8 and abs(pts[1][1] - pts[0][1]) < 0.3 and pts[1][0] - pts[0][0] > 1.0:
                        shapes.append(_Shape("curve", r, start=pts[0]))
                        continue
        if "re" in ops or (len(ops) == 4 and all(o == "l" for o in ops)):
            if has_fill and 2.0 <= w <= 60 and h > max(_MAX_MARK, 1.5 * w) * 0.5:
                shapes.append(_Shape("bar", r, tuple(fill)))
            elif has_fill and 1.0 <= w <= 40 and 1.0 <= h <= 40:
                shapes.append(_Shape("tile", r, tuple(fill)))
            elif not has_fill and stroke is not None and 3 <= w <= 60 and h >= 3:
                shapes.append(_Shape("box", r))
    return shapes


def _count_errorbars(vlines: list[fitz.Rect], hlines: list[fitz.Rect]) -> int:
    """A vertical whisker with a short horizontal cap at either end."""
    n = 0
    for v in vlines:
        cx = (v.x0 + v.x1) / 2
        for y in (v.y0, v.y1):
            if any(abs((h.y0 + h.y1) / 2 - y) < 0.8 and h.x0 - 0.5 <= cx <= h.x1 + 0.5 and h.width <= 12 for h in hlines):
                n += 1
                break
    return n


def _count_boxes(boxes: list[fitz.Rect], hlines: list[fitz.Rect]) -> int:
    """A stroked box with a median line spanning it."""
    return sum(
        1
        for b in boxes
        if any(b.y0 + 0.5 < (h.y0 + h.y1) / 2 < b.y1 - 0.5 and abs(h.width - b.width) < 1.5 for h in hlines)
    )


def _colour_lattice(dots: list[_Shape]) -> bool:
    """Small filled squares on a full row x column grid in many colours: heatmap cells."""
    if len(dots) < 16:
        return False
    xs = {round((s.bbox.x0 + s.bbox.x1) / 2, 0) for s in dots}
    ys = {round((s.bbox.y0 + s.bbox.y1) / 2, 0) for s in dots}
    colours = {tuple(round(c, 2) for c in s.fill) for s in dots if s.fill}
    return len(xs) * len(ys) <= 1.5 * len(dots) and len(colours) >= 6


def _heatmap_tiles(tiles: list[_Shape]) -> int:
    """Tiles of one size in a grid with varied fills."""
    if len(tiles) < 30:
        return 0
    sizes: dict[tuple[float, float], list[_Shape]] = {}
    for t in tiles:
        sizes.setdefault((round(t.bbox.width, 0), round(t.bbox.height, 0)), []).append(t)
    best = max(sizes.values(), key=len)
    fills = {tuple(round(c, 2) for c in t.fill or ()) for t in best}
    return len(best) if len(best) >= 30 and len(fills) >= 5 else 0


# --------------------------------------------------------------------------- raster dots


def raster_dot_centroids(
    gray: np.ndarray, *, min_area: int = 12, max_area: int = 900
) -> tuple[list[tuple[float, float]], int, bool]:
    """Dark round marks in a grayscale image (0 = black): (centroids px, estimate, overlapped).

    Dark components (holes filled, so hollow markers count) are kept when round and
    compact; lines fail the aspect test and large fills the area cap. A component several
    times the median mark area counts as that many overlapping marks
    (``estimate`` > len(centroids)).
    """
    import cv2
    from scipy.ndimage import binary_fill_holes

    if gray.size == 0:
        return [], 0, False
    inv = 255.0 - gray.astype(np.float32)
    mask = binary_fill_holes(inv > 100).astype(np.uint8)
    n_lab, _lab, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    comps = []
    for i in range(1, n_lab):
        x, y, w, h, area = stats[i]
        if area < min_area or area > max_area * 4:
            continue
        aspect = max(w, h) / max(1, min(w, h))
        fill = area / float(w * h)
        if aspect > 2.5 or fill < 0.45:
            continue
        comps.append((float(cents[i][0]), float(cents[i][1]), int(area)))
    if not comps:
        return [], 0, False
    single = [a for *_c, a in comps if a <= max_area]
    med = float(np.median(single)) if single else float(np.median([a for *_c, a in comps]))
    estimate, overlapped = 0, False
    for *_c, a in comps:
        k = max(1, int(round(a / med))) if med > 0 else 1
        if k > 1 and a > 1.6 * med:
            overlapped = True
        estimate += k
    return [(cx, cy) for cx, cy, _a in comps], estimate, overlapped


def _image_boxes(page: fitz.Page) -> list[fitz.Rect]:
    try:
        return [fitz.Rect(i.get("bbox") or (0, 0, 0, 0)) for i in page.get_image_info()]
    except Exception:
        return []


def _raster_cover(boxes: list[fitz.Rect], rect: fitz.Rect) -> float:
    cover = 0.0
    for b in boxes:
        inter = fitz.Rect(b) & rect
        if not inter.is_empty:
            cover += inter.width * inter.height
    area = rect.width * rect.height
    return min(1.0, cover / area) if area > 0 else 0.0


def _render(page: fitz.Page, rect: fitz.Rect) -> np.ndarray:
    pix = page.get_pixmap(matrix=fitz.Matrix(_RASTER_ZOOM, _RASTER_ZOOM), clip=rect, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return arr[:, :, :3]


def _image_mask(shape: tuple[int, int], images: list[fitz.Rect], rect: fitz.Rect) -> np.ndarray:
    """Rendered pixels of ``rect`` that belong to a raster image (not page text / vectors)."""
    hgt, wid = shape
    mask = np.zeros((hgt, wid), bool)
    for b in images:
        inter = fitz.Rect(b) & rect
        if inter.is_empty:
            continue
        x0 = int((inter.x0 - rect.x0) * _RASTER_ZOOM)
        y0 = int((inter.y0 - rect.y0) * _RASTER_ZOOM)
        x1 = int(np.ceil((inter.x1 - rect.x0) * _RASTER_ZOOM))
        y1 = int(np.ceil((inter.y1 - rect.y0) * _RASTER_ZOOM))
        mask[max(0, y0) : min(hgt, y1), max(0, x0) : min(wid, x1)] = True
    return mask


def _paper_fraction(rgb: np.ndarray, mask: np.ndarray) -> float:
    """Share of near-white, unsaturated pixels over the raster image pixels."""
    if rgb.size == 0 or not mask.any():
        return 0.0
    px = rgb[mask].astype(int)
    sat = px.max(axis=1) - px.min(axis=1)
    return float(((px.mean(axis=1) > 220) & (sat < 15)).mean())


def _picture_kind(rgb: np.ndarray, rect: fitz.Rect) -> str:
    """Raster panel without plot marks: blot (grey, wide, light background) or image."""
    if rgb.size == 0:
        return "image"
    sat = rgb.max(axis=2).astype(int) - rgb.min(axis=2).astype(int)
    grey = float((sat < 18).mean())
    light = float((rgb.mean(axis=2) > 200).mean())
    if grey > 0.9 and rect.width / max(rect.height, 1) >= 1.6 and light > 0.35:
        return "blot"
    return "image"


# --------------------------------------------------------------------------- per panel


def read_panel_marks(
    page: fitz.Page,
    labels: dict[str, tuple[float, float, float]],
    *,
    raster: bool = True,
) -> dict[str, PanelMarks]:
    from pre_peer_checker.parsers.pdf_panel_plots import caption_top

    rects = panel_rects(labels, page.rect, bottom=caption_top(page))
    images = _image_boxes(page)
    # marks drawn over a photo are arrows / scale bars / annotations, not plotted samples
    shapes = [
        s
        for s in classify_drawings(page.get_drawings())
        if not any(b.contains(fitz.Point((s.bbox.x0 + s.bbox.x1) / 2, (s.bbox.y0 + s.bbox.y1) / 2)) for b in images)
    ]
    out: dict[str, PanelMarks] = {}
    for letter, rect in rects.items():
        inside = [s for s in shapes if rect.contains(fitz.Point((s.bbox.x0 + s.bbox.x1) / 2, (s.bbox.y0 + s.bbox.y1) / 2))]
        dot_shapes = [s for s in inside if s.kind == "dot"]
        if _colour_lattice(dot_shapes):
            inside = [s if s.kind != "dot" else _Shape("tile", s.bbox, s.fill) for s in inside]
        by = {k: [s.bbox for s in inside if s.kind == k] for k in ("dot", "hollow", "bar", "vline", "hline", "box", "step")}
        tiles = [s for s in inside if s.kind == "tile"]
        pm = PanelMarks(panel=letter, rect=tuple(rect))
        pm.dots = [((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2) for b in by["dot"] + by["hollow"]]
        pm.hollow = len(by["hollow"])
        pm.bars = len(by["bar"])
        pm.errorbars = _count_errorbars(by["vline"], by["hline"])
        pm.boxes = _count_boxes(by["box"], by["hline"])
        pm.steps = _survival_paths(inside)
        pm.tiles = _heatmap_tiles(tiles)
        pm.n_estimate = len(pm.dots)
        pm.n_lower_bound = _vector_overlap(by["dot"] + by["hollow"])
        pm.raster_cover = _raster_cover(images, rect)
        vector_marks = len(pm.dots) + pm.bars + pm.boxes + pm.steps + pm.tiles
        rgb = None
        if raster and pm.raster_cover >= 0.3 and vector_marks < 3:
            rgb = _render(page, rect)
            in_image = _image_mask(rgb.shape[:2], images, rect)
            # page text (panel letter, tick labels) is not a marker
            gray = np.where(in_image, rgb.mean(axis=2), 255.0)
            # dots are read only when the raster itself is plot paper (near-white, unsaturated);
            # photos and stained sections give texture blobs
            if _paper_fraction(rgb, in_image) > 0.6:
                cents, est, overlapped = raster_dot_centroids(gray)
                if len(cents) >= 3:
                    pm.dots = [(rect.x0 + cx / _RASTER_ZOOM, rect.y0 + cy / _RASTER_ZOOM) for cx, cy in cents]
                    pm.n_estimate = est
                    pm.n_lower_bound = overlapped
                    pm.source = "raster"
        pm.kind = _kind(pm, rgb, rect)
        out[letter] = pm
    return out


def _vector_overlap(boxes: list[fitz.Rect]) -> bool:
    """Touching markers hide each other: the visible count is then a minimum."""
    if len(boxes) < 2:
        return False
    cs = [((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, max(b.width, b.height)) for b in boxes]
    for i, (ax, ay, ad) in enumerate(cs):
        for bx, by, bd in cs[i + 1 :]:
            if math.hypot(ax - bx, ay - by) < 0.5 * min(ad, bd):
                return True
    return False


def _kind(pm: PanelMarks, rgb: np.ndarray | None, rect: fitz.Rect) -> str:
    if pm.tiles:
        return "heatmap"
    if pm.steps:
        return "survival"
    if len(pm.dots) >= _FLOW_DOTS:
        return "flow"
    if len(pm.dots) >= 3 or pm.bars or pm.boxes or pm.errorbars:
        return "plot"
    if pm.raster_cover >= 0.3:
        return _picture_kind(rgb if rgb is not None else np.zeros((0, 0, 3), np.uint8), rect)
    return "unknown"
