"""合成図（多パネル図・ページ画像・スクリーンショット）のラスタ分割 — 余白 XY-cut.

H3 コーパス照合で「図全体どうし」では見た目が違いすぎて一致しない再利用
（同じ写真が別レイアウト・別画質で載っている）を拾うため、写真系パネルを切り出す。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class PanelBox:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_list(self) -> list[int]:
        return [self.left, self.top, self.right, self.bottom]


def _background_level(gray: np.ndarray, *, tol: float, min_uniform: float = 0.9) -> float | None:
    """Median border tone, or None when the border is not a uniform canvas (a photo)."""
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    level = float(np.median(border))
    if float((np.abs(border - level) <= tol).mean()) < min_uniform:
        return None
    return level


def _runs(flags: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """[start, end) runs of True with length >= min_len."""
    out: list[tuple[int, int]] = []
    start = None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(flags) - start >= min_len:
        out.append((start, len(flags)))
    return out


def _segments(bg_lines: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """Content segments between background gaps (gaps shorter than min_gap are ignored)."""
    n = len(bg_lines)
    content = ~bg_lines
    if not content.any():
        return []
    first = int(np.argmax(content))
    last = n - int(np.argmax(content[::-1]))
    segs: list[tuple[int, int]] = []
    cur = first
    for g0, g1 in _runs(bg_lines[first:last], min_gap):
        segs.append((cur, first + g0))
        cur = first + g1
    segs.append((cur, last))
    return [(a, b) for a, b in segs if b > a]


def _xy_cut(
    bg: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    min_gap: int,
    line_bg_frac: float,
    depth: int,
    max_depth: int,
    out: list[tuple[int, int, int, int]],
) -> None:
    top, bottom, left, right = box
    sub = bg[top:bottom, left:right]
    if sub.size == 0:
        return
    row_bg = sub.mean(axis=1) >= line_bg_frac
    col_bg = sub.mean(axis=0) >= line_bg_frac
    rows = _segments(row_bg, min_gap)
    cols = _segments(col_bg, min_gap)
    if not rows or not cols:
        return
    # Trim to content before deciding whether this is a leaf
    t, b = top + rows[0][0], top + rows[-1][1]
    lft, rgt = left + cols[0][0], left + cols[-1][1]
    if depth >= max_depth:
        out.append((t, b, lft, rgt))
        return
    if len(rows) > 1:
        for r0, r1 in rows:
            _xy_cut(
                bg,
                (top + r0, top + r1, left, right),
                min_gap=min_gap,
                line_bg_frac=line_bg_frac,
                depth=depth + 1,
                max_depth=max_depth,
                out=out,
            )
        return
    if len(cols) > 1:
        for c0, c1 in cols:
            _xy_cut(
                bg,
                (t, b, left + c0, left + c1),
                min_gap=min_gap,
                line_bg_frac=line_bg_frac,
                depth=depth + 1,
                max_depth=max_depth,
                out=out,
            )
        return
    out.append((t, b, lft, rgt))


def _dense_components(
    bg: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    window: int,
    min_density: float,
) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of densely filled blobs inside ``box`` (photos amid diagrams/text)."""
    from scipy import ndimage

    top, bottom, left, right = box
    fg = ~bg[top:bottom, left:right]
    if fg.size == 0:
        return []
    density = ndimage.uniform_filter(fg.astype(np.float32), size=window, mode="constant")
    labels, n = ndimage.label(density >= min_density)
    out: list[tuple[int, int, int, int]] = []
    for sl in ndimage.find_objects(labels)[:n]:
        if sl is None:
            continue
        out.append((top + sl[0].start, top + sl[0].stop, left + sl[1].start, left + sl[1].stop))
    return out


