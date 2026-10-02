"""Number of subjects behind a survival / time-course curve.

Source Data often gives a Kaplan–Meier curve as percent survival per time point
instead of one row per animal. Each drop is ``deaths / at-risk`` for whole
numbers, so the starting cohort can be recovered: the smallest ``n`` whose
integer replay (deaths, with censoring lowering the at-risk count between
deaths) reproduces every printed value to its printed precision.
"""

from __future__ import annotations

import math

MAX_COHORT = 5000


def _decimals(v: float) -> int:
    s = f"{v:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def _tolerance(v: float, scale: float) -> float:
    """Half a unit of the last printed digit, in fraction units."""
    return (0.5 * 10 ** (-min(_decimals(v), 8)) + 1e-9) / scale


def _replay(fracs: list[float], tols: list[float], n: int) -> bool:
    at_risk = n
    # chain from the printed previous value: each printed value carries its own rounding
    s_prev, tol_prev = 1.0, 0.0
    for s, tol in zip(fracs, tols):
        if s >= s_prev - tol - tol_prev:
            continue
        q = 1.0 - s / s_prev
        slack = tol + tol_prev + 1e-12
        hit = None
        # largest at-risk count (least censoring) whose integer deaths fit
        for d in range(min(at_risk, math.floor(at_risk * q + 0.5) + 1), 0, -1):
            r = round(d / q) if q > 0 else at_risk
            for rr in (r, r - 1, r + 1):
                if d <= rr <= at_risk and abs(s_prev * (1 - d / rr) - s) <= slack:
                    hit = (rr, d)
                    break
            if hit:
                break
        if hit is None:
            return False
        rr, d = hit
        s_prev, tol_prev = s, tol
        at_risk = rr - d
    return True


def km_cohort_size(values: list[float], *, max_n: int = MAX_COHORT) -> int | None:
    """Starting n of a percent (0–100) or fraction (0–1) Kaplan–Meier curve."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 3:
        return None
    if any(b > a + 1e-9 for a, b in zip(vals, vals[1:])):
        return None
    scale = 100.0 if vals[0] > 1.5 else 1.0
    if not (0.99 * scale <= vals[0] <= 1.0 * scale + 1e-9):
        return None
    fracs = [v / scale for v in vals[1:]]
    # stored cells drop trailing zeros: 98.4 next to 87.178378 is 98.400000
    digits = max(_decimals(v) for v in vals)
    tols = [_tolerance(10.0 ** -digits if digits else 1.0, scale)] * len(fracs)
    if all(f >= 1.0 - t for f, t in zip(fracs, tols)):
        return None
    for n in range(2, max_n + 1):
        if _replay(fracs, tols, n):
            return n
    return None


def alive_count_cohort(values: list[float]) -> int | None:
    """Curves given as animals alive per time point: the first count."""
    vals = [v for v in values if v is not None]
    if len(vals) < 3 or any(not float(v).is_integer() for v in vals):
        return None
    if any(b > a for a, b in zip(vals, vals[1:])):
        return None
    return int(vals[0]) if vals[0] > 0 else None


def curve_cohort_size(values: list[float]) -> int | None:
    """n of one curve column, or None when the column is not a survival-type curve."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    top = max(vals)
    # integer columns topping out at 100 are ambiguous (percent vs 100 animals): KM first
    if abs(top - 100.0) < 1e-9 or top <= 1.0:
        n = km_cohort_size(vals)
        if n is not None:
            return n
    return alive_count_cohort(vals)
