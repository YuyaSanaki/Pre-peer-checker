"""生存率×固定 N0 が非整数羽 — P-SURVIVAL-COUNT-NONINTEGER."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from pre_peer_checker.warnings import WarningItem, WarningTag

_N0_RE = re.compile(
    r"(?P<n>\d+)\s*(?:flies?|animals?|mice|rats?|individuals?|subjects?)?\s*"
    r"(?:per\s+)?(?:vial|cage|cohort|group|dish|well)",
    re.I,
)
_N0_ALT = re.compile(
    r"(?:vial|cage|cohort)\s*(?:size|n)\s*[=:]?\s*(?P<n>\d+)"
    r"|(?:starting|initial)\s+n\s*[=:]\s*(?P<n2>\d+)",
    re.I,
)
_ABOUT = re.compile(r"\b(?:about|approximately|approx\.?|~|≈|およそ|約)\b", re.I)


def extract_cohort_size(texts: list[str]) -> tuple[int | None, str | None, bool]:
    """Return (N0, span, approximate)."""
    approx = False
    for t in texts:
        if not t:
            continue
        blob = str(t)
        if _ABOUT.search(blob):
            approx = True
        for rx in (_N0_RE, _N0_ALT):
            m = rx.search(blob)
            if not m:
                continue
            n = m.groupdict().get("n") or m.groupdict().get("n2")
            if n:
                return int(n), m.group(0).strip(), approx
    return None, None, approx


def _survival_columns(df: pd.DataFrame) -> tuple[str | None, str | None]:
    cols = {str(c).lower(): c for c in df.columns}
    surv = None
    for key in ("survival", "surv", "fraction_alive", "pct_survival", "survival_rate"):
        if key in cols:
            surv = cols[key]
            break
    if surv is None:
        for c in df.columns:
            if "surv" in str(c).lower():
                surv = c
                break
    time_col = None
    for key in ("day", "time", "t", "week"):
        if key in cols:
            time_col = cols[key]
            break
    return time_col, surv


def load_survival_fractions(path: Path) -> list[tuple[str, float]]:
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    _, surv = _survival_columns(df)
    if surv is None:
        return []
    series = pd.to_numeric(df[surv], errors="coerce")
    # Accept percent (0–100) or fraction (0–1)
    vals = [float(x) for x in series.dropna().tolist()]
    if not vals:
        return []
    if max(vals) > 1.5:
        vals = [v / 100.0 for v in vals]
    out: list[tuple[str, float]] = []
    for i, v in enumerate(vals):
        if v < 0 or v > 1.0 + 1e-6:
            continue
        out.append((f"row{i}", v))
    return out


def _alive_column(df: pd.DataFrame) -> str | None:
    cols = {str(c).lower(): c for c in df.columns}
    for key in ("n_alive", "alive", "count_alive", "n_surviving", "surviving"):
        if key in cols:
            return cols[key]
    return None


def audit_survival_curve(
    path: Path,
    n0: int,
    *,
    int_tol: float = 1e-6,
) -> list[dict]:
    """Rebuild expected alive counts from survival×N0; flag mismatches / rises."""
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    time_col, surv = _survival_columns(df)
    if surv is None:
        return []
    alive_col = _alive_column(df)
    fracs = pd.to_numeric(df[surv], errors="coerce")
    finite = fracs.dropna()
    if len(finite) and float(finite.max()) > 1.5:
        fracs = fracs / 100.0
    issues: list[dict] = []
    prev_expected: float | None = None
    for i, frac in enumerate(fracs.tolist()):
        if frac != frac:  # NaN
            continue
        if frac < 0 or frac > 1.0 + 1e-6:
            continue
        expected = float(frac) * n0
        nearest = round(expected)
        row_issues: dict = {"row": i, "fraction": float(frac), "expected_count": expected}
        if abs(expected - nearest) > int_tol and abs(expected - nearest) > 1e-3:
            row_issues["noninteger"] = True
        if alive_col is not None:
            claimed = pd.to_numeric(df.iloc[i][alive_col], errors="coerce")
            if claimed == claimed:  # not NaN
                if abs(float(claimed) - expected) > 0.51:
                    row_issues["claimed_alive"] = float(claimed)
                    row_issues["curve_mismatch"] = True
        if prev_expected is not None and expected > prev_expected + 0.51:
            row_issues["non_monotonic"] = True
        prev_expected = expected
        if any(k in row_issues for k in ("noninteger", "curve_mismatch", "non_monotonic")):
            if time_col is not None:
                row_issues["time"] = str(df.iloc[i][time_col])
            issues.append(row_issues)
    return issues


def warnings_from_survival_counts(
    texts: list[str],
    table_paths: list[Path],
    *,
    int_tol: float = 1e-6,
) -> list[WarningItem]:
    n0, span, approx = extract_cohort_size(texts)
    if n0 is None or approx:
        return []
    if n0 < 2:
        return []

    warnings: list[WarningItem] = []
    for path in table_paths:
        # Prefer curve audit (covers non-integer + n_alive mismatch + rises)
        audited = audit_survival_curve(path, n0, int_tol=int_tol)
        if not audited:
            # Fall back to fraction-only non-integer scan
            fracs = load_survival_fractions(path)
            if not fracs:
                continue
            for label, frac in fracs:
                expected = frac * n0
                nearest = round(expected)
                if abs(expected - nearest) > int_tol and abs(expected - nearest) > 1e-3:
                    audited.append(
                        {
                            "label": label,
                            "fraction": frac,
                            "expected_count": expected,
                            "noninteger": True,
                        }
                    )
        if not audited:
            continue
        sample = audited[0]
        has_curve = any(
            x.get("curve_mismatch") or x.get("non_monotonic") for x in audited
        )
        has_nonint = any(x.get("noninteger") for x in audited)
        if has_curve:
            title = "生存曲線の再計算が記載個体数と不一致"
            reason = (
                f"記載「{span}」→ N0={n0} で生存率から再計算すると、"
                f"例: fraction={sample.get('fraction', 0):g}"
                + (
                    f" ↔ n_alive={sample['claimed_alive']:g}"
                    if "claimed_alive" in sample
                    else f" → {sample.get('expected_count', 0):.4g} 羽"
                )
                + f"。不一致／非単調 {len(audited)} 箇所。"
            )
            check = "survival_curve_recalc"
        else:
            title = "生存率×固定個体数が非整数になる時点あり"
            reason = (
                f"記載「{span}」→ N0={n0} なのに、例: fraction={sample.get('fraction', 0):g} "
                f"→ {sample.get('expected_count', 0):.4g} 羽（非整数）。"
                f"不一致時点 {len(audited)} 箇所。"
            )
            check = "noninteger"
        if not has_nonint and not has_curve:
            continue
        warnings.append(
            WarningItem(
                tag=WarningTag.SAMPLE_SIZE,
                title=title,
                location=f"{path.name} (N0={n0})",
                reason=reason,
                sources=[str(path)],
                metadata={
                    "pattern_id": "P-SURVIVAL-COUNT-NONINTEGER",
                    "n0": n0,
                    "n0_span": span,
                    "n_bad_points": len(audited),
                    "examples": audited[:5],
                    "check": check,
                },
            )
        )
    return warnings
