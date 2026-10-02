"""出版 Figure PDF の多パネル点列抽出（パネルラベル＋ggplot マーカー）。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import fitz
import numpy as np

from pre_peer_checker.parsers.figure_panel_layout import dominant_panel_run


@dataclass(frozen=True)
class PanelPointGroup:
    """1 パネル内の 1 群（x クラスター）分の PDF-y 座標（ソート済み）。"""

    x_bin: float
    ys: tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.ys)


@dataclass
class PanelPlot:
    panel: str
    page_index: int
    label_xy: tuple[float, float]
    groups: list[PanelPointGroup] = field(default_factory=list)
    n_markers: int = 0
    kind: str = "unknown"
    # ``vector`` filled markers feed identity checks; ``hollow`` / ``raster`` are display only
    mark_source: str = "vector"
    n_lower_bound: bool = False
    marks: dict = field(default_factory=dict)

    @property
    def identity_groups(self) -> list[PanelPointGroup]:
        return self.groups if self.mark_source == "vector" else []


@dataclass
class FigurePagePlots:
    path: Path
    page_index: int
    figure_label: str | None
    panels: list[PanelPlot] = field(default_factory=list)


_BOLD_FONT_RE = re.compile(r"bold|black|heavy|blk", re.IGNORECASE)
_MIN_BOLD_LABEL_SIZE = 6.0


def _is_bold(span: dict) -> bool:
    return bool(int(span.get("flags", 0)) & 16) or bool(_BOLD_FONT_RE.search(span.get("font", "")))


def _bold_panel_labels(spans: list[tuple[str, float, float, float, bool]]) -> dict[str, tuple[float, float, float]]:
    """Journal-style labels (bold, often lowercase, ~7–8 pt): one size, one letter run."""
    bold = [(t.upper(), size, cx, cy) for t, size, cx, cy, is_bold in spans if is_bold and size >= _MIN_BOLD_LABEL_SIZE]
    if not bold:
        return {}
    sizes = Counter(round(size * 2) / 2 for _t, size, _x, _y in bold)
    mode = sizes.most_common(1)[0][0]
    best: dict[str, tuple[float, float, float]] = {}
    for letter, size, cx, cy in bold:
        if abs(size - mode) > 0.5:
            continue
        prev = best.get(letter)
        if prev is None or size > prev[0]:
            best[letter] = (size, cx, cy)
    run = dominant_panel_run(best)
    if len(run) < 2:
        return {}
    return {k: v for k, v in best.items() if k in run}


_CAPTION_HEAD_RE = re.compile(r"^\s*(?:Extended\s+Data\s+|Supplementary\s+)?Fig(?:ure)?\.?\s*S?\d+\s*[|.:]", re.I)


def caption_top(page: fitz.Page) -> float:
    """Top of the figure caption on the page (``Fig. 2 |`` / ``Figure 2.``); page bottom if none.

    Bold section letters of the caption (``a, … b, …``) are not panel labels.
    """
    best = float(page.rect.y1)
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        block_len = sum(len(s.get("text", "")) for ln in block.get("lines", []) for s in ln.get("spans", []))
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", []))
            if _CAPTION_HEAD_RE.match(text):
                y0 = float(line["bbox"][1])
                # a short head near the very top is a running header; a long block is the caption
                if y0 > page.rect.y0 + 0.15 * page.rect.height or block_len > 150:
                    best = min(best, y0)
    return best


def _panel_labels(page: fitz.Page, *, min_size: float = 10.0) -> dict[str, tuple[float, float, float]]:
    """Map panel letter -> (font_size, cx, cy), keeping the largest span per letter.

    Large uppercase letters are preferred; otherwise bold single letters of a common
    size (lowercase journal labels) are used, keyed in uppercase. Letters at or below
    the caption head are skipped.
    """
    best: dict[str, tuple[float, float, float]] = {}
    singles: list[tuple[str, float, float, float, bool]] = []
    bottom = caption_top(page)
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if len(text) != 1 or not text.isascii() or not text.isalpha():
                    continue
                size = float(span.get("size", 0))
                bbox = span["bbox"]
                cx = (bbox[0] + bbox[2]) / 2.0
                cy = (bbox[1] + bbox[3]) / 2.0
                if cy >= bottom:
                    continue
                singles.append((text, size, cx, cy, _is_bold(span)))
                if not text.isupper() or size < min_size:
                    continue
                prev = best.get(text)
                if prev is None or size > prev[0]:
                    best[text] = (size, cx, cy)
    if len(best) >= 2:
        return best
    return _bold_panel_labels(singles) or best


def _marker_centroids(page: fitz.Page, *, max_span: float = 15.0) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if not fill or fill == (1.0, 1.0, 1.0):
            continue
        # Small filled rect / ellipse (synthetic PDFs and some exporters)
        rect = d.get("rect")
        if rect is not None:
            x0, y0, x1, y1 = rect
            w, h = float(x1 - x0), float(y1 - y0)
            if 0 < w <= max_span and 0 < h <= max_span:
                pts.append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
                continue
        items = [it for it in (d.get("items") or []) if it and it[0] == "c"]
        if len(items) < 3:
            # also accept short path lists with move/line forming a tiny blob
            items_all = list(d.get("items") or [])
            xs: list[float] = []
            ys: list[float] = []
            for it in items_all:
                if not it:
                    continue
                for j in range(1, len(it)):
                    pt = it[j]
                    if hasattr(pt, "x"):
                        xs.append(float(pt.x))
                        ys.append(float(pt.y))
            if xs and max(xs) - min(xs) < max_span and max(ys) - min(ys) < max_span:
                pts.append((sum(xs) / len(xs), sum(ys) / len(ys)))
            continue
        xs = []
        ys = []
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


def _figure_label(page: fitz.Page) -> str | None:
    text = page.get_text("text")
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("figure ") or s.lower().startswith("fig."):
            return s[:40]
    return None


def _markers_near(
    label_x: float,
    label_y: float,
    markers: list[tuple[float, float]],
    *,
    x_max: float = 140.0,
    y_max: float = 150.0,
) -> list[tuple[float, float]]:
    return [
        (mx, my)
        for mx, my in markers
        if label_x - 15 <= mx <= label_x + x_max and label_y - 10 <= my <= label_y + y_max
    ]


def _group_ys(points: list[tuple[float, float]], *, bin_size: float = 10.0) -> list[PanelPointGroup]:
    if not points:
        return []
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    bins = np.round(xs / bin_size) * bin_size
    groups: dict[float, list[float]] = {}
    for bx, yy in zip(bins, ys):
        groups.setdefault(float(bx), []).append(float(yy))
    return [
        PanelPointGroup(x_bin=k, ys=tuple(sorted(v)))
        for k, v in sorted(groups.items())
        if len(v) >= 3
    ]


def extract_figure_page_plots(
    page: fitz.Page,
    *,
    path: Path,
    page_index: int,
) -> FigurePagePlots:
    from pre_peer_checker.parsers.plot_marks import read_panel_marks

    labels = _panel_labels(page)
    markers = _marker_centroids(page)
    try:
        marks = read_panel_marks(page, labels) if len(labels) >= 2 else {}
    except Exception:  # noqa: BLE001
        marks = {}
    panels: list[PanelPlot] = []
    for letter, (_size, lx, ly) in sorted(labels.items()):
        near = _markers_near(lx, ly, markers)
        groups = _group_ys(near)
        pm = marks.get(letter)
        source = "vector"
        if not groups and pm is not None and pm.dots and (pm.hollow or pm.source == "raster"):
            groups = _group_ys(pm.dots)
            near = pm.dots
            source = "raster" if pm.source == "raster" else "hollow"
        if not groups and pm is None:
            continue
        panels.append(
            PanelPlot(
                panel=letter,
                page_index=page_index,
                label_xy=(lx, ly),
                groups=groups,
                n_markers=len(near),
                kind=pm.kind if pm is not None else "plot",
                mark_source=source,
                n_lower_bound=bool(pm and pm.n_lower_bound),
                marks=pm.to_dict() if pm is not None else {},
            )
        )
    return FigurePagePlots(
        path=path,
        page_index=page_index,
        figure_label=_figure_label(page),
        panels=panels,
    )


def extract_multipanel_figure_pdf(
    path: Path | str,
    *,
    max_pages: int = 8,
) -> list[FigurePagePlots]:
    path = Path(path)
    doc = fitz.open(path)
    out: list[FigurePagePlots] = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            page_plots = extract_figure_page_plots(page, path=path, page_index=i)
            if page_plots.panels:
                out.append(page_plots)
    finally:
        doc.close()
    return out


def standardized_group_score(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Compare two y-series after z-scoring sorted values (panel-local axis)."""
    sa = np.array(sorted(a), dtype=float)
    sb = np.array(sorted(b), dtype=float)
    if abs(len(sa) - len(sb)) > 2:
        return 0.0
    n = min(len(sa), len(sb))
    if n < 4:
        return 0.0
    sa, sb = sa[:n], sb[:n]
    sa = (sa - sa.mean()) / (sa.std() + 1e-9)
    sb = (sb - sb.mean()) / (sb.std() + 1e-9)
    return float(max(0.0, 1.0 - np.mean(np.abs(sa - sb)) / 2.0))


