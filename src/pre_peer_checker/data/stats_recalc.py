"""表データ読込と統計再計算."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class GroupStats:
    group: str
    n: int
    mean: float
    sd: float
    median: float


@dataclass
class StatsResult:
    path: Path
    value_col: str
    group_col: str | None
    groups: list[GroupStats]
    pairwise: list[dict[str, Any]] = field(default_factory=list)
    anova: dict[str, Any] | None = None
    warnings_hints: list[str] = field(default_factory=list)


def _dedupe_columns(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        base = name or "col"
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base}.{n}")
    return out


def _score_dataframe(df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return -1
    cols = {str(c).lower() for c in df.columns}
    score = 0
    if "value" in cols:
        score += 50
    if cols & {"label", "genotype", "group", "condition"}:
        score += 25
    if any("ratio" in c for c in cols):
        score += 40
    numeric_nonnull = 0
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce")
        numeric_nonnull += int(s.notna().sum())
    if numeric_nonnull == 0:
        return -1
    score += min(numeric_nonnull, 80)
    score += min(len(df), 40)
    return score


def _find_header_row(df: pd.DataFrame) -> int | None:
    limit = min(40, len(df))
    for i in range(limit):
        cells = [
            str(x).strip().lower() if pd.notna(x) else "" for x in df.iloc[i].tolist()
        ]
        joined = " | ".join(cells)
        if "ratio" in joined and ("%" in joined or "percent" in joined or "ratio" in cells):
            return i
        if "value" in cells and (
            "label" in cells or "genotype" in cells or "group" in cells or "id" in cells
        ):
            return i
        if "disc id" in joined or "dic id" in joined:
            return i
    return None


def _promote_header(df: pd.DataFrame, header_row: int) -> pd.DataFrame:
    headers: list[str] = []
    for j, v in enumerate(df.iloc[header_row].tolist()):
        if pd.isna(v) or str(v).strip() == "":
            headers.append(f"col_{j}")
        else:
            headers.append(str(v).strip())
    body = df.iloc[header_row + 1 :].copy()
    body.columns = _dedupe_columns(headers)
    body = body.dropna(how="all")
    return body.reset_index(drop=True)


def recover_ratio_measurement_table(df_raw: pd.DataFrame) -> pd.DataFrame | None:
    """Imaging workbooks: header row with ratio (%) → tidy label/value."""
    if df_raw is None or df_raw.empty:
        return None
    header_i = _find_header_row(df_raw)
    if header_i is None:
        return None
    body = _promote_header(df_raw, header_i)
    ratio_cols = [c for c in body.columns if "ratio" in str(c).lower()]
    if not ratio_cols:
        return None
    series = pd.to_numeric(body[ratio_cols[0]], errors="coerce").dropna()
    # Drop sentinel 100% rows sometimes left in clone-ratio sheets
    series = series[(series >= 0) & (series < 99.999)]
    if len(series) < 3:
        return None
    return pd.DataFrame(
        {
            "label": ["all"] * len(series),
            "value": [float(x) for x in series.tolist()],
        }
    )


def _load_excel_best(path: Path) -> pd.DataFrame:
    """Pick the richest sheet; recover headerless ratio tables when needed."""
    with warnings.catch_warnings():
        # openpyxl drops unsupported Excel extensions (slicers, x14 validation, ...);
        # harmless for read-only use but emitted once per sheet.
        warnings.filterwarnings(
            "ignore",
            message=r".*extension is not supported and will be removed",
            category=UserWarning,
            module=r"openpyxl\..*",
        )
        return _load_excel_best_inner(path)


def _load_excel_best_inner(path: Path) -> pd.DataFrame:
    from pre_peer_checker.data.table_grid import find_header_row, grid_to_frame, read_grids

    try:
        grids = read_grids(path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Excel を読めません: {path.name}: {exc}") from exc
    if not grids:
        raise ValueError(f"シートがありません: {path.name}")

    best: pd.DataFrame | None = None
    best_score = -1
    for grid in grids:
        if not grid.rows:
            continue
        raw = grid_to_frame(grid, None)
        header_at = find_header_row(grid.rows)
        tidy0 = grid_to_frame(grid, 0)
        views = [tidy0]
        if header_at:
            views.append(grid_to_frame(grid, header_at))
        for candidate in views:
            if candidate is None or candidate.empty:
                continue
            score = _score_dataframe(candidate)
            # Already-tidy value/label sheets win over wide imaging layouts
            cols = {str(c).lower() for c in candidate.columns}
            if "value" in cols and cols & {"label", "genotype", "group"}:
                score += 100
            if score > best_score:
                best_score = score
                best = candidate

        recovered = recover_ratio_measurement_table(raw)
        if recovered is not None and not recovered.empty:
            score = _score_dataframe(recovered) + 120  # prefer ratio→tidy recovery
            if score > best_score:
                best_score = score
                best = recovered
                continue

        # Also score promoted-header raw even without ratio recovery
        header_i = _find_header_row(raw)
        if header_i is not None:
            promoted = _promote_header(raw, header_i)
            score = _score_dataframe(promoted)
            if score > best_score:
                best_score = score
                best = promoted

    if best is None or best_score < 0:
        raise ValueError("数値列が見つかりません")
    return best


def _source_data_tidy(path: Path) -> pd.DataFrame | None:
    """Source Data workbook → one label per block, value = plotted column."""
    from pre_peer_checker.data.source_data_blocks import parse_source_data_blocks

    blocks = parse_source_data_blocks(path)
    if not blocks:
        return None
    labels: list[str] = []
    values: list[float] = []
    for b in blocks:
        if b.layout in {"large", "matrix"}:
            continue
        for group, vals in b.groups:
            key = b.group_key(group)
            labels.extend([key] * len(vals))
            values.extend(vals)
    if not values:
        return None
    return pd.DataFrame({"label": labels, "value": values})


def _load_text_table(path: Path) -> pd.DataFrame:
    """CSV / TSV / TXT export; a preamble above the header row is skipped."""
    from pre_peer_checker.data.table_grid import find_header_row, grid_to_frame, read_grids

    grids = read_grids(path)
    if not grids or not grids[0].rows:
        raise ValueError(f"空のファイル: {path.name}")
    grid = grids[0]
    header_at = find_header_row(grid.rows)
    return grid_to_frame(grid, header_at if header_at is not None else 0)


def load_table(path: Path | str) -> pd.DataFrame:
    from pre_peer_checker.data.table_grid import EXCEL_SUFFIXES, TEXT_SUFFIXES, _table_suffix

    path = Path(path)
    suf = _table_suffix(path)
    if suf in TEXT_SUFFIXES:
        return _load_text_table(path)
    if suf in EXCEL_SUFFIXES:
        tidy = _source_data_tidy(path)
        if tidy is not None:
            return tidy
        return _load_excel_best(path)
    raise ValueError(f"Unsupported table format: {path.suffix}")


def infer_group_and_value(
    df: pd.DataFrame,
) -> tuple[str | None, str]:
    """群列・値列をヒューリスティック推定."""
    # Prefer explicit biology quant schema
    cols_l = {str(c).lower(): c for c in df.columns}
    if "value" in cols_l:
        value_col = cols_l["value"]
        for g in ("label", "genotype", "group", "condition"):
            if g in cols_l:
                return cols_l[g], value_col
        return None, value_col

    ratio_cols = [c for c in df.columns if "ratio" in str(c).lower()]
    if ratio_cols:
        return None, ratio_cols[0]

    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    # Columns that become numeric after coercion
    if not numeric:
        for c in df.columns:
            if pd.to_numeric(df[c], errors="coerce").notna().sum() >= 3:
                numeric.append(c)
    categorical = [
        c
        for c in df.columns
        if c not in numeric and df[c].nunique(dropna=True) <= max(20, len(df) // 2)
    ]
    group_col = categorical[0] if categorical else None
    # Prefer non-ID-like numeric columns
    value_candidates = [
        c
        for c in numeric
        if c != group_col and str(c).lower() not in {"id", "index", "unnamed: 0"}
    ]
    if not value_candidates:
        value_candidates = [c for c in numeric if c != group_col]
    if not value_candidates:
        raise ValueError("数値列が見つかりません")
    return group_col, value_candidates[0]


def describe_groups(
    df: pd.DataFrame,
    *,
    group_col: str | None = None,
    value_col: str | None = None,
) -> StatsResult:
    if group_col is None or value_col is None:
        inferred_g, inferred_v = infer_group_and_value(df)
        group_col = group_col or inferred_g
        value_col = value_col or inferred_v
    if not pd.api.types.is_numeric_dtype(df[value_col]):
        df = df.copy()
        df[value_col] = pd.to_numeric(df[value_col], errors="coerce")

    groups: list[GroupStats] = []
    if group_col is None:
        series = df[value_col].dropna()
        groups.append(
            GroupStats(
                group="all",
                n=int(series.shape[0]),
                mean=float(series.mean()),
                sd=float(series.std(ddof=1)) if len(series) > 1 else 0.0,
                median=float(series.median()),
            )
        )
        return StatsResult(
            path=Path("."),
            value_col=value_col,
            group_col=None,
            groups=groups,
        )

    for name, part in df.groupby(group_col, dropna=False):
        series = part[value_col].dropna()
        groups.append(
            GroupStats(
                group=str(name),
                n=int(series.shape[0]),
                mean=float(series.mean()) if len(series) else float("nan"),
                sd=float(series.std(ddof=1)) if len(series) > 1 else 0.0,
                median=float(series.median()) if len(series) else float("nan"),
            )
        )

    pairwise: list[dict[str, Any]] = []
    anova: dict[str, Any] | None = None
    hints: list[str] = []
    series_by_group = [
        part[value_col].dropna().to_numpy(dtype=float)
        for _, part in df.groupby(group_col, dropna=False)
    ]
    series_by_group = [s for s in series_by_group if len(s) > 0]

    if len(series_by_group) == 2:
        a, b = series_by_group
        t_stat, p_student = stats.ttest_ind(a, b, equal_var=True)
        _, p_welch = stats.ttest_ind(a, b, equal_var=False)
        pairwise.append(
            {
                "test": "student_t",
                "statistic": float(t_stat),
                "p_value": float(p_student),
            }
        )
        pairwise.append(
            {
                "test": "welch_t",
                "statistic": float(t_stat),
                "p_value": float(p_welch),
            }
        )
    elif len(series_by_group) >= 3:
        f_stat, p_anova = stats.f_oneway(*series_by_group)
        anova = {"test": "one_way_anova", "statistic": float(f_stat), "p_value": float(p_anova)}
        hints.append(
            "3群以上: 多重比較補正なしの両側 t 検定のみが報告されている場合は "
            "Warning [統計手法の不整合] の候補"
        )
        # 単純な pairwise Welch（Bonferroni 示唆用）
        names = [g.group for g in groups]
        for i in range(len(series_by_group)):
            for j in range(i + 1, len(series_by_group)):
                t_stat, p = stats.ttest_ind(
                    series_by_group[i], series_by_group[j], equal_var=False
                )
                pairwise.append(
                    {
                        "test": "welch_t_uncorrected",
                        "group_a": names[i],
                        "group_b": names[j],
                        "statistic": float(t_stat),
                        "p_value": float(p),
                    }
                )

    return StatsResult(
        path=Path("."),
        value_col=value_col,
        group_col=group_col,
        groups=groups,
        pairwise=pairwise,
        anova=anova,
        warnings_hints=hints,
    )


def analyze_table_file(path: Path | str) -> StatsResult:
    path = Path(path)
    df = load_table(path)
    result = describe_groups(df)
    result.path = path
    from pre_peer_checker.data.source_data_blocks import is_source_data_workbook

    if is_source_data_workbook(path):
        # blocks belong to different panels/units; cross-block tests are meaningless
        result.pairwise = []
        result.anova = None
        result.warnings_hints = []
    return result


def total_n(result: StatsResult) -> int:
    return sum(g.n for g in result.groups)
