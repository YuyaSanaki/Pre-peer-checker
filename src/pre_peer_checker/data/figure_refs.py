"""Figure / panel labels as they appear in data files.

Source Data and lab workbooks name the plotted panel in many ways:

* a title cell: ``Fig. 4h``, ``Fig.1c``, ``Extended Data Fig. 3b``, ``Figure 2B–D``
* the sheet name: ``Fig.1c``, ``Fid.1e`` (typo), ``Source data for Extended Fig 1``,
  or just ``2a`` / ``3bf`` / ``6b.p1`` when the file is the figure's Source Data
* the file name: ``Source_Data_Fig1.xlsx``, ``MOESM09_Source_Data_ED_Fig1.xlsx``

Multi-letter panels (``bcdefg``, ``c–e``, ``a,b``) expand to every letter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_EXT_WORDS = (
    r"extended(?:\s*data)?|ext\.?\s*data|ed|supplementary|supplemental|suppl?\.?|"
    r"supporting|appendix"
)
_PANEL_RUN = r"[a-z](?:\s*(?:[-–—,&+]|and)?\s*[a-z])*"

_FULL_LABEL_RE = re.compile(
    rf"^\s*(?:source\s*data\s*(?:for|of|to)?\s*)?"
    rf"(?P<ext>{_EXT_WORDS})?\s*[-_.]?\s*"
    r"(?:fig(?:ure)?s?|fid|fg)\s*\.?\s*[-_]?\s*"
    r"(?P<num>S?\d{1,2})\s*[-_.]?\s*"
    rf"(?P<panels>{_PANEL_RUN})?"
    r"(?:\s*[-_.]?\s*(?:p(?:art)?|panel)?\s*\d{1,2})?"
    r"\s*[.:]?\s*$",
    re.IGNORECASE,
)
_ED_SHORT_RE = re.compile(
    rf"^\s*(?:ed|edf|exd)\s*[-_.]?\s*(?P<num>\d{{1,2}})\s*(?P<panels>{_PANEL_RUN})?\s*$",
    re.IGNORECASE,
)
_SHORT_SHEET_RE = re.compile(
    r"^\s*(?P<num>S?\d{1,2})?\s*[-_.]?\s*(?P<panels>[a-z]{1,16}(?:\s*[-–,]\s*[a-z])*)"
    r"(?:\s*[._\-\s]\s*(?:p(?:art)?)?\s*\d{1,2})?\s*$",
    re.IGNORECASE,
)
_FILE_FIG_RE = re.compile(
    rf"(?:^|[^a-z])(?P<ext>{_EXT_WORDS})?[\s_.-]*"
    r"(?:fig(?:ure)?s?)[\s_.-]*(?P<num>S?\d{1,2})(?![0-9])"
    rf"(?:[\s_.-]*(?P<panels>[a-z](?:[a-z]{{0,8}})))?(?![a-z])",
    re.IGNORECASE,
)
_SOURCE_HINT_RE = re.compile(r"source[\s_.-]*data|sourcedata|_esm\b|moesm", re.IGNORECASE)


@dataclass(frozen=True)
class FigureLabel:
    number: str  # "4", "S2"
    panels: tuple[str, ...] = ()
    extended: bool = False

    @property
    def panel(self) -> str:
        return self.panels[0] if self.panels else ""


def _is_extended(ext: str | None) -> bool:
    return bool(ext and ext.strip())


def valid_panel_run(run: str | None) -> bool:
    """Letters written together (``bcf``) must be in alphabetical order, unlike words."""
    if not run:
        return True
    for chunk in re.split(r"\s*(?:[-–—,&+]|\band\b|\s)\s*", run.lower()):
        letters = [ch for ch in chunk if ch.isalpha()]
        if any(b <= a for a, b in zip(letters, letters[1:])):
            return False
    return True


def expand_panels(run: str | None) -> tuple[str, ...]:
    """``"c–e"`` → (c, d, e); ``"bcf"`` → (b, c, f); ``"a, b"`` → (a, b)."""
    if not run or not valid_panel_run(run):
        return ()
    s = run.lower().replace("and", ",")
    out: list[str] = []
    i = 0
    letters = [ch for ch in s if ch.isalpha() or ch in "-–—"]
    while i < len(letters):
        ch = letters[i]
        if ch.isalpha():
            if (
                i + 2 < len(letters)
                and letters[i + 1] in "-–—"
                and letters[i + 2].isalpha()
                and letters[i + 2] > ch
            ):
                out.extend(chr(c) for c in range(ord(ch), ord(letters[i + 2]) + 1))
                i += 3
                continue
            out.append(ch)
        i += 1
    seen: list[str] = []
    for p in out:
        if p not in seen:
            seen.append(p)
    return tuple(seen)


def parse_figure_label(text: str) -> FigureLabel | None:
    """Whole-string figure label (cell or sheet name); None for anything else."""
    if not text or len(text) > 60:
        return None
    m = _FULL_LABEL_RE.match(text)
    if m and valid_panel_run(m.group("panels")):
        return FigureLabel(
            number=m.group("num").upper(),
            panels=expand_panels(m.group("panels")),
            extended=_is_extended(m.group("ext")),
        )
    m = _ED_SHORT_RE.match(text)
    if m and valid_panel_run(m.group("panels")):
        return FigureLabel(
            number=m.group("num"),
            panels=expand_panels(m.group("panels")),
            extended=True,
        )
    return None


def figure_from_filename(path: Path | str) -> FigureLabel | None:
    """Figure the file is the Source Data of (``Source_Data_ED_Fig3.xlsx``)."""
    stem = Path(path).stem
    hits = list(_FILE_FIG_RE.finditer(stem))
    if len(hits) != 1:
        return None
    m = hits[0]
    panels = m.group("panels") or ""
    # "Fig1_source" style words after the number are not panel letters
    panel_t = expand_panels(panels) if 0 < len(panels) <= 3 else ()
    return FigureLabel(
        number=m.group("num").upper(),
        panels=panel_t,
        extended=_is_extended(m.group("ext")),
    )


def looks_like_source_data_file(path: Path | str) -> bool:
    return bool(_SOURCE_HINT_RE.search(Path(path).name))


def parse_short_sheet_label(name: str) -> tuple[str | None, tuple[str, ...]] | None:
    """``"2a"`` → ("2", (a,)); ``"bc"`` → (None, (b, c)); ``"6b.p1"`` → ("6", (b,))."""
    m = _SHORT_SHEET_RE.match(name or "")
    if not m or not valid_panel_run(m.group("panels")):
        return None
    num = m.group("num")
    panels = expand_panels(m.group("panels"))
    if not panels:
        return None
    if num is None and len(panels) > 6:
        return None  # a word, not a panel run
    return (num.upper() if num else None), panels


def sheet_figure_label(
    sheet: str,
    *,
    file_label: FigureLabel | None,
    short_names_ok: bool,
) -> FigureLabel | None:
    """Figure for a sheet name, using the file's figure when the sheet only gives panels."""
    full = parse_figure_label(sheet)
    if full is not None:
        if file_label is not None and full.number == file_label.number and not full.extended:
            return FigureLabel(full.number, full.panels, file_label.extended)
        return full
    if not short_names_ok:
        return None
    short = parse_short_sheet_label(sheet)
    if short is None:
        return None
    num, panels = short
    if num is None:
        if file_label is None:
            return None
        return FigureLabel(file_label.number, panels, file_label.extended)
    extended = bool(file_label and file_label.number == num and file_label.extended)
    return FigureLabel(num, panels, extended)


def workbook_uses_short_sheet_names(sheets: list[str]) -> bool:
    """True when most sheets are named like ``2a`` / ``4bc`` (one sheet per panel)."""
    named = [s for s in sheets if s.strip()]
    if not named:
        return False
    hits = 0
    for s in named:
        short = parse_short_sheet_label(s)
        if short is not None and short[0] is not None:
            hits += 1
    return hits >= max(1, (len(named) + 1) // 2)