def is_photo_like(
    img: Image.Image,
    *,
    work_side: int = 512,
    bg_tol: float = 12.0,
    min_fill: float = 0.5,
    min_std: float = 8.0,
) -> bool:
    """True when the image is mostly non-background texture (not a chart / text page)."""
    work = img.convert("L")
    work.thumbnail((work_side, work_side))
    gray = np.asarray(work, dtype=np.float32)
    if gray.size == 0:
        return False
    level = _background_level(gray, tol=bg_tol)
    fill = 1.0 if level is None else 1.0 - float((np.abs(gray - level) <= bg_tol).mean())
    return fill >= min_fill and float(gray.std()) >= min_std


def split_panels(
    img: Image.Image,
    *,
    work_side: int = 1600,
    bg_tol: float = 12.0,
    line_bg_frac: float = 0.98,
    min_gap: int = 3,
    min_side_frac: float = 0.03,
    min_side_px: int = 48,
    min_fill: float = 0.5,
    min_std: float = 8.0,
    max_aspect: float = 5.0,
    dense_window: int = 9,
    dense_min: float = 0.85,
    max_panels: int = 32,
    max_depth: int = 12,
) -> list[PanelBox]:
    """Split a composite raster into photo-like panels (original pixel coordinates).

    Background is the median border tone (white pages and dark figure canvases
    both work). Lines that are >= ``line_bg_frac`` background count as gaps, so
    thin frame borders around a screenshot do not block the cut. Leaves that are
    small, mostly background (plots, text), flat (colour bars) or very elongated
    are dropped. Leaves that cannot be cut further but are mostly background
    (e.g. a photo row joined to a diagram by connector lines) are searched for
    dense blobs instead. Returns [] when the image is a single panel.
    """
    w0, h0 = img.size
    if w0 < 2 or h0 < 2:
        return []
    scale = min(1.0, work_side / max(w0, h0))
    work = img.convert("L")
    if scale < 1.0:
        work = work.resize(
            (max(1, int(w0 * scale)), max(1, int(h0 * scale))), Image.Resampling.BILINEAR
        )
    gray = np.asarray(work, dtype=np.float32)
    level = _background_level(gray, tol=bg_tol)
    if level is None:
        return []
    bg = np.abs(gray - level) <= bg_tol
    # Dense-blob search only on page-like light canvases; on dark microscopy
    # backgrounds it would fragment a single specimen into cell clusters.
    dense_ok = level >= 128
    leaves: list[tuple[int, int, int, int]] = []
    _xy_cut(
        bg,
        (0, gray.shape[0], 0, gray.shape[1]),
        min_gap=min_gap,
        line_bg_frac=line_bg_frac,
        depth=0,
        max_depth=max_depth,
        out=leaves,
    )
    min_side_work = max(
        min_side_frac * max(gray.shape), min_side_px * scale
    )
    def _too_small(h: int, w: int) -> bool:
        return min(h, w) < min_side_work

    candidates: list[tuple[int, int, int, int]] = []
    for leaf in leaves:
        t, b, lft, rgt = leaf
        if _too_small(b - t, rgt - lft):
            continue
        if 1.0 - float(bg[t:b, lft:rgt].mean()) >= min_fill:
            candidates.append(leaf)
        elif dense_ok:
            candidates.extend(
                _dense_components(bg, leaf, window=dense_window, min_density=dense_min)
            )

    kept: list[tuple[int, PanelBox]] = []
    for t, b, lft, rgt in candidates:
        h, w = b - t, rgt - lft
        if _too_small(h, w) or max(h, w) / max(min(h, w), 1) > max_aspect:
            continue
        if 1.0 - float(bg[t:b, lft:rgt].mean()) < min_fill:
            continue
        if float(gray[t:b, lft:rgt].std()) < min_std:
            continue
        box = PanelBox(
            left=max(0, int(lft / scale)),
            top=max(0, int(t / scale)),
            right=min(w0, int(np.ceil(rgt / scale))),
            bottom=min(h0, int(np.ceil(b / scale))),
        )
        kept.append((box.area, box))
    kept.sort(key=lambda x: -x[0])
    boxes = [b for _, b in kept[:max_panels]]
    if len(boxes) == 1 and boxes[0].area >= 0.9 * w0 * h0:
        return []
    return boxes
