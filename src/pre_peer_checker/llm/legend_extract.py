"""Figure check-item JSON: 読む＝LLM（本線）、規則はフォールバック／安全網。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from pre_peer_checker.llm.legend_schema import (
    LegendFigureJSON,
    LegendPanelJSON,
    build_figure_chunk_llm_prompt,
    build_legend_llm_prompt,
    detect_citation,
    detect_error_bar_type,
    detect_exclusion_criteria,
    detect_independence_claims,
    parse_legend_llm_response,
)
from pre_peer_checker.parsers.figure_chunks import (
    FigureChunk,
    build_figure_chunks_from_docx,
    chunks_to_artifact,
    figure_num_key,
)
from pre_peer_checker.parsers.legend_struct import (
    PanelN,
    StructuredLegend,
    extract_structured_legends,
)


def structured_to_legend_json(leg: StructuredLegend) -> LegendFigureJSON:
    panels = [
        LegendPanelJSON(
            panel=pn.panel,
            n=pn.n,
            notes=pn.context[:120],
            n_scope="unknown",
            evidence_span=pn.context[:160],
            confidence=0.95,
            n_max=pn.n_max,
        )
        for pn in leg.panel_ns
    ]
    return LegendFigureJSON(
        figure=leg.figure,
        panels=panels,
        tests=list(leg.tests),
        p_values=list(leg.p_values),
        citation=detect_citation(leg.text),
        error_bar_type=detect_error_bar_type(leg.text),
        independence_claims=detect_independence_claims(leg.text),
        exclusion_criteria=detect_exclusion_criteria(leg.text),
        raw_excerpt=leg.text[:500],
        extractor="rules",
    )


LEGEND_LLM_MODES = ("off", "auto", "on")


def normalize_legend_llm_mode(value: Any) -> str:
    """``off`` / ``auto`` / ``on`` from a mode string or the legacy bool flag.

    auto: rules first; the LLM only assigns to panels the sample sizes a legend states
    that the rules did not pick up (the model is not loaded when every figure was read).
    """
    if value is True:
        return "on"
    if value is None or value is False:
        return "off"
    v = str(value).strip().lower()
    if v in {"on", "always", "all", "true", "1", "yes"}:
        return "on"
    if v in {"auto", "unread"}:
        return "auto"
    return "off"


_WORD_NUMS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUM = r"(\d+|" + "|".join(_WORD_NUMS) + r")"
# ``n = 19 (a), 16 (d), and 13 (g)`` — the list after one ``n =`` states several n.
_N_EQ_RE = re.compile(
    r"\b[nN]\s*(?:=|≥|>|≤|<)\s*(\d+)"
    r"((?:\s*(?:\([^()]{1,40}\))?\s*(?:,|and|or)\s*(?:and\s+)?\d+)*)"
)
_PAREN_RE = re.compile(r"\([^()]*\)")
_N_PHRASE_RE = re.compile(
    _NUM + r"\s+(?:independent|biological(?:ly\s+independent)?|technical|separate)\s+"
    r"(?:experiments?|replicates?|repeats?|cultures?|samples?|animals|mice)\b"
    r"|\b(\d+)\s+(?:mice|rats|animals|patients|donors|embryos|larvae|individuals)\b",
    re.IGNORECASE,
)


def legend_sample_sizes(text: str) -> set[int]:
    """Sample sizes a legend states (``n = …``, ``three independent experiments``, ``12 mice``).

    Replicate / animal phrases count only in legends without any ``n =``: next to an
    explicit n they describe where the n came from (``N = 14 images from 3 independent
    experiments``), not another panel n.
    """
    from pre_peer_checker.parsers.legend_struct import clean_legend_text

    text = clean_legend_text(text)
    out: set[int] = set()
    for m in _N_EQ_RE.finditer(text):
        out.add(int(m.group(1)))
        tail = _PAREN_RE.sub(" ", m.group(2) or "")
        out.update(int(x) for x in re.findall(r"(?<![\w.])\d+(?![\w.])", tail))
    if out:
        return out
    for m in _N_PHRASE_RE.finditer(text):
        tok = (m.group(1) or m.group(2) or "").lower()
        out.add(int(tok) if tok.isdigit() else _WORD_NUMS[tok])
    return out


def legend_needs_llm(chunk: FigureChunk, rules: LegendFigureJSON) -> bool:
    """True when the legend states a sample size the rules did not extract."""
    stated = legend_sample_sizes(chunk.legend or "")
    if not stated:
        return False
    read = {int(p.n) for p in rules.panels if p.n is not None}
    return not stated <= read


# Merge guards that post-filter / supplement LLM rows. The product always runs with
# ALL_GUARDS; subsets exist only for generalization ablation (eval/generalization.py).
GUARD_GROUNDING = "grounding"
GUARD_SECTION_MISATTRIB = "section_misattrib"
GUARD_DOSE_ON_WRONG = "dose_on_wrong"
GUARD_STOLEN_FROM_RULES = "stolen_from_rules"
GUARD_GROUP_CONFLICT = "group_conflict"
GUARD_RULE_FILL = "rule_fill"
ALL_GUARDS: frozenset[str] = frozenset(
    {
        GUARD_GROUNDING,
        GUARD_SECTION_MISATTRIB,
        GUARD_DOSE_ON_WRONG,
        GUARD_STOLEN_FROM_RULES,
        GUARD_GROUP_CONFLICT,
        GUARD_RULE_FILL,
    }
)


def _rules_from_chunk(chunk: FigureChunk) -> LegendFigureJSON:
    """Rule extract primarily from legend text; citation scan includes all sections."""
    from pre_peer_checker.parsers.legend_struct import parse_panel_ns, _TEST_RE, _P_RE

    blob = "\n".join(
        [chunk.legend, *chunk.results, *chunk.methods]
    )
    legend_text = chunk.legend or blob
    figure = chunk.figure_id or "Figure"
    panel_ns = parse_panel_ns(figure, legend_text)
    # If legend had no panel n, try full blob for bare panel forms
    if not panel_ns and blob != legend_text:
        panel_ns = parse_panel_ns(figure, blob)
    tests = [t.group(0) for t in _TEST_RE.finditer(blob)]
    pvals: list[float] = []
    for pm in _P_RE.finditer(blob):
        try:
            pvals.append(float(pm.group(1)))
        except ValueError:
            pass
    return LegendFigureJSON(
        figure=figure,
        panels=[
            LegendPanelJSON(
                panel=p.panel,
                n=p.n,
                groups=[p.group] if getattr(p, "group", "") else [],
                notes=p.context[:120],
                evidence_span=p.context[:160],
                confidence=0.95,
                n_max=p.n_max,
            )
            for p in panel_ns
        ],
        tests=tests,
        p_values=pvals,
        citation=detect_citation(blob),
        error_bar_type=detect_error_bar_type(blob),
        independence_claims=detect_independence_claims(blob),
        exclusion_criteria=detect_exclusion_criteria(blob),
        raw_excerpt=(chunk.legend or blob)[:500],
        extractor="rules",
    )


def _explicit_uppercase_panels_in_span(span: str) -> set[str]:
    """Panels referenced as n=11 (H) / 20 (I) style (uppercase only)."""
    import re

    found: set[str] = set()
    for m in re.finditer(r"(?:n\s*=\s*)?(\d+)\s*\(\s*([A-Z]\d?)\s*\)", span or ""):
        found.add(m.group(2).upper())
    return found


def _is_misattributed_section_panel(p: LegendPanelJSON) -> bool:
    """True when LLM put n on section letter K but evidence is n=11 (H), 20 (I)…"""
    span = p.evidence_span or p.notes or ""
    explicit = _explicit_uppercase_panels_in_span(span)
    if len(explicit) < 1:
        return False
    return p.panel.upper() not in explicit


_DOSE_GROUP_RE = re.compile(r"^\d+x$", re.I)


def _drop_dose_on_wrong_panels(panels: list[LegendPanelJSON]) -> list[LegendPanelJSON]:
    """If 1x/4x rows exist on a late panel (E+), drop the same n on A/B/C."""
    dose_owners: set[tuple[int, str]] = set()
    for p in panels:
        if p.n is None or not p.groups:
            continue
        g = str(p.groups[0])
        if _DOSE_GROUP_RE.match(g) and p.panel.upper() >= "E":
            dose_owners.add((int(p.n), g.lower()))
    if not dose_owners:
        return panels
    out: list[LegendPanelJSON] = []
    for p in panels:
        g = str(p.groups[0]).lower() if p.groups else ""
        if (
            p.n is not None
            and g
            and (int(p.n), g) in dose_owners
            and p.panel.upper() in {"A", "B", "C", "D"}
        ):
            continue
        out.append(p)
    return out


def _drop_llm_n_stolen_from_rules(
    llm_panels: list[LegendPanelJSON],
    rule_pn: set[tuple[str, int]],
) -> list[LegendPanelJSON]:
    """Drop LLM rows that reassign an n already owned by rules on another panel.

    Typo / section-letter cases: rules L=140, M=88 (empty group); LLM invents
    K=140 or P=140/(k) — those steal the n and must not survive the merge.
    Day/dose lists owned by rules (B,C day 3 = 15) are protected the same way.
    Shared n on multiple rule panels (D,E,G,H=10) still allows those owners.
    """
    if not rule_pn:
        return llm_panels
    n_to_owners: dict[int, set[str]] = {}
    for panel, n in rule_pn:
        n_to_owners.setdefault(n, set()).add(panel)
    out: list[LegendPanelJSON] = []
    for p in llm_panels:
        if p.n is None:
            out.append(p)
            continue
        owners = n_to_owners.get(int(p.n))
        if owners and p.panel.upper() not in owners:
            continue
        out.append(p)
    return out


def _drop_llm_group_conflicts(
    llm_panels: list[LegendPanelJSON],
    rule_panels: list[LegendPanelJSON],
) -> list[LegendPanelJSON]:
    """Rules own grouped (panel, n): G=3 (RNA-seq) blocks LLM G=3 (<paraphrase>)."""
    rule_groups: dict[tuple[str, int], set[str]] = {}
    for rp in rule_panels:
        if rp.n is None or not rp.groups:
            continue
        key = (rp.panel.upper(), int(rp.n))
        rule_groups.setdefault(key, set()).add(str(rp.groups[0]).strip().lower())
    if not rule_groups:
        return llm_panels
    out: list[LegendPanelJSON] = []
    for p in llm_panels:
        if p.n is not None and p.groups:
            owned = rule_groups.get((p.panel.upper(), int(p.n)))
            if owned and str(p.groups[0]).strip().lower() not in owned:
                continue
        out.append(p)
    return out


def _norm_for_grounding(text: str) -> str:
    t = (text or "").replace("\u00ad", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    t = t.replace("–", "-").replace("—", "-").replace("−", "-")
    t = re.sub(r"-\s*\n\s*", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _llm_row_is_grounded(p: LegendPanelJSON, source_norm: str) -> bool:
    """LLM evidence must quote the source and contain the n it claims."""
    if p.n is None:
        return True
    ev = _norm_for_grounding(p.evidence_span or p.notes or "")
    n_tok = rf"(?<!\d)(?<!\d\.){int(p.n)}(?!\d)(?!\.\d)"
    if not ev:
        return bool(re.search(n_tok, source_norm))
    if not re.search(n_tok, ev):
        return False
    frags = [f.strip(" ,;:.()") for f in re.split(r"\.\.\.|…", ev)]
    frags = [f for f in frags if len(f) >= 4]
    return all(f in source_norm for f in frags) if frags else True


def _is_replicate_statement(p: LegendPanelJSON) -> bool:
    """`3 independent experiments showed similar patterns` is not a sample size."""
    if p.n is None:
        return False
    ev = p.evidence_span or p.notes or ""
    n = int(p.n)
    if re.search(rf"\b[Nn]\s*=\s*{n}\b", ev):
        return False
    return bool(re.search(rf"\b{n}\s+(?:\w+\s+)?independent\s+experiments?\b", ev, re.I))


def _drop_ungrounded_llm_rows(
    llm_panels: list[LegendPanelJSON], source_text: str | None
) -> list[LegendPanelJSON]:
    if not source_text:
        return llm_panels
    src = _norm_for_grounding(source_text)
    return [
        p
        for p in llm_panels
        if _llm_row_is_grounded(p, src) and not _is_replicate_statement(p)
    ]


def _paneln_to_legend_json(pn: PanelN) -> LegendPanelJSON:
    return LegendPanelJSON(
        panel=pn.panel.upper(),
        n=pn.n,
        groups=[pn.group] if getattr(pn, "group", "") else [],
        notes=(pn.context or "")[:120],
        n_scope="per_group" if getattr(pn, "group", "") else "unknown",
        evidence_span=(pn.context or "")[:160],
        confidence=0.95,
        n_max=pn.n_max,
    )


def _rule_json_is_explicit(p: LegendPanelJSON) -> bool:
    """Rule/LLM hit tied to uppercase panel letter in n=…(X)."""
    import re

    ctx = p.evidence_span or p.notes or ""
    return bool(
        re.search(rf"(?:n\s*=\s*)?\d+\s*\(\s*{re.escape(p.panel)}\s*\)", ctx)
    )


def _merge_llm_over_rules(
    base: LegendFigureJSON,
    parsed: LegendFigureJSON,
    *,
    llm_primary: bool = True,
    source_text: str | None = None,
    guards: frozenset[str] = ALL_GUARDS,
) -> LegendFigureJSON:
    """Merge LLM + rules. Rules own parenthesis class (panel-letter → empty group).

    - Drop LLM rows whose evidence is not in ``source_text`` or is a replicate count.
    - Drop K←(H,I,J) section misattribution and dose-on-A mistakes.
    - If rules assign panel+n with empty group, strip LLM-invented groups on that key.
    - Always keep rule rows the LLM omitted (shared n, last list element, published styles).

    ``guards`` selects which of these steps run (see ``ALL_GUARDS``); the
    group-strip step belongs to ``group_conflict``.
    """
    unknown = set(guards) - ALL_GUARDS
    if unknown:
        raise ValueError(f"unknown guards: {sorted(unknown)}")
    dose_guard = GUARD_DOSE_ON_WRONG in guards
    rule_fill = GUARD_RULE_FILL in guards
    if not parsed.figure:
        parsed.figure = base.figure
    if base.citation.mentioned and not parsed.citation.mentioned:
        parsed.citation = base.citation
    if not parsed.tests and base.tests:
        parsed.tests = base.tests
    if not parsed.p_values and base.p_values:
        parsed.p_values = base.p_values
    if base.error_bar_type and not parsed.error_bar_type:
        parsed.error_bar_type = base.error_bar_type
    if base.independence_claims.mentioned and not parsed.independence_claims.mentioned:
        parsed.independence_claims = base.independence_claims
    if base.exclusion_criteria.mentioned and not parsed.exclusion_criteria.mentioned:
        parsed.exclusion_criteria = base.exclusion_criteria

    rule_empty_pn = {
        (rp.panel.upper(), int(rp.n))
        for rp in base.panels
        if rp.n is not None and not (rp.groups)
    }
    rule_pn = {(rp.panel.upper(), int(rp.n)) for rp in base.panels if rp.n is not None}

    if parsed.panels and GUARD_GROUNDING in guards:
        parsed.panels = _drop_ungrounded_llm_rows(parsed.panels, source_text)

    if llm_primary and parsed.panels:
        kept = list(parsed.panels)
        if GUARD_SECTION_MISATTRIB in guards:
            kept = [p for p in kept if not _is_misattributed_section_panel(p)]
        if dose_guard:
            kept = _drop_dose_on_wrong_panels(kept)
        if GUARD_STOLEN_FROM_RULES in guards:
            kept = _drop_llm_n_stolen_from_rules(kept, rule_pn)
        if GUARD_GROUP_CONFLICT in guards:
            kept = _drop_llm_group_conflicts(kept, base.panels)
        by_key: dict[tuple[str, str, int | None], LegendPanelJSON] = {}
        overlaid = False
        for p in kept:
            g = str(p.groups[0]) if p.groups else ""
            if (
                GUARD_GROUP_CONFLICT in guards
                and p.n is not None
                and (p.panel.upper(), int(p.n)) in rule_empty_pn
                and g
            ):
                # Rules: parentheses are panel letters → group must stay empty.
                p = LegendPanelJSON(
                    panel=p.panel,
                    n=p.n,
                    groups=[],
                    notes=p.notes,
                    n_scope=p.n_scope,
                    evidence_span=p.evidence_span,
                    confidence=p.confidence,
                    n_max=p.n_max,
                )
                g = ""
                overlaid = True
            by_key[(p.panel.upper(), g, p.n)] = p
        llm_covered = {k[0] for k in by_key}
        llm_empty_pn = {
            (k[0], k[2]) for k in by_key if not k[1] and k[2] is not None
        }
        for rp in base.panels if rule_fill else []:
            g = str(rp.groups[0]) if rp.groups else ""
            key = (rp.panel.upper(), g, rp.n)
            if key in by_key:
                if rp.n_max is not None and by_key[key].n_max is None:
                    by_key[key] = rp  # ``n = 7–9`` read by rules, only the 7 by the LLM
                continue
            panel_u = rp.panel.upper()
            # Keep every distinct rule (panel, group, n). Empty-group panel+n from
            # rules must survive even when LLM already has another n on that panel.
            if (
                _rule_json_is_explicit(rp)
                or panel_u not in llm_covered
                or (rp.n is not None and (panel_u, int(rp.n)) in rule_empty_pn and not g)
                or (rp.groups and key not in by_key)
                or (
                    rp.n is not None
                    and not g
                    and (panel_u, int(rp.n)) not in llm_empty_pn
                )
            ):
                by_key[key] = rp
                overlaid = True
        merged = list(by_key.values())
        parsed.panels = _drop_dose_on_wrong_panels(merged) if dose_guard else merged
        parsed.extractor = "llm+rules" if overlaid else "llm"
        return parsed

    if not rule_fill:
        parsed.extractor = "llm"
        return parsed

    if not parsed.panels and base.panels:
        parsed.panels = base.panels
    else:
        by_panel = {p.panel.upper(): p for p in base.panels}
        for p in parsed.panels:
            key = p.panel.upper()
            if key in by_panel:
                rp = by_panel[key]
                if p.n is None and rp.n is not None:
                    p.n = rp.n
                if not p.groups and rp.groups:
                    p.groups = list(rp.groups)
                if not p.evidence_span and rp.evidence_span:
                    p.evidence_span = rp.evidence_span
                if (
                    p.n is not None
                    and (p.panel.upper(), int(p.n)) in rule_empty_pn
                    and p.groups
                ):
                    p.groups = []
        seen = {
            (p.panel.upper(), str(p.groups[0]) if p.groups else "", p.n)
            for p in parsed.panels
        }
        for rp in base.panels:
            g = str(rp.groups[0]) if rp.groups else ""
            if (rp.panel.upper(), g, rp.n) not in seen:
                parsed.panels.append(rp)
    parsed.extractor = "hybrid-llm"
    return parsed


def extract_legend_json_hybrid(
    legend_text: str,
    *,
    figure_hint: str | None = None,
    llm_generate: Callable[[str], str] | None = None,
    prefer_llm: bool = False,
    only_if_unread: bool = False,
    assign_generate: Callable[[str], str] | None = None,
) -> LegendFigureJSON:
    """Rules first; optional LLM fill/override when prefer_llm and callable given.

    Legacy entry: legend text only (no Results/Methods chunk).
    """
    chunk = FigureChunk(
        figure_id=figure_hint or "Figure",
        figure_num=figure_num_key(figure_hint or "Figure"),
        legend=legend_text,
    )
    return extract_check_items_from_chunk(
        chunk,
        llm_generate=llm_generate,
        prefer_llm=prefer_llm,
        legend_only_prompt=True,
        only_if_unread=only_if_unread,
        assign_generate=assign_generate,
    )


def extract_check_items_from_chunk(
    chunk: FigureChunk,
    *,
    llm_generate: Callable[[str], str] | None = None,
    prefer_llm: bool = False,
    legend_only_prompt: bool = False,
    llm_primary: bool = True,
    guards: frozenset[str] = ALL_GUARDS,
    prompt_variant: str = "full",
    only_if_unread: bool = False,
    assign_generate: Callable[[str], str] | None = None,
) -> LegendFigureJSON:
    """Rules fallback; when prefer_llm, LLM panels take priority by default.

    Without the ``rule_fill`` guard an LLM failure yields no panels instead of
    the rules result, so ablations measure the LLM path alone.
    only_if_unread: skip the LLM when the rules already read every stated n.
    assign_generate: free-text generator; with only_if_unread the LLM then only assigns
    the stated n the rules left unread to panels (``legend_assign``) instead of
    re-reading the whole legend.
    """
    base = _rules_from_chunk(chunk)
    if only_if_unread and assign_generate is not None:
        from pre_peer_checker.llm.legend_assign import assign_unread_ns

        return assign_unread_ns(chunk.figure_id or base.figure, chunk.legend or "", base, assign_generate)
    if not prefer_llm or llm_generate is None:
        return base
    if only_if_unread and not legend_needs_llm(chunk, base):
        return base
    if legend_only_prompt:
        prompt = build_legend_llm_prompt(
            chunk.legend or "", figure_hint=chunk.figure_id, variant=prompt_variant
        )
    else:
        prompt = build_figure_chunk_llm_prompt(
            chunk.prompt_body(),
            figure_hint=chunk.figure_id,
            variant=prompt_variant,
        )
    fallback = base if GUARD_RULE_FILL in guards else LegendFigureJSON(
        figure=base.figure, extractor="llm-failed"
    )
    try:
        raw = llm_generate(prompt)
        parsed = parse_legend_llm_response(raw)
    except Exception:
        return fallback
    if parsed is None:
        return fallback
    source = "\n".join([chunk.legend or "", *chunk.results, *chunk.methods])
    return _merge_llm_over_rules(
        base, parsed, llm_primary=llm_primary, source_text=source, guards=guards
    )


def extract_legends_json_from_docx(
    path: Path | str,
    *,
    prefer_llm: bool = False,
    llm_generate: Callable[[str], str] | None = None,
    use_figure_chunks: bool = True,
    figure_pdfs: list[Path] | None = None,
    panel_labels_by_figure: dict[str, list[str]] | None = None,
    panel_label_meta: dict | None = None,
    on_item: Callable[[int, int, str], None] | None = None,
    llm_only_unread: bool = False,
    assign_generate: Callable[[str], str] | None = None,
) -> tuple[list[LegendFigureJSON], list[FigureChunk]]:
    """Extract check-item JSON per figure; returns (items, chunks used).

    on_item(done, total, figure_label) is called before each figure and once at the end.
    llm_only_unread: call the LLM only for figures the rules could not fully read.
    assign_generate: see ``extract_check_items_from_chunk``.
    """

    def _notify(done: int, total: int, label: str) -> None:
        if on_item is not None:
            on_item(done, total, label)

    chunks = build_figure_chunks_from_docx(path) if use_figure_chunks else []
    if chunks and panel_labels_by_figure is not None:
        from pre_peer_checker.parsers.figure_panel_labels import attach_panel_labels_to_chunks

        attach_panel_labels_to_chunks(
            chunks, panel_labels_by_figure, meta_by_figure=panel_label_meta
        )
    elif figure_pdfs and chunks:
        from pre_peer_checker.parsers.figure_panel_labels import (
            attach_panel_labels_to_chunks,
            collect_panel_labels_by_figure_detailed,
        )

        by_fig, meta = collect_panel_labels_by_figure_detailed(list(figure_pdfs))
        attach_panel_labels_to_chunks(chunks, by_fig, meta_by_figure=meta)
    if not chunks:
        # Fallback: legend blocks only
        out: list[LegendFigureJSON] = []
        legs = extract_structured_legends(path)
        for i, leg in enumerate(legs):
            _notify(i, len(legs), leg.figure or "")
            out.append(
                extract_legend_json_hybrid(
                    leg.text,
                    figure_hint=leg.figure,
                    llm_generate=llm_generate,
                    prefer_llm=prefer_llm,
                    only_if_unread=llm_only_unread,
                    assign_generate=assign_generate,
                )
            )
        _notify(len(legs), len(legs), "")
        return out, []

    out = []
    for i, ch in enumerate(chunks):
        _notify(i, len(chunks), ch.figure_id or "")
        out.append(
            extract_check_items_from_chunk(
                ch,
                llm_generate=llm_generate,
                prefer_llm=prefer_llm,
                legend_only_prompt=False,
                llm_primary=True,
                only_if_unread=llm_only_unread,
                assign_generate=assign_generate,
            )
        )
    _notify(len(chunks), len(chunks), "")
    return out, chunks


def extract_legends_with_backend(
    path: Path | str,
    *,
    prefer: str = "auto",
    model_id: str | None = None,
    profile_id: str | None = None,
    enabled: bool = False,
    figure_pdfs: list[Path] | None = None,
    panel_labels_by_figure: dict[str, list[str]] | None = None,
    panel_label_meta: dict | None = None,
    on_item: Callable[[int, int, str], None] | None = None,
    backend: Any | None = None,
    mode: str | None = None,
) -> tuple[list[LegendFigureJSON], dict[str, Any]]:
    """Extract legends/check-items; optionally refine with MLX/CUDA when enabled.

    backend: an already-selected backend to reuse (keeps the model loaded across
    manuscripts in one run). None selects one here.
    mode: ``off`` / ``auto`` / ``on`` (overrides ``enabled``); see normalize_legend_llm_mode.
    """
    mode = normalize_legend_llm_mode(mode if mode is not None else enabled)
    enabled = mode != "off"
    meta: dict[str, Any] = {
        "legend_llm_enabled": enabled,
        "legend_llm_mode": mode,
        "backend": None,
        "figure_chunk_mode": True,
        "llm_primary": bool(enabled),
        "llm_profile": profile_id,
    }
    if not enabled:
        items, chunks = extract_legends_json_from_docx(
            path,
            figure_pdfs=figure_pdfs,
            panel_labels_by_figure=panel_labels_by_figure,
            panel_label_meta=panel_label_meta,
            on_item=on_item,
        )
        meta["n_figure_chunks"] = len(chunks)
        meta["figure_chunks"] = chunks_to_artifact(chunks)
        return items, meta

    from pre_peer_checker.llm.backend import probe_backends, select_backend
    from pre_peer_checker.llm.registry import resolve_model

    resolved = resolve_model(
        role="text",
        profile_id=profile_id,
        model_id=model_id,
        prefer=None if (prefer or "auto").lower() == "auto" else prefer,
    )
    meta["resolved_llm"] = resolved.to_dict()
    meta["backends_probed"] = [
        b.__dict__ for b in probe_backends(model_id=model_id, profile_id=profile_id)
    ]
    if backend is None:
        backend = select_backend(prefer, model_id=model_id, profile_id=profile_id)
    if backend is None:
        meta["backend"] = "none"
        meta["note"] = "no MLX/transformers backend; used rules only"
        meta["llm_primary"] = False
        items, chunks = extract_legends_json_from_docx(
            path,
            figure_pdfs=figure_pdfs,
            panel_labels_by_figure=panel_labels_by_figure,
            panel_label_meta=panel_label_meta,
            on_item=on_item,
        )
        meta["n_figure_chunks"] = len(chunks)
        meta["figure_chunks"] = chunks_to_artifact(chunks)
        return items, meta

    info = backend.info()
    meta["backend"] = info.__dict__

    from pre_peer_checker.llm.json_mode import structured_legend_generate

    json_modes: list[str] = []

    def _gen(prompt: str) -> str:
        text, jm = structured_legend_generate(backend, prompt, max_tokens=1024)
        mode = str(jm.get("json_mode") or "free+coerce")
        json_modes.append(mode)
        return text

    def _free(prompt: str) -> str:
        json_modes.append("free")
        return backend.generate(prompt, max_tokens=768)

    items, chunks = extract_legends_json_from_docx(
        path,
        prefer_llm=True,
        llm_generate=_gen,
        figure_pdfs=figure_pdfs,
        panel_labels_by_figure=panel_labels_by_figure,
        panel_label_meta=panel_label_meta,
        on_item=on_item,
        llm_only_unread=mode == "auto",
        assign_generate=_free if mode == "auto" else None,
    )
    meta["n_llm_calls"] = len(json_modes)
    meta["n_figures"] = len(items)
    meta["n_figure_chunks"] = len(chunks)
    meta["figure_chunks"] = chunks_to_artifact(chunks)
    meta["n_llm_panels"] = sum(len(x.panels) for x in items)
    meta["extractors"] = sorted({x.extractor for x in items})
    meta["json_mode"] = json_modes[0] if len(set(json_modes)) == 1 else (
        json_modes[-1] if json_modes else "none"
    )
    meta["json_modes"] = json_modes
    meta["json_mode_outlines"] = any(m.startswith("outlines") for m in json_modes)
    return items, meta


def legend_json_to_panel_ns(
    items: list[LegendFigureJSON],
    *,
    min_confidence: float = 0.5,
) -> list[PanelN]:
    """Convert extracted check-items into PanelN for deterministic n matching."""
    out: list[PanelN] = []
    for fig in items:
        for p in fig.panels:
            if p.n is None:
                continue
            if p.confidence is not None and p.confidence < min_confidence:
                continue
            ctx = p.evidence_span or p.notes or f"n={p.n} ({p.panel})"
            group = ""
            if p.groups:
                group = str(p.groups[0])
            out.append(
                PanelN(
                    panel=p.panel.upper(),
                    n=int(p.n),
                    figure=fig.figure,
                    context=ctx[:200],
                    group=group,
                    n_max=p.n_max,
                )
            )
    return out


def merge_panel_ns(
    rules: list[PanelN],
    llm_or_hybrid: list[PanelN],
    *,
    prefer_llm: bool = False,
) -> list[PanelN]:
    """Merge panel n lists.

    Default / prefer_llm=False: rules win on the same (figure, panel, group);
    LLM only fills keys rules missed.

    prefer_llm=True (legacy): LLM/hybrid list is sole source when non-empty.
    """
    if prefer_llm and llm_or_hybrid:
        return list(llm_or_hybrid)
    merged: dict[tuple[str, str, str], PanelN] = {}
    for pn in llm_or_hybrid:
        key = (figure_num_key(pn.figure), pn.panel.upper(), getattr(pn, "group", "") or "")
        merged[key] = pn
    for pn in rules:
        key = (figure_num_key(pn.figure), pn.panel.upper(), getattr(pn, "group", "") or "")
        merged[key] = pn  # rules lock: never overwrite an explicit rule row
    return list(merged.values())


def summarize_legend_llm_meta(metas: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Aggregate per-docx legend_llm artifacts into a single status for UI/coverage."""
    metas = metas or []
    if not metas:
        return {
            "requested": False,
            "used": False,
            "status": "off",
            "message": "Legend LLM 補助はオフ（規則抽出のみ）",
        }
    requested = any(bool(m.get("legend_llm_enabled")) for m in metas)
    if not requested:
        return {
            "requested": False,
            "used": False,
            "status": "off",
            "message": "Legend LLM 補助はオフ（規則抽出のみ）",
        }
    backends = []
    notes = []
    profiles: list[str] = []
    used = False
    for m in metas:
        resolved = m.get("resolved_llm")
        if isinstance(resolved, dict) and resolved.get("profile_id"):
            profiles.append(str(resolved["profile_id"]))
        elif m.get("llm_profile"):
            profiles.append(str(m["llm_profile"]))
        be = m.get("backend")
        if be == "none" or be is None:
            notes.append(m.get("note") or "バックエンドなし")
        elif isinstance(be, dict):
            backends.append(f"{be.get('name')} ({be.get('model_id')})")
            if be.get("available", True):
                used = True
        else:
            backends.append(str(be))
            used = True
    profile_extra = ""
    uniq_profiles = sorted(set(profiles))
    if uniq_profiles:
        profile_extra = f" · profile={','.join(uniq_profiles)}"
    auto = all(m.get("legend_llm_mode") == "auto" for m in metas if m.get("legend_llm_enabled"))
    n_calls = sum(int(m.get("n_llm_calls") or 0) for m in metas)
    n_figs = sum(int(m.get("n_figures") or 0) for m in metas)
    if used and auto and n_calls == 0:
        return {
            "requested": True,
            "used": False,
            "status": "auto_skipped",
            "mode": "auto",
            "n_llm_calls": 0,
            "n_figures": n_figs,
            "backends": backends,
            "profiles": uniq_profiles,
            "message": f"Legend LLM 自動: 規則で全 {n_figs} Figure の n を読めたため LLM は未使用",
        }
    if used:
        extractors: list[str] = []
        for m in metas:
            extractors.extend(m.get("extractors") or [])
        extra = profile_extra
        if extractors:
            extra += f" · extractors={sorted(set(extractors))}"
        n_panels = sum(int(m.get("n_llm_panels") or 0) for m in metas)
        if n_panels:
            extra += f" · panels={n_panels}"
        modes = []
        for m in metas:
            if m.get("json_mode"):
                modes.append(str(m["json_mode"]))
            modes.extend(str(x) for x in (m.get("json_modes") or []))
        uniq_modes = sorted(set(modes))
        if uniq_modes:
            extra += f" · json_mode={','.join(uniq_modes)}"
        head = (
            f"Legend LLM 自動: 規則で読めなかった {n_calls}/{n_figs} Figure を LLM で読み取り: "
            if auto
            else "Legend LLM を本線使用: "
        )
        return {
            "requested": True,
            "used": True,
            "status": "active",
            "mode": "auto" if auto else "on",
            "n_llm_calls": n_calls,
            "n_figures": n_figs,
            "backends": backends,
            "profiles": uniq_profiles,
            "json_modes": uniq_modes,
            "n_figure_chunks": sum(int(m.get("n_figure_chunks") or 0) for m in metas),
            "message": head + (", ".join(backends) or "backend") + extra,
        }
    return {
        "requested": True,
        "used": False,
        "status": "unavailable",
        "backends": [],
        "profiles": uniq_profiles,
        "notes": notes,
        "message": (
            "Legend LLM を ON にしましたが MLX/transformers が未導入のため規則のみで実行しました。"
            " 導入: ./install.sh を再実行（Apple Silicon は MLX を自動導入）"
            + profile_extra
        ),
    }


def legends_any_citation(legends: list[LegendFigureJSON]) -> bool:
    return any(leg.citation.mentioned for leg in legends)


def legends_any_reuse_statement(legends: list[LegendFigureJSON]) -> bool:
    """Explicit "reproduced/adapted/modified/taken from …" — not mere reference citations."""
    return any(leg.citation.reproduced_from for leg in legends)


def legends_to_artifact(legends: list[LegendFigureJSON]) -> list[dict[str, Any]]:
    return [leg.to_dict() for leg in legends]
