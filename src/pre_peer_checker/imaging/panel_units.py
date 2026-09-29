"""写真パネル単位の照合に共通の下ごしらえ（コーパス照合・原稿内照合で共有）."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from pre_peer_checker.imaging.microscopy import frame_to_uint8_rgb, try_load_frames
from pre_peer_checker.imaging.panel_split import is_photo_like, split_panels


@dataclass
class PanelUnit:
    path: Path
    source: Path
    box: list[int] | None  # None = whole image


def save_scaled(img: Image.Image, dest: Path, max_side: int) -> Path:
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR
        )
    img.save(dest)
    return dest


def prepare_panel_sources(
    paths: list[Path],
    out_dir: Path,
    *,
    n_preview: int,
    panels: bool,
    preview_max_side: int = 384,
    panel_max_side: int = 768,
    max_panels_per_image: int = 24,
    max_units: int = 800,
    panel_boxes: dict[str, list] | None = None,
) -> tuple[list[tuple[Path, Path]], list[PanelUnit]]:
    """Load each source once; write whole-image previews and panel crops.

    Returns (previews for the first ``n_preview`` sources as (preview, source),
    panel units — the whole image plus split panels — for every source).

    ``panel_boxes`` maps a resolved source path to letter-anchor crops (from
    figure OCR). When present they replace XY-cut for that file; crops that are
    not photo-like fall back to XY-cut so graphs do not become LightGlue units.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    previews: list[tuple[Path, Path]] = []
    units: list[PanelUnit] = []
    boxes_map = panel_boxes or {}
    for i, src in enumerate(paths):
        want_units = panels and len(units) < max_units
        if i >= n_preview and not want_units:
            break
        frames, err = try_load_frames(src, max_series=1)
        if err or not frames:
            continue
        img = Image.fromarray(frame_to_uint8_rgb(frames[0].data))
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in src.stem)[:40]
        stem = f"{i}_{safe}"
        if i < n_preview:
            previews.append((save_scaled(img, out_dir / f"{stem}.png", preview_max_side), src))
        if not want_units:
            continue
        try:
            src_key = str(src.resolve())
        except OSError:
            src_key = str(src)
        precomputed = boxes_map.get(src_key) or boxes_map.get(str(src)) or []
        boxes = []
        if precomputed:
            photo_boxes = []
            for box in precomputed:
                crop = img.crop((box.left, box.top, box.right, box.bottom))
                if is_photo_like(crop):
                    photo_boxes.append(box)
            boxes = photo_boxes
        if not boxes:
            boxes = split_panels(img, max_panels=max_panels_per_image)
        # Charts / text pages share glyphs and axes, which LightGlue happily matches,
        # so the whole image is a unit only when it is itself photo-like
        if is_photo_like(img):
            units.append(
                PanelUnit(
                    save_scaled(img, out_dir / f"{stem}_whole.png", panel_max_side), src, None
                )
            )
        for j, box in enumerate(boxes):
            if len(units) >= max_units:
                break
            crop = img.crop((box.left, box.top, box.right, box.bottom))
            units.append(
                PanelUnit(
                    save_scaled(crop, out_dir / f"{stem}_p{j}.png", panel_max_side),
                    src,
                    box.as_list(),
                )
            )
    return previews, units


def rank_vectors(paths: list[Path], *, prefer_dino: bool) -> np.ndarray:
    """L2-normalised global descriptors used only to shortlist panel pairs."""
    from pre_peer_checker.imaging.duplicate_scan import (
        DinoDuplicateScanner,
        _combined_fallback_vector,
        shared_dino_scanner,
    )

    def _fallback() -> np.ndarray:
        return np.stack([_combined_fallback_vector(p) for p in paths])

    if not (prefer_dino and DinoDuplicateScanner.available()):
        return _fallback()
    try:
        return np.stack(shared_dino_scanner().embed_many(paths, full_frame=True))
    except Exception:  # noqa: BLE001
        return _fallback()


def rank_similarity(
    paths_a: list[Path], paths_b: list[Path] | None = None, *, prefer_dino: bool
) -> np.ndarray:
    """Shortlist scores a×b (a×a when ``paths_b`` is None), max over normal and
    ``b`` intensity-inverted, so an inverted reuse is not dropped before verification."""
    from pre_peer_checker.imaging.lightglue_match import inverted_copy

    same = paths_b is None
    b = paths_a if same else paths_b
    extra = [] if same else list(b)
    # One call so every row comes from the same descriptor (DINO or fallback).
    vec = rank_vectors(
        list(paths_a) + extra + [inverted_copy(p) for p in b], prefer_dino=prefer_dino
    )
    n_a, n_b = len(paths_a), len(b)
    va = vec[:n_a]
    vb = va if same else vec[n_a : n_a + n_b]
    vb_inv = vec[len(vec) - n_b :]
    sims = np.maximum(va @ vb.T, va @ vb_inv.T)
    return np.maximum(sims, sims.T) if same else sims


def match_rank(a: PanelUnit, b: PanelUnit, vr: Any) -> tuple[int, float]:
    """Sort key for competing matches on one source pair, best last.

    A match between two split panels localises the reuse to a box, so prefer it
    over an equally strong whole-image match.
    """
    localised = (a.box is not None) + (b.box is not None)
    return localised, float(vr.score)


