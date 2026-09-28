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


def extract_panel_regions_from_page(
    page,
    *,
    page_index: int = 0,
    min_label_size: float = 10.0,
) -> list[PanelRegion]:
    """Detect uppercase panel letters on a page and return geometric regions."""
    labels = _panel_labels(page, min_size=min_label_size)
    rect = page.rect
    return regions_from_labels(
        labels,
        float(rect.width),
        float(rect.height),
        page_index=page_index,
    )


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
