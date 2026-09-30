"""Figure Legend の構造化（パネル紐付け n / 検定名）。

重要: `n=18 (a) and 10 (b)` の (a)/(b) は図パネルではなく群ラベルのことが多い。
パネル区間 `(K-M) ... quantification (M). n=18 (a)` のように文脈で紐付ける。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

_FIG_START_RE = re.compile(
    r"^(Extended\s+Data\s+Fig(?:ure|\.)?|Supplementa(?:ry|l)\s+Fig(?:ure|\.)?|"
    r"Appendix\s+Fig(?:ure|\.)?|Figure|Fig\.?|Table)\s*(S?\d+)",
    re.IGNORECASE,
)
# After ``Figure 2``: a body sentence (``Figure 2 shows …``) or an eLife sub-item
# (``Figure 1—figure supplement 1``) / continuation, none of which opens a new legend.
_NOT_LEGEND_TAIL_RE = re.compile(
    r"^[A-Za-z]?\s+(?-i:(?:shows?|showed|depicts?|illustrates?|summari[sz]es?|presents?|"
    r"displays?|demonstrates?|indicates?|reveals?|represents?|is|was|are|were|and|or|also|"
    r"in|of|for|to|with|from)\b)"
    r"|^\s*[—–-]\s*(?:figure\s+supplement|source\s+data|video|animation)\b"
    r"|^\s*\(?\s*continued\b",
    re.IGNORECASE,
)


_CONTINUED_RE = re.compile(r"^\s*\(?\s*continued\b\s*\)?\s*[.:]?", re.IGNORECASE)


def match_legend_head(text: str) -> re.Match[str] | None:
    """``Figure 2`` / ``Extended Data Fig. 3`` / ``Supplementary Fig. 1`` legend opener, else None."""
    m = _FIG_START_RE.match(text or "")
    if m is None or _NOT_LEGEND_TAIL_RE.match(text[m.end() :]):
        return None
    return m


def legend_figure_name(kind: str, num: str) -> str:
    """Canonical figure name for a legend head match."""
    k = kind.lower()
    n = num.upper()
    if k.startswith("extended"):
        return f"Extended Data Figure {n}"
    if k.startswith("table"):
        return f"Table {n}"
    if k.startswith(("supplement", "appendix")):
        return f"Supplementary Figure {n if n.startswith('S') else 'S' + n}"
    return f"Figure {n}"
# Typeset PDFs put zero-width / thin spaces inside ``n =​ 22`` and ``P <​ 0.01``.
_INVISIBLE_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_THIN_SPACE_RE = re.compile("[\u00a0\u2002-\u200a\u202f]")


def clean_legend_text(text: str) -> str:
    return _THIN_SPACE_RE.sub(" ", _INVISIBLE_RE.sub("", text or ""))


# Panel section openers: (A)  (D-I)  (K–M)  (N-Q) — NOT n=…(early L3)
_SECTION_OPEN_RE = re.compile(
    r"(?:^|[.\u3002;]\s*|\()\s*"
    r"\("
    r"("
    r"[A-Z]\d?"
    r"(?:\s*[-–—,]\s*[A-Z]\d?)*"
    r"(?:\s+and\s+[A-Z]\d?)?"
    r")"
    r"\)\s+",
)
# Explicit panel list after n=: n=10 (D, E, G and H) / n=10 (N), 9 (O)
# Panel letters must be UPPERCASE (genotype labels use lowercase a/b).
_N_EXPLICIT_PANELS_RE = re.compile(
    r"n\s*=\s*(\d+)\s*\(\s*"
    r"("
    r"[A-Z]\d?"
    r"(?:\s*[,/]\s*[A-Z]\d?)*"
    r"(?:\s+and\s+[A-Z]\d?)?"
    r")\s*\)",
)
_N_SEQ_RE = re.compile(
    r"(?:^|[,;]|\band\b)\s*"
    r"(?:n\s*=\s*)?(\d+)\s*\(\s*([A-Z]\d?)\s*\)",
)
# Genotype / condition lowercase: n=18 (a) and 10 (b)
_N_GROUP_LOWER_RE = re.compile(
    r"(?:n\s*=\s*)?(\d+)\s*\(\s*([a-z])\s*\)",
)
# n=4 (early L3) and 3 (late L3) — values with non-panel labels
_N_LABELED_VALUE_RE = re.compile(
    r"(?:n\s*=\s*)?(\d+)\s*\(\s*([^)]{1,40})\s*\)",
    re.IGNORECASE,
)
_N_BARE_RE = re.compile(r"\bn\s*=\s*(\d+)\b", re.IGNORECASE)
_P_RE = re.compile(r"p\s*[<≤=]\s*([0-9.eE\-]+)", re.IGNORECASE)
_TEST_RE = re.compile(
    r"(Welch.?s?\s*t[- ]?test|Student.?s?\s*t[- ]?test|Dunnett?\s*test|Dunnet\s*test|"
    r"Tukey|ANOVA|Mann[- ]Whitney|Pearson.?s?\s*Chi[- ]squared\s*test)",
    re.IGNORECASE,
)
# Published-PDF writing styles (paper_01 Nature / paper_02 Cell)
_CAPTION_END_RE = re.compile(
    r"(?:Source data are provided|Scale bars?:|See also Figure|Data are mean)",
    re.IGNORECASE,
)
# PLOS ``Fig 1.`` / Sci Rep ``Fig. 1.`` / Frontiers ``FIGURE 1`` (own line); not ``Fig 1B``.
_LINE_CAPTION_RE = re.compile(
    r"^[^\S\n]*(?:Fig\.?|Figure|FIGURE)[^\S\n]*(\d+)(?![^\S\n]*\(Continued\))[^\S\n]*(?:[.|:]|$)",
    re.MULTILINE,
)
_NATURE_BARE_HINT_RE = re.compile(
    r"Fig\.\s*\d+\s*\||\bN\s*=\s*\d+\s+(?:dishes?|images?|ROIs?|aggregates?|fi[bp]ers?)",
    re.IGNORECASE,
)
# Nature ``a–d, Confocal images …`` / ``e, f, Left …`` / ``i, Quantification …`` at sentence start.
_COMMA_OPEN_RE = re.compile(
    r"(?:^|(?<=[.;|] ))"
    r"([a-z](?:\s*[–—-]\s*[a-z])?(?:\s*,\s*[a-z](?:\s*[–—-]\s*[a-z])?)*)"
    r",\s+(?=\S)"
)
_UNIT_WORD_RE = re.compile(
    r"^(?:dishes?|images?|rois?|sarcomere\s+fi[bp](?:er|re)s?|aggregates?|fi[bp]ers?|"
    r"fields?|cells?|mice|animals?|samples?|replicates?|experiments?|fibers?|fibres?)\b",
    re.IGNORECASE,
)
# Quantification clause pointing at a panel: "quantification … (M)"
# Panel letter must be UPPERCASE — IGNORECASE would treat n=140 (k) as panel K.
_QUANT_PANEL_RE = re.compile(
    r"(?i:quantif\w*).{0,80}?\(([A-Z]\d?)\)",
)
# Dose / food / condition labels — not figure panels
_CONDITION_LABEL_RE = re.compile(
    r"^(?:"
    r"\d+x|"
    r"[a-z]|"
    r".*(?:yeast|control|mut|wt|early|late|food|vehicle|rich|opp|hf).*"
    r")$",
    re.IGNORECASE,
)


def _is_condition_label(label: str) -> bool:
    """True for 1x/4x, early L3, control, lowercase a/b — not panel letters A–Z."""
    lab = (label or "").strip()
    if not lab:
        return False
    if re.fullmatch(r"[A-Z]\d?", lab):
        return False
    if re.fullmatch(r"[A-Z]\d?(?:\s*,\s*[A-Z]\d?)+", lab):
        return False
    if re.fullmatch(r"\d+x", lab, re.I):
        return True
    if re.fullmatch(r"[a-z]", lab):
        return True
    if _CONDITION_LABEL_RE.match(lab):
        return True
    # Any non-single-panel-letter parenthesis content is a condition
    return not bool(_expand_panel_token(lab))



@dataclass
class PanelN:
    panel: str
    n: int
    figure: str
    context: str
    group: str = ""


@dataclass
class StructuredLegend:
    figure: str
    text: str
    panel_ns: list[PanelN] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    p_values: list[float] = field(default_factory=list)


def _normalize_panel(p: str) -> str:
    return p.replace("’", "'").replace("'", "").upper()


def _expand_panel_token(token: str) -> list[str]:
    """Expand 'K-M' / 'D, E, G and H' / 'N' into panel letters."""
    token = token.replace("–", "-").replace("—", "-").replace(",", " ")
    token = re.sub(r"\band\b", " ", token, flags=re.I)
    parts = [p.strip() for p in re.split(r"[\s/]+", token) if p.strip()]
    out: list[str] = []
    for part in parts:
        if re.fullmatch(r"[A-Za-z]\d?-[A-Za-z]\d?", part):
            a, b = part.split("-", 1)
            if a[0].isalpha() and b[0].isalpha() and a[0].isupper() and b[0].isupper():
                for code in range(ord(a[0].upper()), ord(b[0].upper()) + 1):
                    out.append(chr(code))
                continue
        if re.fullmatch(r"[A-Za-z]\d?", part):
            out.append(_normalize_panel(part))
    # dedupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _find_section_spans(text: str) -> list[tuple[int, int, list[str]]]:
    """Return (start, end, section_panels) covering legend body."""
    opens: list[tuple[int, list[str]]] = []
    for m in re.finditer(
        r"\("
        r"("
        r"[A-Z]\d?"
        r"(?:\s*[-–—]\s*[A-Z]\d?)?"
        r"(?:\s*,\s*[A-Z]\d?)*"
        r"(?:\s+and\s+[A-Z]\d?)?"
        r")"
        r"\)",
        text,
    ):
        inner = m.group(1).strip()
        # Skip if this looks like n=…(panels) — preceded by digit/= nearby
        pre = text[max(0, m.start() - 12) : m.start()]
        if re.search(r"n\s*=\s*\d+\s*$", pre, re.I) or re.search(r"\d+\s*$", pre):
            # could be "10 (D, E…" after comma in sequence — skip as section
            if re.search(r"(?:n\s*=\s*)?\d+\s*$", pre, re.I):
                continue
        panels = _expand_panel_token(inner)
        if not panels:
            continue
        # Section openers are usually short: single letter, range, or few letters
        if len(inner) > 24:
            continue
        # Prefer boundaries after sentence end
        before = text[max(0, m.start() - 2) : m.start()]
        if m.start() > 0 and not re.search(r"[\.\s\(]$", before) and before[-1:] not in " ":
            # still allow mid-legend "(A) Relative"
            if not re.match(r"^[.\s]*$", before) and "." not in text[max(0, m.start() - 3) : m.start()]:
                # allow if previous char is space after closing paren of prior sentence
                if before not in (" ", "  "):
                    pass
        opens.append((m.start(), panels))

    if not opens:
        return [(0, len(text), [])]

    spans: list[tuple[int, int, list[str]]] = []
    for i, (start, panels) in enumerate(opens):
        end = opens[i + 1][0] if i + 1 < len(opens) else len(text)
        spans.append((start, end, panels))
    # Leading text before first section
    if opens[0][0] > 0:
        spans.insert(0, (0, opens[0][0], []))
    return spans


def _add_pn(
    found: list[PanelN],
    *,
    figure: str,
    panel: str,
    n: int,
    context: str,
    group: str = "",
) -> None:
    panel_u = _normalize_panel(panel)
    if not panel_u:
        return
    if any(
        x.panel == panel_u and x.n == n and x.figure == figure and x.group == group
        for x in found
    ):
        return
    found.append(
        PanelN(
            panel=panel_u,
            n=n,
            figure=figure,
            context=context[:120],
            group=group,
        )
    )


def _lowercase_n_as_panel_typos(
    lower_hits: list[re.Match[str]],
    section_panels: list[str],
    chunk: str,
) -> list[tuple[str, int, str]] | None:
    """Map legend typos like n=140 (k) and 88 (l) → panels L, M.

    Pattern: mid/late alphabet lowercase letters after n=, no real uppercase
    quantification panel, and letter.upper() not in the section opener. Shift +1
    recovers the intended panels (k→L, l→M). Early letters a/b/c remain groups.
    """
    if len(lower_hits) < 1:
        return None
    letters = [m.group(2) for m in lower_hits]
    if not all(len(x) == 1 and x.islower() for x in letters):
        return None
    # Genotype labels are almost always a/b/c/d near the start of the alphabet.
    if any(ord(x) < ord("j") for x in letters):
        return None
    if _QUANT_PANEL_RE.search(chunk):
        return None
    if any(x.upper() in section_panels for x in letters):
        return None
    shifted: list[str] = []
    for x in letters:
        nxt = chr(ord(x.upper()) + 1)
        if not ("A" <= nxt <= "Z"):
            return None
        shifted.append(nxt)
    out: list[tuple[str, int, str]] = []
    for m, panel in zip(lower_hits, shifted, strict=True):
        ctx = chunk[max(0, m.start() - 40) : m.end() + 20]
        out.append((panel, int(m.group(1)), ctx))
    return out


def _trim_published_caption(text: str) -> str:
    """Keep caption body; drop trailing stats boilerplate when present."""
    m = _CAPTION_END_RE.search(text or "")
    if m and m.start() > 80:
        return text[: m.end() + 80]
    return text or ""


def _normalize_caption_text(text: str) -> str:
    t = clean_legend_text(text).replace("\u00ad", "")
    t = t.replace("ﬁ", "fi").replace("ﬂ", "fl")
    t = re.sub(r"-\s*\n\s*", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _published_section_spans(
    text: str, *, nature_bare: bool
) -> list[tuple[int, int, list[str]]]:
    """Section openers for published captions: (A)/(G and H) and optional Nature a/e,f.

    Two or more ``a–d,`` style openers mark a comma-style caption; they replace the
    bare-letter heuristic, which would read ``shown in b (n = 22)`` as a new section.
    """
    opens: list[tuple[int, list[str]]] = []
    comma = [
        (m.start(1), _expand_panel_token(m.group(1).upper()))
        for m in _COMMA_OPEN_RE.finditer(text)
    ]
    if len(comma) >= 2:
        opens.extend((start, panels) for start, panels in comma if panels)
        nature_bare = False
    for m in re.finditer(
        r"\("
        r"("
        r"[A-Za-z]\d?"
        r"(?:\s*[-–—]\s*[A-Za-z]\d?)?"
        r"(?:\s*,\s*[A-Za-z]\d?)*"
        r"(?:\s+and\s+[A-Za-z]\d?)?"
        r")"
        r"\)",
        text,
    ):
        pre = text[max(0, m.start() - 12) : m.start()]
        # `n = 10 (D, E)` / `15 (day 3)` lists — but not gene names like `ATF4 (A and B)`
        if re.search(r"(?<![A-Za-z])\d+\s*$", pre):
            continue
        panels = _expand_panel_token(m.group(1))
        if not panels:
            inner = m.group(1).strip()
            if re.fullmatch(r"[a-z]", inner):
                panels = [inner.upper()]
            elif re.fullmatch(r"[a-z](?:\s*,\s*[a-z])+", inner):
                panels = [x.strip().upper() for x in inner.split(",")]
            elif re.search(r"[a-z].*\band\b.*[a-z]", inner, re.I):
                panels = [x.upper() for x in re.findall(r"[a-z]", inner)]
        after = text[m.end() : m.end() + 3]
        if re.match(r"\s*[–—\-]", after or ""):
            continue  # stats refs like (D)–(F);
        sentence_start = m.start() == 0 or bool(
            re.search(r"[.;]\s*$", text[max(0, m.start() - 3) : m.start()])
        )
        if panels and (
            re.match(r"\s*[A-Z]", after or "")
            or after == ""
            or (sentence_start and re.match(r"\s+[0-9]", after or ""))
        ):
            opens.append((m.start(), panels))
    if nature_bare:
        for m in re.finditer(r"(?:^|[.\s;])([a-z])(?:,([a-z]))?(?=\s+\S)", text):
            panels = [m.group(1).upper()]
            if m.group(2):
                panels.append(m.group(2).upper())
            start = m.start()
            if text[start : start + 1] in ". ;" or text[start : start + 1].isspace():
                start += 1
            opens.append((start, panels))
    opens.sort(key=lambda x: x[0])
    dedup: list[tuple[int, list[str]]] = []
    seen: set[int] = set()
    for start, panels in opens:
        if start in seen:
            continue
        seen.add(start)
        dedup.append((start, panels))
    if not dedup:
        return [(0, len(text), [])]
    spans: list[tuple[int, int, list[str]]] = []
    for i, (start, panels) in enumerate(dedup):
        end = dedup[i + 1][0] if i + 1 < len(dedup) else len(text)
        spans.append((start, end, panels))
    return spans


def _parse_published_pdf_styles(figure: str, text: str) -> list[PanelN]:
    """Four published-caption writing styles used by thin paper gold cases.

    1. Panel-level ``N = 3 dishes`` → section panel, empty group (units ≠ groups)
    2. Timepoint lists ``N = 15 (day 3), …`` / ``14 images … (day 10)``
    3. Shared lowercase ``f,g … N = 57`` → both panels, empty group
    4. Postfix ``(n = 5)`` / ``mice (n = 5)`` / ``(RNA-seq, n = 3)``
    """
    found: list[PanelN] = []
    if not text:
        return found
    nature_bare = bool(_NATURE_BARE_HINT_RE.search(text))
    t = _normalize_caption_text(_trim_published_caption(text))
    if not t:
        return found
    for start, end, section_panels in _published_section_spans(t, nature_bare=nature_bare):
        if not section_panels:
            continue
        chunk = t[start:end]

        # (2) compact day list — capital N= only (avoid n=140 (k) typo / genotype)
        for m in re.finditer(
            r"(?<![A-Za-z])N\s*=\s*(\d+)\s*\(([^)]+)\)"
            r"((?:\s*,?\s*(?:and\s*)?\d+\s*\([^)]+\))*)",
            chunk,
        ):
            values = [(int(m.group(1)), m.group(2).strip())]
            for sm in re.finditer(r"(\d+)\s*\(([^)]+)\)", m.group(3) or ""):
                values.append((int(sm.group(1)), sm.group(2).strip()))
            if values and all(
                (
                    re.match(r"day\s*\d+", lab, re.I)
                    or (
                        _is_condition_label(lab)
                        and not (len(lab) == 1 and lab.islower())
                    )
                )
                for _, lab in values
            ):
                ctx = m.group(0)[:120]
                for n, lab in values:
                    for p in section_panels:
                        _add_pn(
                            found, figure=figure, panel=p, n=n, context=ctx, group=lab
                        )

        # (2b) verbose day list with "images"
        for m in re.finditer(
            r"(?<![A-Za-z])N\s*=\s*(\d+)\s+images?\b.{0,100}?\((day\s*\d+)\)"
            r"((?:\s*,?\s*(?:and\s*)?\d+\s+images?\b.{0,100}?\(day\s*\d+\))*)",
            chunk,
            re.S,
        ):
            values = [(int(m.group(1)), m.group(2).strip())]
            for sm in re.finditer(
                r"(\d+)\s+images?\b.{0,100}?\((day\s*\d+)\)",
                m.group(3) or "",
                re.S,
            ):
                values.append((int(sm.group(1)), sm.group(2).strip()))
            ctx = m.group(0)[:120]
            for n, lab in values:
                for p in section_panels:
                    _add_pn(found, figure=figure, panel=p, n=n, context=ctx, group=lab)

        # (1)(3) panel-level N with unit noun (empty group)
        for m in re.finditer(
            r"(?<![A-Za-z])N\s*=\s*(\d+)\s+"
            r"((?:sarcomere\s+)?"
            r"(?:dishes?|images?|ROIs?|aggregates?|fi[bp]ers?|fibres?|fields?|cells?))"
            r"\b",
            chunk,
        ):
            window = chunk[m.start() : m.end() + 120]
            if re.search(r"\(day\s*\d+\)", window, re.I) and re.search(
                r"images?", m.group(2), re.I
            ):
                continue
            ctx = m.group(0)[:120]
            for p in section_panels:
                _add_pn(found, figure=figure, panel=p, n=int(m.group(1)), context=ctx)

        # (4) postfix (n = N) — Cell / Elsevier style
        for panel, n, ctx, group in _postfix_n_assignments(chunk, section_panels):
            _add_pn(found, figure=figure, panel=panel, n=n, context=ctx, group=group)

    return found


# Groups: 1 cohort word, 2 genotype-panel letter (``shown in b (n = 22)``, ``(n) (n = 30)``;
# not ``24 h (n = 5)``), 3 condition, 4 n, 5 panel list. A unit / descriptor may follow n:
# ``(n = 22 eye discs)`` / ``(n = 19, number of eye discs)``.
_POSTFIX_N_RE = re.compile(
    r"(?:(\b(?:clinical\s+)?patients?\b|\bmice\b)\s+"
    r"|(?<![\w(])(?<!\d )\(?((?-i:[a-z]))\)?\s+)?"
    r"\((?:([^(),]{1,30}?),\s*)?n\s*=\s*(\d+)"
    r"(?:\s*,\s*([A-Z](?:\s*(?:,|and|[-–—])\s*[A-Z])*)(?=\s*\))|[\s,][^()\d]{0,40}?)?\s*\)",
    re.I,
)
_INLINE_PANEL_REF_RE = re.compile(
    r"\(([A-Z](?:\s*(?:,|and|[-–—])\s*[A-Z])*)\)"
)
_SCHEMATIC_RE = re.compile(r"^\(\s*[A-Z]\s*\)\s*(?:Schematic|Scheme)\b", re.I)


def _inline_refs(text: str, section_panels: list[str]) -> list[tuple[int, int, list[str]]]:
    """In-sentence panel refs like ``weight (J)`` that name panels of this section."""
    allowed = set(section_panels)
    out: list[tuple[int, int, list[str]]] = []
    for m in _INLINE_PANEL_REF_RE.finditer(text):
        panels = [p for p in _expand_panel_token(m.group(1)) if p in allowed]
        if panels:
            out.append((m.start(), m.end(), panels))
    return out


def _postfix_n_assignments(
    chunk: str, section_panels: list[str]
) -> list[tuple[str, int, str, str]]:
    """Bind each ``(n = N)`` in a section to the panel(s) it describes.

    A section header ``(J–L)`` names a block; per-panel refs inside it
    (``weight (J) (n = 3), histology (K) (n = 6)``) take precedence over the
    header. Only when a multi-panel block has no inline refs is the n shared.
    """
    hits = list(_POSTFIX_N_RE.finditer(chunk))
    if not hits:
        return []
    if len(section_panels) == 1 and _SCHEMATIC_RE.match(chunk):
        return []

    header = re.match(r"\s*\([^)]*\)", chunk)
    body_start = header.end() if header else 0
    multi = len(section_panels) > 1
    refs = _inline_refs(chunk[body_start:], section_panels) if multi else []
    refs = [(s + body_start, e + body_start, p) for s, e, p in refs]

    labelled = []
    for m in hits:
        prefix = (m.group(1) or "").strip()
        letter = m.group(2) or ""
        cond = (m.group(3) or "").strip()
        group = ""
        if (
            cond
            and not _UNIT_WORD_RE.match(cond)
            and not re.match(r"^(upper|lower|left|right|see)\b", cond, re.I)
        ):
            group = cond
        elif letter:
            group = letter
        elif re.search(r"patients?", prefix, re.I):
            group = "patients"
        elif re.search(r"\bmice\b", prefix, re.I):
            group = "mice"
        labelled.append((m, group, bool(prefix) and not cond, bool(letter) and not cond))
    # "mice (n = 5)" is a group label only when contrasted with another cohort
    cohort_labels = {g for _, g, from_prefix, _ in labelled if from_prefix and g}
    if len(cohort_labels) < 2:
        labelled = [
            (m, "" if from_prefix else g, from_prefix, from_letter)
            for m, g, from_prefix, from_letter in labelled
        ]

    # (G and H) … (RNA-seq, n = 3) and … (ATAC-seq, n = 3): one labelled n per panel, in order.
    # Genotype letters (``shown in b (n = 22)``) name groups of a shared plot, not panels.
    groups = [g for _, g, _, _ in labelled]
    paired = (
        multi
        and not refs
        and len(labelled) == len(section_panels)
        and all(groups)
        and len(set(groups)) == len(groups)
        and not any(from_letter for *_, from_letter in labelled)
    )

    out: list[tuple[str, int, str, str]] = []
    prev_end = body_start
    last_ref_panels: list[str] = []
    for idx, (m, group, _, _) in enumerate(labelled):
        n = int(m.group(4))
        ctx = m.group(0)[:120]
        if m.group(5):
            targets = _expand_panel_token(m.group(5))
        elif paired:
            targets = [section_panels[idx]]
        elif refs:
            before = [r for r in refs if prev_end <= r[0] and r[1] <= m.start()]
            adjacent = [r for r in before if not chunk[r[1] : m.start()].strip()]
            if adjacent:
                targets = adjacent[-1][2]
            elif before:
                targets = [p for r in before for p in r[2]]
            else:
                # trailing "Statistical analysis … (n = 4)" after per-panel clauses
                targets = last_ref_panels or section_panels
        else:
            targets = section_panels
        if refs:
            seen = [r for r in refs if r[1] <= m.start()]
            if seen:
                last_ref_panels = seen[-1][2]
        for p in targets:
            out.append((p, n, ctx, group))
        prev_end = m.end()
    return out


# ``n = 26`` / ``n=8`` / ``n = 67 cells from 14 mice`` (X only); not ranges ``n = 28–32`` or
# decimals.
_N_MENTION_RE = re.compile(r"(?<![A-Za-z])[nN]\s*=\s*(\d+)(?!\d)(?![.,]\d)(?!\s*(?:[–—-]|to)\s*\d)")
_ANY_PANEL_REF_RE = re.compile(r"\(([A-Z](?:\s*(?:,|and|[-–—])\s*[A-Z])*)\)")
_SENTENCE_BREAK_RE = re.compile(r"[.;]\s+(?=[A-Z(])")
# Section header whose letters carry primes: ``(C-F′) TUNEL assay …`` / ``(C′,D′) Orthogonal …``
_HEADER_WITH_PRIMES_RE = re.compile(r"\(([A-Z][′']*(?:\s*(?:,|and|[-–—])\s*[A-Z][′']*)*)\)\s+[A-Z]")
_LABEL_STOP_RE = re.compile(
    r"^(?:and|or|of|from|in|for|with|versus|vs\.?|between|to|the|by|on|at|per|each|both)$", re.I
)


def _group_label_before(pre: str) -> str:
    """``TRPV1+/+ (n = `` / ``R24, n=`` / ``w1118 , n=`` → the label right before the n."""
    m = re.search(r"(.*?)\s*\(\s*$", pre) or re.search(r"(.*?)\s*[,:]\s*$", pre)
    if not m:
        return ""
    seg = re.split(r"[;:.()\[\]]|,(?=\s)", m.group(1))[-1].strip()
    words: list[str] = []
    for w in reversed(seg.split()):
        if _LABEL_STOP_RE.match(w):
            break
        words.insert(0, w)
        if len(words) == 4:
            break
    label = " ".join(words).strip(" ,")
    if not label or _UNIT_WORD_RE.match(label) or len(label) > 40 or label.isdigit():
        return ""
    return label


def _generic_clause_ns(figure: str, text: str, found: list[PanelN]) -> list[PanelN]:
    """``n =`` the specific styles above missed: bare ``n=13.``, ``R24, n=21``, ``WT (n = 67 cells from 14 mice)``.

    Targets: a panel ref right before ``(n =``; else refs since the previous n of the sentence
    (``Circularity (F) and solidity (G) … (n = 76)``), plus the section header when it opens the
    sentence; else the previous n's panels in that sentence; else the section panels. An n
    already read for this section, or followed by its own panel list (``n=12 (E)``), is left to
    the styles above. Groups only when one sentence labels two or more n for the same panels.
    """
    out: list[PanelN] = []
    t = _normalize_caption_text(text)
    if not t:
        return out
    read = {(pn.panel, pn.n) for pn in found}
    nature_bare = bool(_NATURE_BARE_HINT_RE.search(t))
    spans = _published_section_spans(t, nature_bare=nature_bare)
    headed = {p for _, _, panels in spans for p in panels} | {
        p
        for m in _HEADER_WITH_PRIMES_RE.finditer(t)
        for p in _expand_panel_token(re.sub(r"[′']", "", m.group(1)))
    }
    for start, end, section_panels in spans:
        if not section_panels:
            continue
        chunk = t[start:end]
        header = re.match(r"\s*\([^)]*\)", chunk)
        body = header.end() if header else 0
        breaks = [m.end() for m in _SENTENCE_BREAK_RE.finditer(chunk)]

        def refs_in(a: int, b: int) -> list[tuple[int, int, list[str]]]:
            # a ref to a panel with its own section elsewhere points at it (``Myc positive (D)``)
            out_refs = []
            for r in _ANY_PANEL_REF_RE.finditer(chunk, a, b):
                ps = [p for p in _expand_panel_token(r.group(1)) if p in section_panels or p not in headed]
                if ps:
                    out_refs.append((r.start(), r.end(), ps))
            return out_refs

        scope = set(section_panels) | {p for *_, ps in refs_in(body, len(chunk)) for p in ps}
        already = {n for p, n in read if p in scope}
        rows: list[tuple[int, list[str], int, str, str, bool]] = []
        prev: dict[int, tuple[int, list[str]]] = {}
        for m in _N_MENTION_RE.finditer(chunk):
            n = int(m.group(1))
            sent_start = max([b for b in breaks if b <= m.start()], default=0)
            ctx = chunk[max(0, m.start() - 40) : m.end() + 20]
            if re.match(r"\s*(?:\(\s*[A-Za-z][^)]{0,40}\)|,\s*[A-Z]\b)", chunk[m.end() :]):
                prev[sent_start] = (m.end(), prev.get(sent_start, (0, section_panels))[1])
                continue
            if n in already:
                # read without its group (``WT (n = 7) and KO (n = 9)``): offer the label only
                panels = sorted({pn.panel for pn in found if pn.n == n and not pn.group and pn.panel in scope})
                label = _group_label_before(chunk[sent_start : m.start()])
                if panels:
                    rows.append((sent_start, panels, n, ctx, label, True))
                prev[sent_start] = (m.end(), panels or prev.get(sent_start, (0, section_panels))[1])
                continue
            since, last_targets = prev.get(sent_start, (max(sent_start, body), []))
            refs = refs_in(max(since, body), m.start())
            if refs and re.fullmatch(r"\s*\(\s*", chunk[refs[-1][1] : m.start()]):
                targets = refs[-1][2]
            elif refs:
                ps = [p for *_, rp in refs for p in rp]
                targets = list(dict.fromkeys(ps + section_panels if sent_start == 0 else ps))
            else:
                targets = last_targets or section_panels
            prev[sent_start] = (m.end(), targets)
            label = _group_label_before(chunk[sent_start : m.start()])
            rows.append((sent_start, targets, n, ctx, label, False))
        for sent, targets, n, ctx, label, relabel in rows:
            grouped = len({r[4] for r in rows if r[0] == sent and r[1] == targets and r[4]}) >= 2
            if relabel and not (grouped and label):
                continue
            for p in targets:
                _add_pn(out, figure=figure, panel=p, n=n, context=ctx, group=label if grouped else "")
    return out


def parse_panel_ns(figure: str, text: str) -> list[PanelN]:
    """Context-aware panel n extraction (rules fallback / offline path)."""
    found: list[PanelN] = []
    text = clean_legend_text(text)
    if not text:
        return found

    spans = _find_section_spans(text)
    for start, end, section_panels in spans:
        chunk = text[start:end]
        # 1) Explicit uppercase panel lists: n=10 (D, E, G and H) / n=10 (N), 9 (O)
        for m in _N_EXPLICIT_PANELS_RE.finditer(chunk):
            n = int(m.group(1))
            panels = _expand_panel_token(m.group(2))
            ctx = chunk[max(0, m.start() - 20) : m.end() + 20]
            for p in panels:
                _add_pn(found, figure=figure, panel=p, n=n, context=ctx)

        # 2) Sequences n=10 (N), 9 (O), 10 (P)
        # Avoid double-counting those already captured by explicit regex
        for m in _N_SEQ_RE.finditer(chunk):
            n = int(m.group(1))
            p = _normalize_panel(m.group(2))
            ctx = chunk[max(0, m.start() - 20) : m.end() + 20]
            # skip if lowercase was intended — SEQ only matches uppercase
            _add_pn(found, figure=figure, panel=p, n=n, context=ctx)

        # 3) Lowercase group labels: bind to quantification panel or last section panel
        lower_hits = [
            m
            for m in _N_GROUP_LOWER_RE.finditer(chunk)
            if "n=" in chunk[max(0, m.start() - 30) : m.end()].lower()
            or re.search(r"n\s*=", chunk[max(0, m.start() - 80) : m.start()], re.I)
        ]
        # Also accept `n=18 (a) and 10 (b)` where only first has n=
        if re.search(r"n\s*=\s*\d+\s*\(\s*[a-z]\s*\)", chunk):
            lower_hits = list(_N_GROUP_LOWER_RE.finditer(chunk))
        if lower_hits:
            typo_panels = _lowercase_n_as_panel_typos(lower_hits, section_panels, chunk)
            if typo_panels:
                for panel, n, ctx in typo_panels:
                    _add_pn(
                        found,
                        figure=figure,
                        panel=panel,
                        n=n,
                        context=ctx,
                        group="",
                    )
            else:
                quant = _QUANT_PANEL_RE.search(chunk)
                target_panels = (
                    [_normalize_panel(quant.group(1))]
                    if quant
                    else (section_panels[-1:] if section_panels else [])
                )
                for m in lower_hits:
                    n = int(m.group(1))
                    grp = m.group(2)
                    ctx = chunk[max(0, m.start() - 40) : m.end() + 20]
                    for p in target_panels:
                        _add_pn(
                            found,
                            figure=figure,
                            panel=p,
                            n=n,
                            context=ctx,
                            group=grp,
                        )

        # 4) Condition labels (1x, 4x, early L3, control…) under a section → section panels + group
        if section_panels:
            has_upper_panel_n = bool(
                re.search(r"n\s*=\s*\d+\s*\(\s*[A-Z]\d?\s*\)", chunk)
            )
            # Still allow dose labels even if a panel letter also appears later in chunk
            for m in re.finditer(
                r"n\s*=\s*(\d+)\s*\(([^)]+)\)"
                r"((?:\s*,?\s*(?:and\s*)?\d+\s*\([^)]+\))*)",
                chunk,
                re.I,
            ):
                values = [(int(m.group(1)), m.group(2).strip())]
                for sm in re.finditer(r"(\d+)\s*\(([^)]+)\)", m.group(3) or ""):
                    values.append((int(sm.group(1)), sm.group(2).strip()))
                # Skip clauses that are purely uppercase panel lists (handled in 1–2)
                if values and all(not _is_condition_label(lab) for _, lab in values):
                    continue
                if lower_hits and all(
                    _is_condition_label(lab) and len(lab) == 1 and lab.islower()
                    for _, lab in values
                ):
                    continue  # already handled as genotype a/b
                ctx = m.group(0)[:120]
                for n, label in values:
                    if not _is_condition_label(label):
                        # mixed clause: uppercase panel letter inside n= list
                        for p in _expand_panel_token(label):
                            _add_pn(found, figure=figure, panel=p, n=n, context=ctx)
                        continue
                    targets = section_panels
                    # Single-letter section (E) or multi (F-H): attach condition to each
                    # panel in the quantification block (shared dose n across metrics).
                    for p in targets:
                        _add_pn(
                            found,
                            figure=figure,
                            panel=p,
                            n=n,
                            context=ctx,
                            group=label,
                        )

    # 5) Published-PDF styles (Nature N= / Cell (n=)); no-op on hyper Word legends
    for pn in _parse_published_pdf_styles(figure, text):
        _add_pn(
            found,
            figure=pn.figure,
            panel=pn.panel,
            n=pn.n,
            context=pn.context,
            group=pn.group,
        )

    # 6) Any remaining ``n =`` bound by sentence / section position; a labelled row replaces
    #    the same n read without its group
    extra = _generic_clause_ns(figure, text, found)
    labelled = {(pn.panel, pn.n) for pn in extra if pn.group}
    found = [pn for pn in found if pn.group or (pn.panel, pn.n) not in labelled]
    for pn in extra:
        _add_pn(
            found,
            figure=pn.figure,
            panel=pn.panel,
            n=pn.n,
            context=pn.context,
            group=pn.group,
        )

    return found


def extract_figure_captions_from_pdf_text(
    text: str,
    *,
    keep: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Split pdftotext -raw output into (Figure N, caption) pairs.

    Header styles: ``Fig. N |`` (Nature), ``Figure N.`` (Cell), or a line-start
    header (``Fig 1.`` / ``Fig. 1.`` / ``FIGURE 1``; first per number, ``(Continued)``
    repeats skipped). The style covering the most figure numbers wins (ties keep that
    order), so in-text ``Figure 2.`` references do not shadow the real headers.
    ``keep`` filters figure numbers as strings ('1','2',...). Using -raw avoids
    two-column left/right interleaving that breaks panel↔n pairing.
    """
    keep = keep or set()
    text = text or ""
    best: list[re.Match[str]] = []
    for pat in (r"(?<!Data )Fig\.\s*(\d+)\s*\|", r"(?<!Data )Figure\s+(\d+)\.", _LINE_CAPTION_RE):
        hits = list(re.finditer(pat, text))
        if pat is _LINE_CAPTION_RE:
            hits = _first_per_number(hits)
        if len({m.group(1) for m in hits}) > len({m.group(1) for m in best}):
            best = hits
    heads = sorted(
        [(m.start(), "", m.group(1)) for m in best]
        + [(m.start(), "ED", m.group(1)) for m in _first_per_number(_ED_CAPTION_RE.finditer(text))]
    )
    pairs: list[tuple[str, str]] = []
    for i, (start, prefix, num) in enumerate(heads):
        if keep and f"{prefix}{num}" not in keep:
            continue
        end = heads[i + 1][0] if i + 1 < len(heads) else min(len(text), start + 8000)
        figure = f"Extended Data Figure {num}" if prefix else f"Figure {num}"
        pairs.append((figure, _cut_caption_tail(clean_legend_text(text[start:end]))))
    return pairs


_ED_CAPTION_RE = re.compile(
    r"^[^\S\n]*Extended[^\S\n]+Data[^\S\n]+Fig(?:ure|\.)?[^\S\n]*(\d+)[^\S\n]*[.|:]",
    re.MULTILINE | re.IGNORECASE,
)
# Running footer lines (copyright / download stamps) end a caption in -raw output.
_CAPTION_STOP_RE = re.compile(r"^[^\S\n]*(?:©|Downloaded\s+from\b)", re.MULTILINE | re.IGNORECASE)
# -raw also emits the artwork's own text after the caption: axis ticks, genotype and
# panel labels, one short line each. Superscript breaks (``gene−/−`` / ``(k),``) also
# give runs of short lines, but those continue a sentence instead of following its end.
_LABEL_LINE_MAX = 24
_LABEL_RUN = 8


def _first_per_number(hits: Iterable[re.Match[str]]) -> list[re.Match[str]]:
    seen: set[str] = set()
    return [m for m in hits if not (m.group(1) in seen or seen.add(m.group(1)))]


def _cut_caption_tail(caption: str) -> str:
    stop = _CAPTION_STOP_RE.search(caption)
    if stop:
        caption = caption[: stop.start()]
    offset = 0
    run_start: int | None = None
    run = 0
    prev = ""
    for line in caption.split("\n"):
        s = line.strip()
        if s:
            if len(s) <= _LABEL_LINE_MAX and (run or prev.endswith(".")):
                if not run:
                    run_start = offset
                run += 1
                if run >= _LABEL_RUN and run_start:
                    return caption[:run_start].rstrip()
            else:
                run = 0
            prev = s
        offset += len(line) + 1
    return caption.rstrip()


def extract_figure_captions_from_pdf(
    path: Path | str,
    *,
    keep: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Read PDF via ``pdftotext -raw`` (column order) and split captions."""
    import subprocess

    path = Path(path)
    try:
        text = subprocess.check_output(
            ["pdftotext", "-raw", str(path), "-"],
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return extract_figure_captions_from_pdf_text(text, keep=keep)


def extract_structured_legends(path: Path | str) -> list[StructuredLegend]:
    """Legends from a Word (.docx) or PDF manuscript."""
    from pre_peer_checker.parsers.manuscript_text import manuscript_paragraphs

    return extract_structured_legends_from_paragraphs(manuscript_paragraphs(path))


def extract_structured_legends_from_paragraphs(paragraphs: list[str]) -> list[StructuredLegend]:
    paragraphs = [p.strip() for p in paragraphs if p and p.strip()]
    legends: list[StructuredLegend] = []
    i = 0
    while i < len(paragraphs):
        m = match_legend_head(paragraphs[i])
        if not m:
            i += 1
            continue
        figure = legend_figure_name(m.group(1), m.group(2))
        parts = [paragraphs[i]]
        j = i + 1
        while j < len(paragraphs):
            para = paragraphs[j]
            head = _FIG_START_RE.match(para)
            if head:
                cont = _CONTINUED_RE.match(para[head.end() :])
                if not cont or legend_figure_name(head.group(1), head.group(2)) != figure:
                    break
                para = para[head.end() + cont.end() :].strip()
            if para:
                parts.append(para)
            j += 1
            if len(parts) > 12:
                break
        text = clean_legend_text(" ".join(parts))
        panel_ns = parse_panel_ns(figure, text)
        tests = [t.group(0) for t in _TEST_RE.finditer(text)]
        pvals = []
        for pm in _P_RE.finditer(text):
            try:
                pvals.append(float(pm.group(1)))
            except ValueError:
                pass
        legends.append(
            StructuredLegend(
                figure=figure,
                text=text,
                panel_ns=panel_ns,
                tests=tests,
                p_values=pvals,
            )
        )
        i = j
    return legends


def all_panel_ns(legends: list[StructuredLegend]) -> list[PanelN]:
    out: list[PanelN] = []
    for leg in legends:
        out.extend(leg.panel_ns)
    return out
