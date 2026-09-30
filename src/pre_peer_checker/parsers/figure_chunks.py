"""原稿を Figure 単位チャンクに分割（Legend / Results 言及 / Methods）。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pre_peer_checker.parsers.legend_struct import (
    StructuredLegend,
    extract_structured_legends_from_paragraphs,
    legend_figure_name,
    match_legend_head,
)
from pre_peer_checker.parsers.manuscript_text import manuscript_paragraphs

# Section headings (English life-science manuscripts)
_SECTION_RE = re.compile(
    r"^(?:"
    r"Abstract|Introduction|Results?|Discussion|"
    r"(?:Materials?\s+and\s+|STAR\W*|Online\s+)?Methods|Method\s+Details|"
    r"Experimental\s+Procedures|(?:Quantification\s+and\s+)?Statistical\s+Analysis|"
    r"References|Acknowledgments?|Author\s+Contributions|"
    r"Competing\s+Interests|Data\s+Availability|"
    r"Supplementary\s+(?:Information|Materials?|Note)|"
    r"Figure\s+Legends?|Table\s+Legends?"
    r")\b",
    re.IGNORECASE,
)

_FIG_MENTION_RE = re.compile(
    r"(?:(?:Supplementa(?:ry|l)|Appendix)\s+Fig(?:ure)?s?\.?|"
    r"(?:Extended\s+Data\s+)?Figs?\.?|(?:Extended\s+Data\s+)?Figures?)\s*"
    r"(S?\d+)(?:\s*[–\-]\s*(S?\d+))?(?:\s*[,/]\s*(S?\d+))*"
    r"(?:\s*[A-Za-z]\d*[’']?)?",
    re.IGNORECASE,
)

_ED_KEY_RE = re.compile(r"extended[\s_-]*data\D{0,12}?(\d+)|(?<![A-Za-z])ED[\s_-]?(\d+)", re.I)


@dataclass
class FigureChunk:
    """One figure's text bundle for check-item extraction (LLM or rules)."""

    figure_id: str
    figure_num: str
    legend: str = ""
    results: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    methods_includes_shared: bool = False
    panel_labels_from_figure: list[str] = field(default_factory=list)
    panel_labels_needs_review: bool = False

    def prompt_body(self, *, max_chars: int = 6000) -> str:
        """Assemble labeled sections for an LLM prompt."""
        parts = [f"[figure_id] {self.figure_id}"]
        if self.panel_labels_from_figure:
            labels = ", ".join(self.panel_labels_from_figure)
            hint = (
                " (raster OCR — verify letters on the figure artwork)"
                if self.panel_labels_needs_review
                else ""
            )
            parts.append(
                f"[figure_panel_labels]\nPanels detected on figure PDF/image{hint}: {labels}"
            )
        parts.append(f"[legend]\n{self.legend.strip() or '(none)'}")
        results = "\n\n".join(p.strip() for p in self.results if p.strip())
        parts.append(f"[results_mentions]\n{results or '(none)'}")
        methods = "\n\n".join(p.strip() for p in self.methods if p.strip())
        shared_note = " (includes shared Methods)" if self.methods_includes_shared else ""
        parts.append(f"[methods_related{shared_note}]\n{methods or '(none)'}")
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            return text[: max_chars - 20] + "\n…[truncated]"
        return text

    def to_dict(self) -> dict:
        d = asdict(self)
        d["prompt_chars"] = len(self.prompt_body())
        return d


def normalize_figure_id(kind: str, num: str) -> str:
    kind_n = kind.replace("Fig.", "Figure").replace("Fig", "Figure")
    if "supplementary" in kind_n.lower():
        return f"Supplementary Figure {num.upper()}"
    if kind_n.lower().startswith("extended"):
        return f"Extended Data Figure {num.upper()}"
    return f"Figure {num.upper()}"


def figure_num_key(figure_id: str) -> str:
    """'1' / 'S2' / 'ED3' / 'T1' (Extended Data, supplementary and tables keep their own numbering)."""
    m = _ED_KEY_RE.search(figure_id or "")
    if m:
        return f"ED{m.group(1) or m.group(2)}"
    m = re.search(r"(S?\d+)", figure_id, re.I)
    if not m:
        return figure_id.upper()
    key = m.group(1).upper()
    head = figure_id[: m.start()].strip().lower()
    if head.startswith("table"):
        return f"T{key}"
    if head.startswith(("supplement", "appendix")) and not key.startswith("S"):
        return f"S{key}"
    return key


