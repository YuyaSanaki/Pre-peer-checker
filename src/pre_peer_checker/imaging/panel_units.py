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
) -> tuple[list[tuple[Path, Path]], list[PanelUnit]]:
    """Load each source once; write whole-image previews and panel crops.

    Returns (previews for the first ``n_preview`` sources as (preview, source),
    panel units — the whole image plus split panels — for every source).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    previews: list[tuple[Path, Path]] = []
    units: list[PanelUnit] = []
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


def match_rank(a: PanelUnit, b: PanelUnit, vr: Any) -> tuple[int, float]:
    """Sort key for competing matches on one source pair, best last.

    A match between two split panels localises the reuse to a box, so prefer it
    over an equally strong whole-image match.
    """
    localised = (a.box is not None) + (b.box is not None)
    return localised, float(vr.score)


def panel_label(unit: PanelUnit) -> str:
    name = Path(unit.source).name
    if unit.box is None:
        return name
    left, top, right, bottom = unit.box
    return f"{name} [{left},{top}–{right},{bottom}]"


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

    def _fallback(a: Path, b: Path):
        if use_orb:
            vr = _verify_orb(a, b, min_inliers=30)
            # ORB's default 0.25 inlier ratio is far too loose over thousands of pairs
            vr.verified = vr.verified and vr.score >= 0.5
            return vr
        return _verify_ncc(a, b)

    def _verify(a: Path, b: Path):
        if cache is None:
            return _fallback(a, b)
        try:
            return cache.match(a, b)
        except Exception:  # noqa: BLE001
            return _fallback(a, b)

    name = f"lightglue+{cache.features}" if cache is not None else ("orb" if use_orb else "ncc")
    return _verify, name