def match_panel_groups(
    a: PanelPlot,
    b: PanelPlot,
    *,
    min_score: float = 0.98,
) -> list[float]:
    """Greedy match of groups between two panels; return scores >= min_score."""
    used: set[int] = set()
    scores: list[float] = []
    for ga in a.identity_groups:
        best_i: int | None = None
        best_s = min_score
        for i, gb in enumerate(b.identity_groups):
            if i in used:
                continue
            s = standardized_group_score(ga.ys, gb.ys)
            if s >= best_s:
                best_s = s
                best_i = i
        if best_i is not None:
            used.add(best_i)
            scores.append(best_s)
    return scores


def find_identical_panel_pairs(
    page_plots: FigurePagePlots,
    *,
    min_groups: int = 2,
    min_score: float = 0.98,
    min_panel_letter_gap: int = 3,
) -> list[tuple[PanelPlot, PanelPlot, list[float]]]:
    """Find panels with near-identical point clouds.

    ``min_panel_letter_gap`` avoids adjacent shared-control style neighbors (optional).
    """
    out: list[tuple[PanelPlot, PanelPlot, list[float]]] = []
    for a, b in combinations(page_plots.panels, 2):
        gap = abs(ord(a.panel.upper()) - ord(b.panel.upper()))
        if gap < min_panel_letter_gap:
            continue
        scores = match_panel_groups(a, b, min_score=min_score)
        if len(scores) >= min_groups:
            out.append((a, b, scores))
    return out