def figure_label(key: str) -> str:
    """Display name for a figure key: 'ED3' → 'Extended Data Figure 3', '2' → 'Figure 2'."""
    if key.upper().startswith("ED"):
        return f"Extended Data Figure {key[2:]}"
    if key.upper().startswith("T"):
        return f"Table {key[1:]}"
    return f"Figure {key}"


def _classify_section(text: str) -> str | None:
    m = _SECTION_RE.match(text.strip())
    if not m:
        return None
    head = m.group(0).lower()
    if head.startswith("result"):
        return "results"
    if "method" in head or "experimental" in head or "statistical" in head:
        return "methods"
    if "figure legend" in head or "table legend" in head:
        return "legends"
    if head.startswith("discussion"):
        return "discussion"
    if head.startswith("intro"):
        return "introduction"
    if head.startswith("abstract"):
        return "abstract"
    return "other"


def _expand_fig_nums(match: re.Match[str]) -> set[str]:
    """Expand 'Fig. 1–3' / 'Figs. 1, 2' style mentions to figure number keys."""
    nums: set[str] = set()
    g1 = match.group(1)
    g2 = match.group(2)
    if g1:
        nums.add(g1.upper())
    if g2:
        # range: S?\d+ – S?\d+
        a, b = g1.upper(), g2.upper()
        if a.startswith("S") == b.startswith("S"):
            prefix = "S" if a.startswith("S") else ""
            try:
                start = int(a.lstrip("S"))
                end = int(b.lstrip("S"))
                if 0 < end - start <= 8:
                    for i in range(start, end + 1):
                        nums.add(f"{prefix}{i}")
            except ValueError:
                nums.add(b)
        else:
            nums.add(b)
    # trailing comma list in group 0
    for m in re.finditer(r"(S?\d+)", match.group(0), re.I):
        nums.add(m.group(1).upper())
    if match.group(0).lower().startswith("extended"):
        return {f"ED{n}" for n in nums if not n.startswith("S")}
    if match.group(0).lower().startswith(("supplement", "appendix")):
        return {n if n.startswith("S") else f"S{n}" for n in nums}
    return nums


def figure_nums_in_text(text: str) -> set[str]:
    out: set[str] = set()
    for m in _FIG_MENTION_RE.finditer(text or ""):
        out |= _expand_fig_nums(m)
    return out


def _tag_paragraphs(paragraphs: list[str]) -> list[tuple[str, str]]:
    """Return (section, text) for each paragraph."""
    section = "body"
    tagged: list[tuple[str, str]] = []
    for p in paragraphs:
        if match_legend_head(p):
            tagged.append(("legend_block", p))
            continue
        sec = _classify_section(p)
        if sec is not None:
            section = sec
            # keep the heading itself in that section (useful for Methods)
            tagged.append((section, p))
            continue
        tagged.append((section, p))
    return tagged


def _shared_methods_paras(methods_paras: list[str], *, max_paras: int = 20) -> list[str]:
    """Prefer statistical / analysis paragraphs; else first N Methods paras."""
    keyed = [
        p
        for p in methods_paras
        if re.search(
            r"statistic|t[- ]?test|anova|sample\s*size|\bn\s*=|replicat|graphpad|prism|r\s+version",
            p,
            re.I,
        )
    ]
    if keyed:
        return keyed[:max_paras]
    # skip pure heading-only lines when possible
    body = [p for p in methods_paras if not _SECTION_RE.match(p.strip())]
    return (body or methods_paras)[:max_paras]


