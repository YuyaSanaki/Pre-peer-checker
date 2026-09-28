"""ggplot 風ベクトル PDF から散布点を数字化する。

R の `ggsave` / `Rplot*.pdf` など、軸目盛りテキストと小さな塗りつぶし
マーカー（cubic 曲線）で構成された単一パネル PDF を対象とする。
ラスタのみの出版 Figure は別経路（画像類似）に委ねる。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import numpy as np


@dataclass(frozen=True)
class DigitizedGroup:
    """1 群分の数字化値（ソート済み）。"""

    label: str
    values: tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.values)


@dataclass
class DigitizedPlot:
    path: Path
    groups: list[DigitizedGroup] = field(default_factory=list)
    page_index: int = 0


def _axis_ticks(page: fitz.Page) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Return (y_ticks, x_ticks) as (pdf_coord, numeric_value)."""
    y_ticks: list[tuple[float, float]] = []
    x_ticks: list[tuple[float, float]] = []
    words = page.get_text("words")
    if not words:
        return y_ticks, x_ticks
    page_w = float(page.rect.width)
    for w in words:
        x0, y0, x1, y1, text, *_ = w
        try:
            val = float(text)
        except ValueError:
            continue
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        # Left-axis labels sit near the left margin.
        if cx < min(60.0, page_w * 0.25):
            y_ticks.append((cy, val))
        else:
            x_ticks.append((cx, val))
    return y_ticks, x_ticks


def _marker_centroids(page: fitz.Page, *, max_span: float = 15.0) -> list[tuple[float, float]]:
    """Extract centroids of small filled cubic markers (ggplot geom_point)."""
    pts: list[tuple[float, float]] = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if not fill or fill == (1.0, 1.0, 1.0):
            continue
        items = [it for it in (d.get("items") or []) if it and it[0] == "c"]
        if len(items) < 3:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for it in items:
            for j in range(1, 5):
                pt = it[j]
                if hasattr(pt, "x"):
                    xs.append(float(pt.x))
                    ys.append(float(pt.y))
        if not xs:
            continue
        if max(xs) - min(xs) < max_span and max(ys) - min(ys) < max_span:
            pts.append((sum(xs) / len(xs), sum(ys) / len(ys)))
    return pts


def _fit_linear(pdf_coords: np.ndarray, values: np.ndarray) -> tuple[float, float] | None:
    if len(pdf_coords) < 2:
        return None
    A = np.vstack([pdf_coords, np.ones_like(pdf_coords)]).T
    try:
        a, b = np.linalg.lstsq(A, values, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    return float(a), float(b)


def digitize_ggplot_page(page: fitz.Page, *, path: Path, page_index: int = 0) -> DigitizedPlot | None:
    y_ticks, x_ticks = _axis_ticks(page)
    markers = _marker_centroids(page)
    if len(y_ticks) < 2 or len(x_ticks) < 1 or len(markers) < 3:
        return None

    y_pdf = np.array([t[0] for t in y_ticks], dtype=float)
    y_val = np.array([t[1] for t in y_ticks], dtype=float)
    fit = _fit_linear(y_pdf, y_val)
    if fit is None:
        return None
    a, b = fit

    groups: dict[float, list[float]] = defaultdict(list)
    for px, py in markers:
        nearest = min(x_ticks, key=lambda t: abs(px - t[0]))
        groups[nearest[1]].append(float(a * py + b))

    digitized = [
        DigitizedGroup(
            label=str(int(g)) if float(g).is_integer() else str(g),
            values=tuple(sorted(round(v, 6) for v in vals)),
        )
        for g, vals in sorted(groups.items())
        if len(vals) >= 2
    ]
    if not digitized:
        return None
    return DigitizedPlot(path=path, groups=digitized, page_index=page_index)


def digitize_plot_pdf(path: Path | str, *, max_pages: int = 1) -> list[DigitizedPlot]:
    """Digitize ggplot-like pages from a PDF. Returns one DigitizedPlot per usable page."""
    path = Path(path)
    doc = fitz.open(path)
    out: list[DigitizedPlot] = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            plot = digitize_ggplot_page(page, path=path, page_index=i)
            if plot is not None:
                out.append(plot)
    finally:
        doc.close()
    return out


def find_plot_pdfs(root: Path | str) -> list[Path]:
    """Locate Rplot / graph residue PDFs likely to be single-panel ggplot exports."""
    root = Path(root)
    found: list[Path] = []
    for p in root.rglob("*.pdf"):
        name = p.name.lower()
        if name.startswith("rplot") or name.startswith("graph"):
            found.append(p)
    # Prefer smaller single-panel exports first (publication multi-panel last)
    found.sort(key=lambda p: (p.stat().st_size, str(p)))
    return found