PanelPositions = dict[tuple[str, tuple[int, ...]], str]


def _row_name(r: int, n_rows: int) -> str:
    if n_rows == 2:
        return ("上段", "下段")[r]
    if n_rows == 3:
        return ("上段", "中段", "下段")[r]
    return f"上から{r + 1}段目"


def _grid_rows(boxes: list[tuple[int, ...]]) -> list[list[tuple[int, ...]]]:
    """Group boxes into rows (top to bottom), each row sorted left to right."""
    rows: list[list[tuple[int, ...]]] = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        top, bottom = box[1], box[3]
        for row in rows:
            r_top = min(b[1] for b in row)
            r_bottom = max(b[3] for b in row)
            overlap = min(bottom, r_bottom) - max(top, r_top)
            if overlap >= 0.5 * min(bottom - top, r_bottom - r_top):
                row.append(box)
                break
        else:
            rows.append([box])
    rows.sort(key=lambda row: min(b[1] for b in row))
    for row in rows:
        row.sort(key=lambda b: b[0])
    return rows


def _grid_labels(boxes: list[tuple[int, ...]]) -> dict[tuple[int, ...], str]:
    """Reading-order position ("上段・左から2枚目") of each box among its siblings."""
    rows = _grid_rows(boxes)
    out: dict[tuple[int, ...], str] = {}
    for r, row in enumerate(rows):
        for c, box in enumerate(row):
            col = f"左から{c + 1}枚目" if len(row) > 1 else ""
            if len(rows) == 1:
                out[box] = col or "パネル"
            else:
                out[box] = "・".join(p for p in (_row_name(r, len(rows)), col) if p)
    return out


def panel_positions(units: list[PanelUnit]) -> PanelPositions:
    """(source, box) -> human-readable panel position within its source image."""
    by_source: dict[str, list[tuple[int, ...]]] = {}
    for u in units:
        if u.box is not None:
            by_source.setdefault(str(u.source), []).append(tuple(u.box))
    out: PanelPositions = {}
    for source, boxes in by_source.items():
        for box, label in _grid_labels(boxes).items():
            out[(source, box)] = label
    return out


def panel_reading_order(units: list[PanelUnit]) -> dict[tuple[str, tuple[int, ...]], tuple[int, int]]:
    """(source, box) -> (row, column) in reading order within its source image."""
    by_source: dict[str, list[tuple[int, ...]]] = {}
    for u in units:
        if u.box is not None:
            by_source.setdefault(str(u.source), []).append(tuple(u.box))
    out: dict[tuple[str, tuple[int, ...]], tuple[int, int]] = {}
    for source, boxes in by_source.items():
        for r, row in enumerate(_grid_rows(boxes)):
            for c, box in enumerate(row):
                out[(source, box)] = (r, c)
    return out


def panel_position(unit: PanelUnit, positions: PanelPositions | None) -> str:
    if unit.box is None:
        return "画像全体"
    if positions:
        label = positions.get((str(unit.source), tuple(unit.box)))
        if label:
            return label
    left, top, right, bottom = unit.box
    return f"[{left},{top}–{right},{bottom}]"


def panel_label(unit: PanelUnit, positions: PanelPositions | None = None) -> str:
    name = Path(unit.source).name
    if unit.box is None:
        return name
    return f"{name}（{panel_position(unit, positions)}）"


def make_panel_verifier(
    *, prefer_lightglue: bool = True
) -> tuple[Callable[[Path, Path], Any], str]:
    """Return (verify(a, b), verifier name) gated for panel-level identity."""
    from pre_peer_checker.imaging.lightglue_match import (
        PANEL_MIN_INLIER_RATIO,
        PANEL_MIN_INLIERS_BY_FEATURES,
        LightGlueFeatureCache,
        _verify_ncc,
        _verify_orb,
        active_lightglue_features,
        lightglue_available,
        opencv_available,
        with_inversion,
    )

    cache = None
    if prefer_lightglue and lightglue_available():
        min_inliers = PANEL_MIN_INLIERS_BY_FEATURES[active_lightglue_features()]
        cache = LightGlueFeatureCache(
            min_matches=min_inliers,
            min_inliers=min_inliers,
            min_inlier_ratio=PANEL_MIN_INLIER_RATIO,
        )
    use_orb = opencv_available()

    def _fallback_once(a: Path, b: Path):
        if use_orb:
            vr = _verify_orb(a, b, min_inliers=30)
            # ORB's default 0.25 inlier ratio is far too loose over thousands of pairs
            vr.verified = vr.verified and vr.score >= 0.5
            return vr
        return _verify_ncc(a, b)

    def _fallback(a: Path, b: Path):
        return with_inversion(_fallback_once, a, b)

    def _verify(a: Path, b: Path):
        if cache is None:
            return _fallback(a, b)
        try:
            return cache.match(a, b)
        except Exception:  # noqa: BLE001
            return _fallback(a, b)

    name = f"lightglue+{cache.features}" if cache is not None else ("orb" if use_orb else "ncc")
    return _verify, name