def build_figure_chunks_from_paragraphs(
    paragraphs: list[str],
    legends: list[StructuredLegend] | None = None,
) -> list[FigureChunk]:
    """Build per-figure chunks from ordered manuscript paragraphs + optional legends."""
    tagged = _tag_paragraphs(paragraphs)

    legend_by_num: dict[str, StructuredLegend] = {}
    if legends:
        for leg in legends:
            legend_by_num[figure_num_key(leg.figure)] = leg
    else:
        # synthesize minimal legends from legend_block runs
        i = 0
        while i < len(tagged):
            sec, text = tagged[i]
            if sec != "legend_block":
                i += 1
                continue
            m = match_legend_head(text)
            if not m:
                i += 1
                continue
            fig_id = legend_figure_name(m.group(1), m.group(2))
            parts = [text]
            j = i + 1
            while j < len(tagged) and tagged[j][0] != "legend_block":
                if match_legend_head(tagged[j][1]):
                    break
                if tagged[j][0] in {"legends", "legend_block", "body"} or j == i + 1:
                    parts.append(tagged[j][1])
                j += 1
                if len(parts) > 12:
                    break
            legend_by_num[figure_num_key(fig_id)] = StructuredLegend(
                figure=fig_id, text=" ".join(parts)
            )
            i = j

    results_paras = [t for s, t in tagged if s == "results"]
    methods_paras = [t for s, t in tagged if s == "methods"]
    # If no explicit Results heading, use non-methods body for mention search
    if not results_paras:
        results_paras = [
            t
            for s, t in tagged
            if s in {"body", "introduction", "discussion", "abstract"}
            and not match_legend_head(t)
        ]

    shared_methods = _shared_methods_paras(methods_paras)
    mention_index: dict[str, list[str]] = {}
    for p in results_paras:
        for num in figure_nums_in_text(p):
            mention_index.setdefault(num, []).append(p)
    methods_mention: dict[str, list[str]] = {}
    for p in methods_paras:
        for num in figure_nums_in_text(p):
            methods_mention.setdefault(num, []).append(p)

    # Seed figures from legends; also include heavily mentioned figs without legends
    fig_nums = set(legend_by_num.keys())
    for num, paras in mention_index.items():
        if len(paras) >= 2:
            fig_nums.add(num)

    chunks: list[FigureChunk] = []
    for num in sorted(fig_nums, key=_fig_sort_key):
        leg = legend_by_num.get(num)
        figure_id = leg.figure if leg else figure_label(num)
        methods = list(methods_mention.get(num, []))
        includes_shared = False
        if shared_methods:
            # Always attach shared statistical Methods (dedupe)
            seen = {m.strip() for m in methods}
            for sm in shared_methods:
                if sm.strip() not in seen:
                    methods.append(sm)
                    includes_shared = True
                    seen.add(sm.strip())
        chunks.append(
            FigureChunk(
                figure_id=figure_id,
                figure_num=num,
                legend=(leg.text if leg else ""),
                results=list(dict.fromkeys(mention_index.get(num, []))),
                methods=list(dict.fromkeys(methods)),
                methods_includes_shared=includes_shared,
            )
        )
    return chunks


def _fig_sort_key(num: str) -> tuple:
    digits = re.sub(r"\D", "", num) or "0"
    return (num.startswith("T"), num.startswith("ED"), num.startswith("S"), int(digits), num)


def _series(key: str) -> str:
    return re.sub(r"\d+$", "", key)


def legend_coverage(
    paragraphs: list[str],
    legends: list[StructuredLegend],
    figure_file_keys: Iterable[str] = (),
) -> dict:
    """Figures the manuscript refers to vs figures whose legend was found.

    Expected figures come from body-text mentions (``Fig. 3``) and the supplied
    figure files. Mentions past the known figures (``Fig. 9`` of a cited paper)
    count only while the numbering stays contiguous from 1. Extended Data and
    supplementary series are expected only when their legends or files are here,
    since those legends often live in a separate document.
    """
    found = {figure_num_key(leg.figure) for leg in legends if (leg.text or "").strip()}
    found = {k for k in found if not k.startswith("T")}
    files = {k for k in figure_file_keys if k and k != "*"}
    mentioned: set[str] = set()
    for sec, text in _tag_paragraphs(paragraphs):
        if sec in {"legend_block", "legends"}:
            continue
        mentioned |= figure_nums_in_text(text)

    expected: set[str] = set()
    for series in ("", "S", "ED"):
        known = {k for k in found | files if _series(k) == series}
        if series and not known:
            continue
        said = {k for k in mentioned if _series(k) == series}
        nums = {int(k[len(series) :]) for k in said | known if k[len(series) :].isdigit()}
        run = 0
        while run + 1 in nums:
            run += 1
        limit = max([run, *(int(k[len(series) :]) for k in known)])
        expected |= {k for k in said | known if int(k[len(series) :]) <= limit}
    missing = expected - found
    return {
        "expected": sorted(expected, key=_fig_sort_key),
        "found": sorted(found, key=_fig_sort_key),
        "missing": sorted(missing, key=_fig_sort_key),
    }


def build_figure_chunks_from_docx(path: Path | str) -> list[FigureChunk]:
    """Figure chunks from a Word (.docx) or PDF manuscript."""
    paragraphs = manuscript_paragraphs(path)
    try:
        legends = extract_structured_legends_from_paragraphs(paragraphs)
    except Exception:  # noqa: BLE001
        legends = []
    return build_figure_chunks_from_paragraphs(paragraphs, legends)


def chunks_to_artifact(chunks: list[FigureChunk]) -> list[dict]:
    return [c.to_dict() for c in chunks]
