"""Journal "Source Data" workbooks: titled blocks laid out side by side on one sheet.

Typical layout (Nature / Springer ESM, Cell, eLife)::

    Fig. 4h                                  Fig. 4l
    scrib vs wild-type (Fig. 4b)             wild-type vs wild-type (Fig. 4i)
    n | Area clone | Area disc | Rel. size   clone no | Perimeter | cCasp3 | ratio
    1 | 47329      | 336005    | 14.08      1        | 395.6     | 1      | 0.0025
    ...
                     Mean      | 11.27
                     SD        | 2.13

Each header row + the numeric rows below it becomes one :class:`SourceDataBlock`.
The figure label (``Fig. 4h``) is the quantification panel; the reference in the
title (``Fig. 4b``) names the image panel of the condition.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter

_FIG_LABEL_RE = re.compile(
    r"^\s*(?:(?P<ext>extended\s+data|supplementary|supp\.?)\s+)?"
    r"fig(?:ure)?\.?\s*(?P<num>S?\d+)\s*(?P<panel>[a-z])?"
    r"(?:\s*[,–\-]\s*[a-z])*\s*$",
    re.IGNORECASE,
)
_FIG_REF_RE = re.compile(
    r"\b(?:(?P<ext>extended\s+data|supplementary)\s+)?"
    r"fig(?:ure)?s?\.?\s*(?P<num>S?\d+)\s*(?P<panel>[a-z])?\b",
    re.IGNORECASE,
)
_SUMMARY_RE = re.compile(
    r"^\s*(mean|average|ave\.?|avg\.?|sd|s\.d\.?|stdev|std\.?\s*dev\.?|sem|s\.e\.m\.?|"
    r"se|s\.e\.?|median|n|count|sum|total|max|min|variance|var)\s*:?\s*$",
    re.IGNORECASE,
)
_INDEX_HEADER_RE = re.compile(
    r"^\s*(n|no\.?|#|id|sample(\s*(no\.?|id))?|clone\s*(no\.?|#|id)|disc\s*(no\.?|id)|"
    r"dic\s*id|cell\s*(no\.?|#|id)|animal(\s*id)?|mouse(\s*id)?|replicate|rep\.?|"
    r"embryo(\s*(no\.?|id))?|larva(\s*(no\.?|id))?)\s*$",
    re.IGNORECASE,
)
_NUMERIC_STR_RE = re.compile(r"^\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*$")

_SUMMARY_CANON = {
    "mean": "mean", "average": "mean", "ave": "mean", "ave.": "mean", "avg": "mean",
    "avg.": "mean", "sd": "sd", "s.d.": "sd", "s.d": "sd", "stdev": "sd",
    "sem": "sem", "s.e.m.": "sem", "s.e.m": "sem", "se": "sem", "s.e.": "sem",
    "s.e": "sem", "median": "median", "n": "n", "count": "n",
}


@dataclass(frozen=True)
class FigureRef:
    number: str  # "4", "S2"
    panel: str  # "h" or ""
    extended: bool = False

    def label(self) -> str:
        prefix = "Extended Data Fig. " if self.extended else "Fig. "
        return f"{prefix}{self.number}{self.panel}"


@dataclass
class SourceColumn:
    header: str
    values: list[float | None]
    summary: dict[str, float] = field(default_factory=dict)

    def numeric(self) -> list[float]:
        return [v for v in self.values if v is not None]


@dataclass
class SourceDataBlock:
    path: Path
    sheet: str
    header_cell: str
    figure: FigureRef | None
    title: str
    panel_refs: tuple[FigureRef, ...]
    columns: list[SourceColumn]
    has_index: bool

    @property
    def primary(self) -> SourceColumn:
        """Plotted quantity: the column carrying Mean/SD rows, else the last one."""
        with_summary = [c for c in self.columns if c.summary.get("mean") is not None]
        return (with_summary or self.columns)[-1]

    @property
    def n(self) -> int:
        return len(self.primary.numeric())

    @property
    def label(self) -> str:
        parts = []
        if self.figure is not None:
            parts.append(self.figure.label())
        if self.title:
            parts.append(self.title)
        return " | ".join(parts) or f"{self.sheet}!{self.header_cell}"

    @property
    def location(self) -> str:
        return f"{self.path.name} [{self.sheet}!{self.header_cell}]"


def _cell_kind(v: Any) -> tuple[str, Any]:
    if v is None:
        return "empty", None
    if isinstance(v, bool):
        return "str", str(v)
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return "empty", None
        return "num", float(v)
    s = str(v).strip()
    if not s:
        return "empty", None
    if _NUMERIC_STR_RE.match(s):
        return "num", float(s)
    return "str", s


def _read_grids(path: Path) -> list[tuple[str, list[list[tuple[str, Any]]]]]:
    suf = path.suffix.lower()
    grids: list[tuple[str, list[list[tuple[str, Any]]]]] = []
    if suf in {".xlsx", ".xlsm"}:
        import warnings

        from openpyxl import load_workbook

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            wb = load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                rows = [
                    [_cell_kind(v) for v in row]
                    for row in ws.iter_rows(min_row=1, min_col=1, values_only=True)
                ]
                grids.append((ws.title, rows))
        finally:
            wb.close()
    elif suf == ".xls":
        import pandas as pd

        book = pd.read_excel(path, sheet_name=None, header=None)
        for name, df in book.items():
            rows = [[_cell_kind(v) for v in r] for r in df.itertuples(index=False)]
            grids.append((str(name), rows))
    return grids


def _cell(grid: list[list[tuple[str, Any]]], r: int, c: int) -> tuple[str, Any]:
    if r < 0 or r >= len(grid):
        return "empty", None
    row = grid[r]
    if c < 0 or c >= len(row):
        return "empty", None
    return row[c]


def _is_summary(kind: str, v: Any) -> bool:
    return kind == "str" and bool(_SUMMARY_RE.match(v))


def _summary_key(v: str) -> str | None:
    return _SUMMARY_CANON.get(v.strip().rstrip(":").strip().lower())


def _parse_figure_label(s: str) -> FigureRef | None:
    m = _FIG_LABEL_RE.match(s)
    if not m:
        return None
    return FigureRef(
        number=m.group("num").upper(),
        panel=(m.group("panel") or "").lower(),
        extended=bool(m.group("ext")),
    )


def _figure_refs(s: str) -> tuple[FigureRef, ...]:
    return tuple(
        FigureRef(
            number=m.group("num").upper(),
            panel=(m.group("panel") or "").lower(),
            extended=bool(m.group("ext")),
        )
        for m in _FIG_REF_RE.finditer(s)
    )


def _header_runs(grid: list[list[tuple[str, Any]]], r: int) -> list[tuple[int, int]]:
    """Contiguous string runs in row ``r`` that sit on top of numeric rows."""
    row = grid[r]
    runs: list[tuple[int, int]] = []
    c = 0
    while c < len(row):
        if row[c][0] != "str":
            c += 1
            continue
        start = c
        while c < len(row) and row[c][0] == "str":
            c += 1
        end = c - 1
        cells = [row[j][1] for j in range(start, end + 1)]
        if all(_SUMMARY_RE.match(x) and not _INDEX_HEADER_RE.match(x) for x in cells):
            continue
        if end - start + 1 == 1 and _parse_figure_label(cells[0]):
            continue
        below = [_cell(grid, r + 1, j)[0] for j in range(start, end + 1)]
        if below.count("num") * 2 >= len(below) and below.count("num") >= 1:
            runs.append((start, end))
    return runs


def _nearest_figure_label(
    grid: list[list[tuple[str, Any]]], r: int, c0: int, c1: int
) -> FigureRef | None:
    for rr in range(r - 1, -1, -1):
        for cc in range(c0, c1 + 1):
            k, v = _cell(grid, rr, cc)
            if k == "str":
                ref = _parse_figure_label(v)
                if ref is not None:
                    return ref
    best: tuple[int, int, FigureRef] | None = None
    for rr in range(r - 1, -1, -1):
        for cc in range(c0):
            k, v = _cell(grid, rr, cc)
            if k != "str":
                continue
            ref = _parse_figure_label(v)
            if ref is None:
                continue
            score = (r - rr, c0 - cc)
            if best is None or score < best[:2]:
                best = (*score, ref)
    return best[2] if best else None


def _parse_block(
    path: Path,
    sheet: str,
    grid: list[list[tuple[str, Any]]],
    r: int,
    c0: int,
    c1: int,
) -> SourceDataBlock | None:
    headers = [str(_cell(grid, r, c)[1]) for c in range(c0, c1 + 1)]
    cols = [SourceColumn(header=h, values=[]) for h in headers]

    rr = r + 1
    n_data = 0
    while rr < len(grid):
        cells = [_cell(grid, rr, c) for c in range(c0, c1 + 1)]
        if all(k == "empty" for k, _ in cells):
            break
        summary_at = next(
            (i for i, (k, v) in enumerate(cells) if _is_summary(k, v)), None
        )
        if summary_at is not None:
            key = _summary_key(cells[summary_at][1])
            if key is not None:
                for i in range(summary_at + 1, len(cells)):
                    k, v = cells[i]
                    if k == "num":
                        cols[i].summary.setdefault(key, v)
            rr += 1
            continue
        if any(k == "str" for k, _ in cells) or any(c.summary for c in cols):
            break
        for i, (k, v) in enumerate(cells):
            cols[i].values.append(v if k == "num" else None)
        n_data += 1
        rr += 1
    if n_data < 2:
        return None

    has_index = False
    if len(cols) >= 2:
        first = cols[0]
        seq = first.numeric()
        is_seq = len(seq) >= 2 and seq == [float(i) for i in range(1, len(seq) + 1)]
        if _INDEX_HEADER_RE.match(first.header) or (is_seq and not first.summary):
            has_index = True
            cols = cols[1:]
    cols = [c for c in cols if c.numeric()]
    if not cols:
        return None

    title_parts: list[str] = []
    figure: FigureRef | None = None
    for up in range(r - 1, max(-1, r - 5), -1):
        cells = [_cell(grid, up, c) for c in range(c0, c1 + 1)]
        if any(k == "num" for k, _ in cells) or any(_is_summary(k, v) for k, v in cells):
            break
        for k, v in cells:
            if k != "str":
                continue
            ref = _parse_figure_label(v)
            if ref is not None:
                figure = figure or ref
            else:
                title_parts.insert(0, v)
    if figure is None:
        figure = _nearest_figure_label(grid, r, c0, c1)
    if figure is None:
        figure = _parse_figure_label(sheet)
    title = " / ".join(title_parts)

    return SourceDataBlock(
        path=path,
        sheet=sheet,
        header_cell=f"{get_column_letter(c0 + 1)}{r + 1}",
        figure=figure,
        title=title,
        panel_refs=_figure_refs(title),
        columns=cols,
        has_index=has_index,
    )


def _parse_uncached(path: Path) -> list[SourceDataBlock]:
    try:
        grids = _read_grids(path)
    except Exception:  # noqa: BLE001
        return []
    blocks: list[SourceDataBlock] = []
    for sheet, grid in grids:
        for r in range(len(grid)):
            for c0, c1 in _header_runs(grid, r):
                block = _parse_block(path, sheet, grid, r, c0, c1)
                if block is not None:
                    blocks.append(block)
    if not any(b.figure is not None for b in blocks):
        return []
    return blocks


@lru_cache(maxsize=256)
def _parse_cached(path_str: str, mtime_ns: int) -> tuple[SourceDataBlock, ...]:
    return tuple(_parse_uncached(Path(path_str)))


def parse_source_data_blocks(path: Path | str) -> list[SourceDataBlock]:
    """Blocks of a Source Data workbook; empty unless the sheet names a figure."""
    p = Path(path)
    if p.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
        return []
    try:
        st = p.stat()
    except OSError:
        return []
    return list(_parse_cached(str(p.resolve()), st.st_mtime_ns))


def is_source_data_workbook(path: Path | str) -> bool:
    return bool(parse_source_data_blocks(path))