def find_identical_panels_across_pages(
    pages: list[FigurePagePlots],
    *,
    min_groups: int = 2,
    min_score: float = 0.98,
) -> list[tuple[PanelPlot, PanelPlot, list[float], FigurePagePlots, FigurePagePlots]]:
    """Cross-page panel identity (Excel/Rplot 無しでもパネル間取り違えを検知)."""
    out: list[tuple[PanelPlot, PanelPlot, list[float], FigurePagePlots, FigurePagePlots]] = []
    for i, pa in enumerate(pages):
        for pb in pages[i + 1 :]:
            for a in pa.panels:
                for b in pb.panels:
                    scores = match_panel_groups(a, b, min_score=min_score)
                    if len(scores) >= min_groups:
                        out.append((a, b, scores, pa, pb))
    return out


def build_synthetic_multipanel_pdf(
    path: Path | str,
    *,
    panel_clouds: dict[str, list[tuple[float, float]]] | None = None,
) -> Path:
    """Write a minimal multipanel figure PDF for regression (vector markers)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if panel_clouds is None:
        # Two distant panels with identical 2-group clouds (I↔Q style)
        cloud = (
            [(80 + i * 0.3, 200 + (i % 5) * 4) for i in range(8)]
            + [(110 + i * 0.3, 210 + (i % 5) * 3) for i in range(8)]
        )
        panel_clouds = {
            "I": [(p[0], p[1]) for p in cloud],
            "Q": [(p[0] + 280, p[1]) for p in cloud],
        }
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((40, 36), "Figure 1", fontsize=14)
    origins = {"I": (50, 80), "Q": (330, 80), "A": (50, 80), "B": (330, 80)}
    for letter, pts in panel_clouds.items():
        ox, oy = origins.get(letter, (50, 80))
        page.insert_text((ox, oy), letter, fontsize=16)
        for x, y in pts:
            # tiny filled rect ≈ ggplot point for marker extractor
            r = fitz.Rect(x - 1.5, y - 1.5, x + 1.5, y + 1.5)
            page.draw_rect(r, color=(0, 0, 0), fill=(0.1, 0.1, 0.1), width=0.1)
    doc.save(path)
    doc.close()
    return path


def build_synthetic_grid_multipanel_pdf(
    path: Path | str,
    *,
    page_w: float = 600.0,
    page_h: float = 400.0,
) -> Path:
    """2×2 panel grid (A B / C D) with fixed label anchors for bbox gold."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    page.insert_text((24, 28), "Figure 1", fontsize=14)
    # Label ≈ top-left of each quadrant (fontsize≥10 for _panel_labels)
    anchors = {
        "A": (40.0, 60.0),
        "B": (320.0, 60.0),
        "C": (40.0, 220.0),
        "D": (320.0, 220.0),
    }
    for letter, (x, y) in anchors.items():
        page.insert_text((x, y), letter, fontsize=18)
        # faint content rect so crop is non-empty
        page.draw_rect(
            fitz.Rect(x, y + 8, x + 200, y + 120),
            color=(0.75, 0.75, 0.75),
            width=0.5,
        )
    doc.save(path)
    doc.close()
    return path
