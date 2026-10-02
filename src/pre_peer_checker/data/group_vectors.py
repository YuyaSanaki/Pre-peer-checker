"""群ベクトルの抽出と同一性判定（汎用パターン P-DATA-SWAP / P-SHARED-CONTROL）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from pre_peer_checker.data.stats_recalc import infer_group_and_value, load_table


@dataclass(frozen=True)
class GroupVector:
    source: Path
    group_key: str
    values: tuple[float, ...]
    n: int
    sheet: str = ""

    def rounded(self, decimals: int = 6) -> tuple[float, ...]:
        return tuple(round(v, decimals) for v in self.values)


def group_key_from_path(path: Path) -> str:
    """Heuristic group label from experimental filename (when sheet has no group col)."""
    from pre_peer_checker.engine.case_profile import get_case_profile

    stem = path.stem.lower().replace(" ", "").replace("_", "").replace("-", "")
    for needle, key in get_case_profile().filename_group_rules:
        if needle in stem:
            return key
    if stem.startswith("cont") or "control" in stem:
        return "cont"
    if stem == "wt" or stem.startswith("wt") or "wildtype" in stem:
        return "wt"
    return "all"


_COUNT_COL_RE = re.compile(r"^(?:number|count|counts|n|freq|frequency|no\.? of .+|number of .+)$", re.I)


def count_table_vectors(path: Path) -> list[GroupVector]:
    """Contingency sheets (``Genotype | Class | number``): n per group is the summed count.

    Each row counts animals of one group in one category, so the rows are not samples.
    One vector per group and sheet; empty when no sheet has that shape.
    """
    from pre_peer_checker.data.stats_recalc import explicit_group_column
    from pre_peer_checker.data.table_grid import EXCEL_SUFFIXES, _table_suffix, find_header_row, grid_to_frame, read_grids

    if _table_suffix(path) not in EXCEL_SUFFIXES:
        return []
    out: list[GroupVector] = []
    for grid in read_grids(path):
        if not grid.rows:
            continue
        df = grid_to_frame(grid, find_header_row(grid.rows) or 0)
        if df is None or df.empty:
            continue
        group_col = explicit_group_column(df)
        count_col = next((c for c in df.columns if _COUNT_COL_RE.match(str(c).strip())), None)
        if group_col is None or count_col is None:
            continue
        counts = pd.to_numeric(df[count_col], errors="coerce")
        body = df[counts.notna()]
        counts = counts[counts.notna()]
        if len(counts) < 3 or (counts < 0).any() or not (counts % 1 == 0).all():
            continue
        others = [
            c for c in df.columns
            if c not in {group_col, count_col} and not pd.api.types.is_numeric_dtype(body[c])
            and body[c].nunique(dropna=True) >= 2
        ]
        # every group split over the same categories (a contingency table, not one row per sample)
        if not others or body.groupby(group_col)[others[0]].nunique().min() < 2:
            continue
        for key, part in counts.groupby(body[group_col], sort=False):
            vals = tuple(float(x) for x in part.tolist())
            out.append(GroupVector(path, str(key), vals, int(sum(vals)), grid.sheet))
    return out


def extract_group_vectors(path: Path | str) -> list[GroupVector]:
    path = Path(path)
    counted = count_table_vectors(path)
    if counted:
        return counted
    df = load_table(path)
    # Prefer explicit schema used in biology quant sheets
    cols = {str(c).lower(): c for c in df.columns}
    if {"value", "label"}.issubset(cols):
        group_col, value_col = cols["label"], cols["value"]
    elif {"value", "genotype"}.issubset(cols):
        group_col, value_col = cols["genotype"], cols["value"]
    elif {"value", "group"}.issubset(cols):
        group_col, value_col = cols["group"], cols["value"]
    else:
        group_col, value_col = infer_group_and_value(df)

    # Coerce value column
    values_series = pd.to_numeric(df[value_col], errors="coerce")

    out: list[GroupVector] = []
    if group_col is None:
        vals = tuple(sorted(float(x) for x in values_series.dropna().tolist()))
        key = group_key_from_path(path)
        out.append(GroupVector(path, key, vals, len(vals)))
        return out

    work = df.copy()
    work["_mc_value"] = values_series
    for key, part in work.groupby(group_col, dropna=False):
        vals = tuple(
            sorted(float(x) for x in part["_mc_value"].dropna().tolist())
        )
        gk = str(int(key)) if isinstance(key, float) and key.is_integer() else str(key)
        if gk.lower() in {"all", "nan", "none", ""}:
            gk = group_key_from_path(path)
        out.append(GroupVector(path, gk, vals, len(vals)))
    return out


@dataclass
class VectorMatch:
    a: GroupVector
    b: GroupVector
    exact: bool
    jaccard: float
    is_subset: bool


def compare_vector_pair(a: GroupVector, b: GroupVector, *, decimals: int = 6) -> VectorMatch:
    ra, rb = a.rounded(decimals), b.rounded(decimals)
    sa, sb = set(ra), set(rb)
    exact = ra == rb
    if not sa and not sb:
        jaccard = 1.0
    elif not sa or not sb:
        jaccard = 0.0
    else:
        jaccard = len(sa & sb) / len(sa | sb)
    is_subset = bool(sa and sb and (sa <= sb or sb <= sa)) and not exact
    return VectorMatch(a=a, b=b, exact=exact, jaccard=jaccard, is_subset=is_subset)


def find_cross_table_matches(
    vectors: list[GroupVector],
    *,
    min_n: int = 3,
    jaccard_threshold: float = 0.95,
) -> list[VectorMatch]:
    """異なるソース間で同一／高重複の群ベクトルを探す。"""
    matches: list[VectorMatch] = []
    for a, b in combinations(vectors, 2):
        if a.source.resolve() == b.source.resolve():
            continue
        if a.n < min_n or b.n < min_n:
            continue
        m = compare_vector_pair(a, b)
        if m.exact or m.jaccard >= jaccard_threshold or m.is_subset:
            matches.append(m)
    return matches


_GENERIC_PATH_TOKENS = frozenset({"control", "wt", "kd", "rnai"})


def path_experiment_tokens(path: Path) -> set[str]:
    """パスから実験系トークンを抽出（汎用ヒューリスティック）。"""
    import re

    from pre_peer_checker.engine.case_profile import get_case_profile

    profile = get_case_profile()
    raw = str(path).lower()
    compact = raw.replace("_", "").replace("-", "").replace(" ", "")
    tokens = set()
    for tok in _GENERIC_PATH_TOKENS | profile.path_vocabulary():
        if tok in compact:
            tokens.add(tok)
    for m in re.finditer(r"fig(?:ure)?_?(\d+[a-z]?)", compact):
        tokens.add("fig" + m.group(1).lower())
    side_toks = profile.all_side_tokens()
    for part in path.parts:
        p = part.lower().replace(" ", "")
        for tok in side_toks:
            if tok in p:
                tokens.add(tok)
    return tokens

def experiments_look_distinct(a: Path, b: Path) -> bool:
    ta, tb = path_experiment_tokens(a), path_experiment_tokens(b)
    if not ta or not tb:
        return True  # unknown → still report as possible swap/share
    return ta != tb
