"""PDF ベクター由来のパネル矩形（太字1文字ラベル → 幾何分割 → 切り出し）。

Phase 6A: VLM 単独分割に頼らず、PyMuPDF テキスト bbox から軸平行矩形を作る。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pre_peer_checker.parsers.pdf_panel_plots import _panel_labels


@dataclass(frozen=True)
class PanelRegion:
    """One panel letter and its axis-aligned region on a PDF page (points)."""

    panel: str
    page_index: int
    x0: float
    y0: float
    x1: float
    y1: float
    label_x: float
    label_y: float
    font_size: float = 0.0

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def as_percent(self, page_w: float, page_h: float) -> dict[str, float]:
        if page_w <= 0 or page_h <= 0:
            return {}
        return {
            "left_pct": round(100.0 * self.x0 / page_w, 3),
            "top_pct": round(100.0 * self.y0 / page_h, 3),
            "width_pct": round(100.0 * (self.x1 - self.x0) / page_w, 3),
            "height_pct": round(100.0 * (self.y1 - self.y0) / page_h, 3),
            "label_x_pct": round(100.0 * self.label_x / page_w, 3),
            "label_y_pct": round(100.0 * self.label_y / page_h, 3),
        }

    def to_dict(self, *, page_w: float | None = None, page_h: float | None = None) -> dict[str, Any]:
        d = asdict(self)
        if page_w and page_h:
            d["pct"] = self.as_percent(page_w, page_h)
        return d


def regions_from_labels(
    labels: dict[str, tuple[float, float, float]],
    page_w: float,
    page_h: float,
    *,
    page_index: int = 0,
    pad: float = 6.0,
) -> list[PanelRegion]:
    """Axis-aligned panel regions from label positions (label ≈ top-left of panel).

    Neighbors define cell boundaries: next distinct x / y among labels, else page edge.
    """
    if not labels or page_w <= 0 or page_h <= 0:
        return []
    pts = {L: (float(sz), float(cx), float(cy)) for L, (sz, cx, cy) in labels.items()}
    xs = sorted({cx for _sz, cx, _cy in pts.values()})
    ys = sorted({cy for _sz, _cx, cy in pts.values()})

    def next_after(sorted_vals: list[float], v: float, end: float, tol: float = 10.0) -> float:
        for u in sorted_vals:
            if u > v + tol:
                return u
        return end

    out: list[PanelRegion] = []
    for letter, (sz, cx, cy) in pts.items():
        x0 = max(0.0, cx - pad)
        y0 = max(0.0, cy - pad)
        x1 = next_after(xs, cx, page_w)
        y1 = next_after(ys, cy, page_h)
        min_w = page_w * 0.08
        min_h = page_h * 0.08
        if x1 - x0 < min_w:
            x1 = min(page_w, x0 + min_w)
        if y1 - y0 < min_h:
            y1 = min(page_h, y0 + min_h)
        out.append(
            PanelRegion(
                panel=letter.upper(),
                page_index=page_index,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                label_x=cx,
                label_y=cy,
                font_size=sz,
            )
        )
    return sorted(out, key=lambda r: r.panel)


def _label_box(size: float, cx: float, cy: float) -> list[float]:
    return [cx - 0.35 * size, cy - 0.5 * size, cx + 0.35 * size, cy + 0.5 * size]


_SPLIT_DPI = 200
_SPLIT_PAD = 10  # white margin so a bitmap whose photos touch its edge still has a background


def _split_bitmap(page, bbox) -> list[list[float]]:
    """Photo panels inside one placed bitmap (whitespace XY-cut), in page points."""
    import fitz
    from PIL import Image, ImageOps

    from pre_peer_checker.imaging.panel_split import split_panels

    clip = fitz.Rect(bbox) & page.rect
    if clip.is_empty:
        return []
    pix = page.get_pixmap(dpi=_SPLIT_DPI, clip=clip, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    img = ImageOps.expand(img, border=_SPLIT_PAD, fill="white")
    sx, sy = clip.width / pix.width, clip.height / pix.height
    p = _SPLIT_PAD
    return [
        [
            clip.x0 + (b.left - p) * sx,
            clip.y0 + (b.top - p) * sy,
            clip.x0 + (b.right - p) * sx,
            clip.y0 + (b.bottom - p) * sy,
        ]
        for b in split_panels(img)
    ]


def _page_elements(
    page, labels: dict[str, tuple[float, float, float]]
) -> tuple[list[list[float]], list[list[float]]]:
    """Artwork rects on a figure page: images, vector paths, and text lines other than labels.

    A bitmap that holds several labelled panels is replaced by its photo panels; the
    parts that still hold several labels are returned separately as opaque areas.
    """
    rect = page.rect
    page_area = float(rect.width * rect.height)
    out: list[list[float]] = []
    opaque: list[list[float]] = []
    label_pts = [(cx, cy, sz) for sz, cx, cy in labels.values()]

    def n_labels(b) -> int:
        """Labels on or just outside ``b`` (a label may sit beside its photo)."""
        return sum(
            b[0] - 1.5 * sz <= cx <= b[2] and b[1] - 1.5 * sz <= cy <= b[3]
            for cx, cy, sz in label_pts
        )

    def add(r) -> None:
        x0, y0 = max(r[0], rect.x0), max(r[1], rect.y0)
        x1, y1 = min(r[2], rect.x1), min(r[3], rect.y1)
        if x1 < x0 or y1 < y0:
            return
        if (x1 - x0) * (y1 - y0) >= 0.6 * page_area:
            return
        out.append([float(x0), float(y0), float(max(x1, x0 + 0.5)), float(max(y1, y0 + 0.5))])

    for info in page.get_image_info():
        b = list(info["bbox"])
        if n_labels(b) < 2:
            add(b)
            continue
        subs = _split_bitmap(page, b)
        for sub in subs:
            if n_labels(sub) >= 2:
                opaque.append(sub)
            else:
                add(sub)
        if not subs:
            opaque.append(b)
    for d in page.get_drawings():
        add(tuple(d["rect"]))
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
            if not text:
                continue
            b = line["bbox"]
            mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if len(text) == 1 and any(
                abs(mx - cx) <= 0.6 * sz and abs(my - cy) <= 0.6 * sz for cx, cy, sz in label_pts
            ):
                continue
            add(b)
    return out, opaque


def regions_from_page_elements(
    page,
    labels: dict[str, tuple[float, float, float]],
    *,
    page_index: int = 0,
) -> list[PanelRegion]:
    """Panel regions fitted to the artwork each label owns; label-grid boxes as fallback.

    Handles labels printed just outside the artwork and labels overlaid on it.
    """
    from pre_peer_checker.parsers.figure_panel_layout import panel_boxes_from_elements

    rect = page.rect
    grid = {
        r.panel: r
        for r in regions_from_labels(
            labels, float(rect.width), float(rect.height), page_index=page_index
        )
    }
    if not labels:
        return []
    elements, opaque = _page_elements(page, labels)
    boxes = panel_boxes_from_elements(
        {k.upper(): _label_box(*v) for k, v in labels.items()}, elements, opaque
    )
    out: list[PanelRegion] = []
    for letter, (sz, cx, cy) in labels.items():
        key = letter.upper()
        box = boxes.get(key)
        if box is None:
            if key in grid:
                out.append(grid[key])
            continue
        pad = 1.5
        out.append(
            PanelRegion(
                panel=key,
                page_index=page_index,
                x0=max(0.0, box[0] - pad),
                y0=max(0.0, box[1] - pad),
                x1=min(float(rect.width), box[2] + pad),
                y1=min(float(rect.height), box[3] + pad),
                label_x=cx,
                label_y=cy,
                font_size=sz,
            )
        )
    return sorted(out, key=lambda r: r.panel)


def extract_panel_regions_from_page(
    page,
    *,
    page_index: int = 0,
    min_label_size: float = 10.0,
) -> list[PanelRegion]:
    """Detect panel letters on a page and fit each region to the artwork it labels."""
    labels = _panel_labels(page, min_size=min_label_size)
    return regions_from_page_elements(page, labels, page_index=page_index)


def crop_panel_png_bytes(
    page,
    region: PanelRegion,
    *,
    zoom: float = 2.0,
) -> bytes:
    """Rasterize one panel region to PNG bytes."""
    import fitz

    clip = fitz.Rect(region.x0, region.y0, region.x1, region.y1)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    return pix.tobytes("png")


def extract_panel_regions_from_pdf(
    path: Path | str,
    *,
    max_pages: int = 8,
) -> list[dict[str, Any]]:
    """Return serializable panel regions for a multipanel figure PDF."""
    import fitz

    path = Path(path)
    doc = fitz.open(path)
    out: list[dict[str, Any]] = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            rect = page.rect
            for reg in extract_panel_regions_from_page(page, page_index=i):
                d = reg.to_dict(page_w=float(rect.width), page_h=float(rect.height))
                d["source"] = str(path)
                d["source_name"] = path.name
                out.append(d)
    finally:
        doc.close()
    return out


def write_panel_crops(
    path: Path | str,
    out_dir: Path | str,
    *,
    max_pages: int = 4,
    zoom: float = 2.0,
) -> list[dict[str, Any]]:
    """Crop each panel region to ``out_dir/{stem}_p{page}_{letter}.png``."""
    import fitz

    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(path)
    written: list[dict[str, Any]] = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            for reg in extract_panel_regions_from_page(page, page_index=i):
                png = crop_panel_png_bytes(page, reg, zoom=zoom)
                dest = out_dir / f"{path.stem}_p{i}_{reg.panel}.png"
                dest.write_bytes(png)
                written.append(
                    {
                        "panel": reg.panel,
                        "page_index": i,
                        "path": str(dest),
                        "bbox": list(reg.bbox),
                        "source": str(path),
                    }
                )
    finally:
        doc.close()
    return written
