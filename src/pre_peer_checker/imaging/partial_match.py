"""部分画像一致（クロップ／縮小包含）— P-IMAGE-PARTIAL-REUSE の「比べる」."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class PartialMatch:
    path_a: Path
    path_b: Path
    score: float
    template_is_a: bool
    method: str = "ncc_containment"
    best_scale: float | None = None
    best_rotation: int = 0


def _gray(path: Path, max_side: int = 256) -> np.ndarray:
    img = Image.open(path).convert("L")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.float32)


def _ncc_at(template: np.ndarray, window: np.ndarray) -> float:
    t = template.ravel()
    w = window.ravel()
    if t.size != w.size or t.size < 16:
        return 0.0
    t = t - t.mean()
    w = w - w.mean()
    tn = float(np.linalg.norm(t))
    wn = float(np.linalg.norm(w))
    if tn < 1e-6 or wn < 1e-6:
        return 0.0
    return float(np.dot(t, w) / (tn * wn))


def max_ncc_containment(
    small: np.ndarray,
    large: np.ndarray,
    *,
    stride: int | None = None,
) -> float:
    """Max normalized cross-correlation of ``small`` sliding over ``large``."""
    sh, sw = small.shape
    lh, lw = large.shape
    if sh > lh or sw > lw:
        return 0.0
    if stride is None:
        stride = 1 if max(sh, sw) <= 64 else max(1, min(sh, sw) // 12)
    best = 0.0
    best_yx = (0, 0)
    for y in range(0, lh - sh + 1, stride):
        for x in range(0, lw - sw + 1, stride):
            window = large[y : y + sh, x : x + sw]
            score = _ncc_at(small, window)
            if score > best:
                best = score
                best_yx = (y, x)
    if stride > 1 and best > 0.5:
        y0, x0 = best_yx
        pad = max(stride, 4)
        y_lo = max(0, y0 - pad)
        y_hi = min(lh - sh, y0 + pad)
        x_lo = max(0, x0 - pad)
        x_hi = min(lw - sw, x0 + pad)
        for y in range(y_lo, y_hi + 1):
            for x in range(x_lo, x_hi + 1):
                window = large[y : y + sh, x : x + sw]
                score = _ncc_at(small, window)
                if score > best:
                    best = score
    return best


def _scale_grid(
    *,
    scale_min: float = 0.55,
    scale_max: float = 1.0,
    scale_step: float = 0.05,
    discrete_fallback: tuple[float, ...] = (1.0, 0.85, 0.7),
) -> list[float]:
    """Continuous scale grid (inclusive), unique + sorted descending."""
    if scale_step <= 0:
        return list(discrete_fallback)
    vals = list(np.arange(scale_min, scale_max + scale_step * 0.5, scale_step))
    # Always include endpoints and classic discrete points
    for v in (scale_min, scale_max) + discrete_fallback:
        vals.append(float(v))
    uniq = sorted({round(float(v), 4) for v in vals if scale_min - 1e-9 <= v <= scale_max + 1e-9})
    return list(reversed(uniq))


def partial_containment_score(
    path_a: Path | str,
    path_b: Path | str,
    *,
    max_side: int = 256,
    min_area_ratio: float = 0.10,
    max_area_ratio: float = 0.85,
    try_rotations: bool = True,
    confirm_precise: bool = True,
    scale_min: float = 0.55,
    scale_max: float = 1.0,
    scale_step: float = 0.05,
    early_exit_score: float = 0.98,
) -> PartialMatch | None:
    """Return match if one image is substantially contained in the other.

    Uses a continuous scale grid (default 0.55–1.0 step 0.05) × optional
    90/180/270° rotations. Optionally re-verifies with LightGlue/ORB.
    """
    a, b = Path(path_a), Path(path_b)
    ga, gb = _gray(a, max_side=max_side), _gray(b, max_side=max_side)
    area_a, area_b = ga.size, gb.size
    if area_a <= 0 or area_b <= 0:
        return None
    if area_a < area_b:
        small, large, template_is_a = ga, gb, True
        ratio = area_a / area_b
    else:
        small, large, template_is_a = gb, ga, False
        ratio = area_b / area_a
    if ratio < min_area_ratio or ratio > max_area_ratio:
        return None

    scales = _scale_grid(
        scale_min=scale_min, scale_max=scale_max, scale_step=scale_step
    )
    rotations = (0, 90, 180, 270) if try_rotations else (0,)
    best = 0.0
    best_rot = 0
    best_scale = 1.0
    for rot in rotations:
        if rot == 0:
            base = small
        else:
            base = np.asarray(
                Image.fromarray(small.astype(np.uint8)).rotate(rot, expand=True),
                dtype=np.float32,
            )
        for s in scales:
            if abs(s - 1.0) < 1e-9:
                tmpl = base
            else:
                th, tw = max(8, int(base.shape[0] * s)), max(8, int(base.shape[1] * s))
                tmpl = np.asarray(
                    Image.fromarray(base.astype(np.uint8)).resize(
                        (tw, th), Image.Resampling.BILINEAR
                    ),
                    dtype=np.float32,
                )
            if tmpl.shape[0] > large.shape[0] or tmpl.shape[1] > large.shape[1]:
                continue
            score = max_ncc_containment(tmpl, large)
            if score > best:
                best = score
                best_rot = rot
                best_scale = float(s)
            if best >= early_exit_score:
                break
        if best >= early_exit_score:
            break

    method = "ncc_containment"
    if abs(best_scale - 1.0) > 1e-3:
        method = f"{method}_s{best_scale:.2f}"
    if best_rot:
        method = f"{method}_rot{best_rot}"
    if confirm_precise and best >= 0.85:
        try:
            from pre_peer_checker.imaging.lightglue_match import verify_image_pair

            vr = verify_image_pair(a, b)
            if vr.verified:
                method = f"{method}+{vr.method}"
                best = max(best, float(vr.score) if vr.score else best)
        except Exception:
            pass
    return PartialMatch(
        path_a=a,
        path_b=b,
        score=best,
        template_is_a=template_is_a,
        method=method,
        best_scale=best_scale,
        best_rotation=best_rot,
    )


def scan_partial_pairs(
    paths: list[Path | str],
    *,
    threshold: float = 0.92,
    max_pairs: int = 40,
) -> list[PartialMatch]:
    """Pairwise partial containment among paths (small sets / synthetics)."""
    resolved = [Path(p) for p in paths]
    out: list[PartialMatch] = []
    from itertools import combinations

    for a, b in combinations(resolved, 2):
        m = partial_containment_score(a, b)
        if m is not None and m.score >= threshold:
            out.append(m)
            if len(out) >= max_pairs:
                break
    return out
