"""LLM assigns the sample sizes the rules left unread to panels and groups.

A generic regex finds every stated n in the legend and tags it in place; the LLM only
decides which panels / group each tag counts (or that it is not a sample size). Values
come from the text, so the model cannot invent an n; it is asked only for the part the
rules get wrong on unseen legend formats — which panels a statement covers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON
from pre_peer_checker.parsers.legend_struct import clean_legend_text

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUMTOK = r"\d+(?:\s*(?:[–—-]|to)\s*\d+)?"
# ``n = 15 (day 3), 93 (day 6), and 48`` — every number listed after one ``n =``.
_N_EQ_RE = re.compile(
    r"(?<![A-Za-z])[nN]\s*(?:\([^()=]{1,40}\)\s*)?(?:=|≥|>|≤|<)\s*(" + _NUMTOK + r")"
    r"((?:\s*(?:\([^()]{1,60}\))?\s*(?:,|/|and|or|;|versus|vs\.?)\s*(?:and\s+)?(?:[nN]\s*=\s*)?"
    + _NUMTOK + r"(?![\d.]))*)"
)
_TOK_RE = re.compile(_NUMTOK)
_PAREN_RE = re.compile(r"\([^()]*\)")
_WORDED_RE = re.compile(
    r"\b(\d+|" + "|".join(_WORDS) + r")\s+"
    r"(?:independent\s+|biological(?:ly\s+independent)?\s+|separate\s+|individual\s+)?"
    r"(?:experiments?|replicates?|repeats?|cultures?|mice|rats|animals|patients|donors|embryos|"
    r"larvae|individuals|fish|flies|subjects|participants|cells|neurons|slices|samples)\b",
    re.I,
)


# A single letter used as a panel reference: "(B", "B,", "B)", "B:", "in B.", "B and C", "B–D",
# or opening a sentence ("... . d IHC images").
_PANEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9'’])([A-Za-z])\d?"
    r"(?=\s*[,)\].:;–—-]|\s+(?:and|to)\s+[A-Za-z]\d?(?![A-Za-z]))"
    r"|\(\s*([A-Za-z])\d?(?![A-Za-z])"
    r"|(?:^|(?<=[.;:]\s))([A-Za-z])\d?(?=\s+\S)"
    r"|(?<=[A-Za-z],)([A-Za-z])\d?(?=\s)"
)
_PANEL_RANGE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\d?\s*[–—-]\s*([A-Za-z])\d?(?![A-Za-z])")


def legend_panel_letters(text: str) -> set[str]:
    """Upper-cased letters the legend uses as panel references (ranges expanded)."""
    found = {next(g for g in m.groups() if g).upper() for m in _PANEL_TOKEN_RE.finditer(text)}
    for m in _PANEL_RANGE_RE.finditer(text):
        lo, hi = m.group(1).upper(), m.group(2).upper()
        if lo < hi:
            found.update(chr(c) for c in range(ord(lo), ord(hi) + 1))
    return found


@dataclass(frozen=True)
class NMention:
    start: int
    end: int
    n: int
    n_max: int | None = None


def _value(tok: str) -> tuple[int, int | None]:
    if tok.lower() in _WORDS:
        return _WORDS[tok.lower()], None
    parts = re.split(r"\s*(?:[–—-]|to)\s*", tok)
    lo = int(parts[0])
    hi = int(parts[1]) if len(parts) > 1 and int(parts[1]) > lo else None
    return lo, hi


def stated_n_mentions(text: str) -> list[NMention]:
    """Every stated sample size; worded counts only in legends without any ``n =``."""
    out: list[NMention] = []
    for m in _N_EQ_RE.finditer(text):
        base = m.start(1)
        seg = _PAREN_RE.sub(lambda p: " " * len(p.group()), text[base : m.end()])
        for t in _TOK_RE.finditer(seg):
            out.append(NMention(base + t.start(), base + t.end(), *_value(t.group())))
    if out:
        return out
    for m in _WORDED_RE.finditer(text):
        out.append(NMention(m.start(1), m.end(1), *_value(m.group(1))))
    return out


def _tagged(text: str, mentions: list[NMention]) -> str:
    parts, last = [], 0
    for i, m in enumerate(mentions, 1):
        parts += [text[last : m.end], f" [#{i}]"]
        last = m.end
    return "".join(parts) + text[last:]


ASSIGN_PROMPT = """Figure legend from a biomedical paper. Every stated sample size is tagged [#k] right after the number.

For each tag, decide:
- "panels": the panel letters whose plotted data this sample size counts.
  * A legend part opened by panel letters ("(C-E) ...", "C-E, ...", "c,d ...") covers all those panels; a sample size inside it applies to each of them.
  * Several numbers listed after one "n =" ("n = 15 (day 3), 93 (day 6)") all apply to the same panels.
  * A sample size stated for the whole figure ("For all panels ...", or one closing sentence after all panel descriptions) covers every panel that shows quantified data, but not panels that are only representative images, schematics or diagrams.
- "group": the group/condition/genotype this number counts, copied from the legend. The label may come before the number ("WT, n = 8", "WT (n = 8)") or after it in parentheses ("n = 15 (day 3)"). Use "" when the number counts the whole panel rather than one group.
- "skip": true when the tagged number is not a sample size of plotted data (a total that is the sum of other tagged numbers, a dose, a time, a count of representative images, a male/female breakdown); otherwise false.

Tags: {tags}

Answer with JSON only, exactly one entry per tag above, in tag order, repeating its number as "n":
{{"tags": [{{"id": 1, "n": 8, "panels": ["C"], "group": "WT", "skip": false}}, ...]}}

Legend ({figure}):
{legend}
"""


# Same task, worded clause-first: a second reading for self-consistency voting.
ASSIGN_PROMPT_ALT = """Below is a figure legend. Each sample size is marked [#k] right after the number.

Work tag by tag. First find the legend part the tag sits in and the panel letters that open that part (a part may open with several letters such as "c,d" or "(C-E)"; then it covers each of them). If the sample size is written once for the whole figure, it covers every panel with quantified data but no representative images or schematics. Several numbers after one "n =" share the same panels.
Then give the group the number counts (genotype / condition / time point as written in the legend; "" if it counts the whole panel).
Mark "skip": true for numbers that are not sample sizes of plotted data (sums of other tagged numbers, doses, times, male/female breakdowns, counts of representative images).

Tags: {tags}

Reply with JSON only, one entry per tag in order, repeating its number as "n":
{{"tags": [{{"id": 1, "n": 8, "panels": ["C"], "group": "WT", "skip": false}}, ...]}}

Legend ({figure}):
{legend}
"""

VERIFY_PROMPT = """Figure legend from a biomedical paper; each stated sample size is tagged [#k] after the number.
A first reader proposed which panels each tag counts. Check every proposal against the legend text.

Proposals:
{proposals}

For each proposal answer "ok": true if the legend states that sample size for exactly those panels (and group), otherwise "ok": false and give the correct "panels" (use [] if the number is not a sample size of plotted data).

Answer with JSON only, one entry per proposal in the same order:
{{"tags": [{{"id": 1, "n": 8, "ok": true, "panels": ["C"]}}, ...]}}

Legend ({figure}):
{legend}
"""

ASSIGN_STRATEGIES = ("single", "verify", "vote")
ASSIGN_STRATEGY_ENV = "PRE_PEER_CHECKER_ASSIGN_STRATEGY"


def _tag_list(mentions: list[NMention]) -> str:
    return ", ".join(
        f"#{i} = {m.n}" + (f"-{m.n_max}" if m.n_max else "") for i, m in enumerate(mentions, 1)
    )


def build_assign_prompt(
    figure: str, text: str, mentions: list[NMention], *, template: str = ASSIGN_PROMPT
) -> str:
    return template.format(figure=figure, legend=_tagged(text, mentions), tags=_tag_list(mentions))


def build_verify_prompt(
    figure: str, text: str, mentions: list[NMention], proposals: list[tuple[NMention, list[str], str]]
) -> str:
    lines = []
    for i, (m, panels, group) in enumerate(proposals, 1):
        tag = mentions.index(m) + 1
        g = f", group {group!r}" if group else ""
        lines.append(f"{i}. tag #{tag} (n = {m.n}) -> panels {', '.join(panels)}{g}")
    return VERIFY_PROMPT.format(
        figure=figure, legend=_tagged(text, mentions), proposals="\n".join(lines)
    )


def apply_verdicts(
    raw: str, proposals: list[tuple[NMention, list[str], str]]
) -> list[tuple[NMention, list[str], str]]:
    """Keep confirmed proposals; a rejected one takes the checker's panels (none drops it).

    A proposal the checker did not answer is kept: silence is not a rejection.
    """
    verdicts: dict[int, dict] = {}
    for entry in _entries(raw):
        try:
            idx = int(entry.get("id")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(proposals) and idx not in verdicts:
            verdicts[idx] = entry
    out = []
    for i, (m, panels, group) in enumerate(proposals):
        v = verdicts.get(i)
        if v is None or v.get("ok") is True or str(v.get("ok")).lower() == "true":
            out.append((m, panels, group))
            continue
        fixed = v.get("panels") or []
        if isinstance(fixed, str):
            fixed = re.findall(r"[A-Za-z]\d?", fixed)
        letters = list(dict.fromkeys(str(p).strip().upper() for p in fixed))
        letters = [p for p in letters if re.fullmatch(r"[A-Z]\d?", p)]
        if letters:
            out.append((m, letters, group))
    return out


def vote(
    a: list[tuple[NMention, list[str], str]], b: list[tuple[NMention, list[str], str]]
) -> list[tuple[NMention, list[str], str]]:
    """Panels both readings gave the same tag; the rest goes back to the rules."""
    other = {m: (set(panels), group) for m, panels, group in b}
    out = []
    for m, panels, group in a:
        if m not in other:
            continue
        shared = [p for p in panels if p in other[m][0]]
        if shared:
            out.append((m, shared, group if group == other[m][1] else ""))
    return out


def assign_strategy() -> str:
    import os

    s = os.environ.get(ASSIGN_STRATEGY_ENV, "").strip().lower()
    return s if s in ASSIGN_STRATEGIES else "single"


def assign_mentions(
    figure: str,
    text: str,
    mentions: list[NMention],
    generate: Callable[[str], str],
    *,
    strategy: str = "single",
) -> list[tuple[NMention, list[str], str]]:
    """LLM panel/group assignment for tagged mentions, by ``strategy``.

    ``single``: one reading. ``verify``: the reading is checked by a second pass that only
    confirms or corrects proposals. ``vote``: two differently worded readings, kept where
    they agree.
    """
    first = parse_assign_response(generate(build_assign_prompt(figure, text, mentions)), mentions)
    if strategy == "verify" and first:
        return apply_verdicts(generate(build_verify_prompt(figure, text, mentions, first)), first)
    if strategy == "vote":
        alt = build_assign_prompt(figure, text, mentions, template=ASSIGN_PROMPT_ALT)
        return vote(first, parse_assign_response(generate(alt), mentions))
    return first


def _entries(raw: str) -> list[dict]:
    s, e = raw.find("{"), raw.rfind("}")
    if s >= 0 and e > s:
        try:
            d = json.loads(raw[s : e + 1])
            if isinstance(d, dict) and isinstance(d.get("tags"), list):
                return [t for t in d["tags"] if isinstance(t, dict)]
        except json.JSONDecodeError:
            pass
    out = []
    for frag in re.findall(r"\{[^{}]*\"id\"[^{}]*\}", raw or ""):
        try:
            out.append(json.loads(frag))
        except json.JSONDecodeError:
            continue
    return out


def _align(entry: dict, mentions: list[NMention], used: set[int]) -> int | None:
    """Tag index of an answer: its id when the echoed n agrees, else the first unused tag with that n."""
    try:
        idx = int(entry.get("id")) - 1
    except (TypeError, ValueError):
        idx = -1
    try:
        n = int(str(entry["n"]).split("-")[0]) if entry.get("n") is not None else None
    except ValueError:
        n = None
    if 0 <= idx < len(mentions) and idx not in used and (n is None or mentions[idx].n == n):
        return idx
    if n is not None:
        for j, m in enumerate(mentions):
            if j not in used and m.n == n:
                return j
    return None


def parse_assign_response(raw: str, mentions: list[NMention]) -> list[tuple[NMention, list[str], str]]:
    """(mention, panels, group) for every tag the model assigned and did not skip."""
    out = []
    used: set[int] = set()
    for entry in _entries(raw):
        idx = _align(entry, mentions, used)
        if idx is None:
            continue
        used.add(idx)
        if entry.get("skip"):
            continue
        panels = entry.get("panels") or []
        if isinstance(panels, str):
            panels = re.findall(r"[A-Za-z]\d?", panels)
        letters = [str(p).strip().upper() for p in panels]
        letters = list(dict.fromkeys(p for p in letters if re.fullmatch(r"[A-Z]\d?", p)))
        if letters:
            out.append((mentions[idx], letters, str(entry.get("group") or "").strip()))
    return out


def unread_mentions(text: str, rules: LegendFigureJSON) -> list[NMention]:
    """Stated n values the rules attached to no panel."""
    from pre_peer_checker.parsers.legend_struct import OPEN_N_MAX

    read = {(p.n, p.n_max) for p in rules.panels if p.n is not None}
    # the rules read ``n ≥ 2`` as an open range; the mention regex sees just its 2
    read |= {(p.n, None) for p in rules.panels if p.n is not None and p.n_max == OPEN_N_MAX}
    return [m for m in stated_n_mentions(text) if (m.n, m.n_max) not in read]


def assign_unread_ns(
    figure: str,
    legend: str,
    rules: LegendFigureJSON,
    generate: Callable[[str], str],
    *,
    strategy: str | None = None,
) -> LegendFigureJSON:
    """``rules`` plus LLM-assigned rows for the stated n values the rules left unread.

    ``generate`` takes the prompt and returns free text (JSON is parsed leniently).
    ``strategy`` defaults to ``PRE_PEER_CHECKER_ASSIGN_STRATEGY`` (``single``).
    """
    text = clean_legend_text(legend or "")
    if not unread_mentions(text, rules):
        return rules
    mentions = stated_n_mentions(text)
    try:
        assigned = assign_mentions(
            figure, text, mentions, generate, strategy=strategy or assign_strategy()
        )
    except Exception:
        return rules
    unread = set(unread_mentions(text, rules))
    letters = legend_panel_letters(text)
    added = [
        LegendPanelJSON(
            panel=p,
            n=m.n,
            n_max=m.n_max,
            groups=[group] if group else [],
            evidence_span=text[max(0, m.start - 80) : m.end + 40],
            confidence=0.7,
            n_scope="llm_assign",
        )
        for m, panels, group in assigned
        if m in unread
        for p in panels
        if p in letters
    ]
    if not added:
        return rules
    rules.panels.extend(added)
    rules.extractor = "rules+llm-assign"
    return rules
