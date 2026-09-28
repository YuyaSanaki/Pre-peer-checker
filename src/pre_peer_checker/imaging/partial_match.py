"""部分画像一致（クロップ／縮小包含）— P-IMAGE-PARTIAL-REUSE の「比べる」."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import fft as _sfft


@dataclass
class PartialMatch:
    path_a: Path
    path_b: Path
    score: float
    template_is_a: bool
    method: str = "ncc_containment"
    best_scale: float | None = None
    best_rotation: int = 0


def _decode_gray(path: Path, max_side: int) -> np.ndarray:
    img = Image.open(path).convert("L")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.float32)


@lru_cache(maxsize=256)
def _gray_cached(path_str: str, max_side: int, _stat: tuple[int, int]) -> np.ndarray:
    arr = _decode_gray(Path(path_str), max_side)
    arr.flags.writeable = False  # shared across pairs — must not be mutated in place
    return arr


def _gray(path: Path, max_side: int = 256) -> np.ndarray:
    """Grayscale array, cached per (path, mtime, size, max_side).

    Cross-set scans re-request the same image for every pair, so decoding once
    per image instead of once per pair removes the bulk of the I/O.
    """
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return _decode_gray(p, max_side)
    return _gray_cached(str(p.resolve()), max_side, (st.st_mtime_ns, st.st_size))


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


class _NccTarget:
    """Precomputed ``large``-side terms for NCC against many templates.

    Holds the padded spectrum and the summed-area tables of ``large`` and
    ``large**2``. Every template in the rotation × scale grid reuses these, so
    the per-template work is one small forward transform plus one inverse.
    """

    def __init__(self, large: np.ndarray):
        self.large = np.ascontiguousarray(large, dtype=np.float64)
        lh, lw = self.large.shape
        self.shape = (lh, lw)
        # Zero-pad to >= 2*side-1 so correlation never wraps, for any template
        # up to the full size of ``large``. Independent of template size, which
        # keeps this reusable across the whole grid.
        self.fshape = (
            int(_sfft.next_fast_len(2 * lh - 1)),
            int(_sfft.next_fast_len(2 * lw - 1)),
        )
        self.spectrum = _sfft.rfft2(self.large, s=self.fshape)
        self._sum = _integral(self.large)
        self._sumsq = _integral(self.large * self.large)

    def _window_norms(self, sh: int, sw: int) -> np.ndarray:
        """L2 norm of every mean-centred ``sh``×``sw`` window of ``large``."""
        s1 = _box_sums(self._sum, sh, sw)
        s2 = _box_sums(self._sumsq, sh, sw)
        var = s2 - (s1 * s1) / float(sh * sw)
        return np.sqrt(np.maximum(var, 0.0))

    def max_ncc(self, template: np.ndarray) -> float:
        sh, sw = template.shape
        lh, lw = self.shape
        if sh > lh or sw > lw or template.size < 16:
            return 0.0
        t = np.asarray(template, dtype=np.float64)
        t = t - t.mean()
        t_norm = float(np.linalg.norm(t))
        if t_norm < 1e-6:
            return 0.0
        # corr[y, x] = sum(window(y, x) * t); the window mean drops out because
        # t is already mean-centred, so this is the NCC numerator.
        spec = self.spectrum * _sfft.rfft2(t[::-1, ::-1], s=self.fshape)
        corr = _sfft.irfft2(spec, s=self.fshape)[sh - 1 : lh, sw - 1 : lw]
        w_norm = self._window_norms(sh, sw)
        usable = w_norm > 1e-6
        if not usable.any():
            return 0.0
        return float((corr[usable] / (t_norm * w_norm[usable])).max())


def _integral(arr: np.ndarray) -> np.ndarray:
    return np.pad(arr.cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)))


def _box_sums(integral: np.ndarray, sh: int, sw: int) -> np.ndarray:
    return (
        integral[sh:, sw:]
        - integral[:-sh, sw:]
        - integral[sh:, :-sw]
        + integral[:-sh, :-sw]
    )


def max_ncc_containment(
    small: np.ndarray,
    large: np.ndarray,
    *,
    stride: int | None = None,
) -> float:
    """Max normalized cross-correlation of ``small`` sliding over ``large``.

    Evaluates every offset (stride 1) via FFT cross-correlation and summed-area
    tables. ``stride`` is accepted for backwards compatibility and ignored: the
    search is now exhaustive, so it can only find peaks the strided scan missed.
    """
    sh, sw = small.shape
    lh, lw = large.shape
    if sh > lh or sw > lw:
        return 0.0
    return _NccTarget(large).max_ncc(small)


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
    target = _NccTarget(large)
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
            score = target.max_ncc(tmpl)
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
