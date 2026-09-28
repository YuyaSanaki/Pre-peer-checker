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
    rule_empty_pn: set[tuple[str, int]],
) -> list[LegendPanelJSON]:
    """Drop LLM rows that reassign an n already owned by rules on another panel.

    Typo / section-letter cases: rules L=140, M=88 (empty group); LLM invents
    K=140 or P=140/(k) — those steal the n and must not survive the merge.
    Shared n on multiple rule panels (D,E,G,H=10) still allows those owners.
    """
    if not rule_empty_pn:
        return llm_panels
    n_to_owners: dict[int, set[str]] = {}
    for panel, n in rule_empty_pn:
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


def _paneln_to_legend_json(pn: PanelN) -> LegendPanelJSON:
    return LegendPanelJSON(
        panel=pn.panel.upper(),
        n=pn.n,
        groups=[pn.group] if getattr(pn, "group", "") else [],
        notes=(pn.context or "")[:120],
        n_scope="per_group" if getattr(pn, "group", "") else "unknown",
        evidence_span=(pn.context or "")[:160],
        confidence=0.95,
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
) -> LegendFigureJSON:
    """Merge LLM + rules. Rules own parenthesis class (panel-letter → empty group).

    - Drop K←(H,I,J) section misattribution and dose-on-A mistakes.
    - If rules assign panel+n with empty group, strip LLM-invented groups on that key.
    - Always keep rule rows the LLM omitted (shared n, last list element, published styles).
    """
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

    if llm_primary and parsed.panels:
        kept = [p for p in parsed.panels if not _is_misattributed_section_panel(p)]
        kept = _drop_dose_on_wrong_panels(kept)
        kept = _drop_llm_n_stolen_from_rules(kept, rule_empty_pn)
        by_key: dict[tuple[str, str, int | None], LegendPanelJSON] = {}
        overlaid = False
        for p in kept:
            g = str(p.groups[0]) if p.groups else ""
            if (
                p.n is not None
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
                )
                g = ""
                overlaid = True
            by_key[(p.panel.upper(), g, p.n)] = p
        llm_covered = {k[0] for k in by_key}
        llm_empty_pn = {
            (k[0], k[2]) for k in by_key if not k[1] and k[2] is not None
        }
        for rp in base.panels:
            g = str(rp.groups[0]) if rp.groups else ""
            key = (rp.panel.upper(), g, rp.n)
            if key in by_key:
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
        parsed.panels = _drop_dose_on_wrong_panels(list(by_key.values()))
        parsed.extractor = "llm+rules" if overlaid else "llm"
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
    )


def extract_check_items_from_chunk(
    chunk: FigureChunk,
    *,
    llm_generate: Callable[[str], str] | None = None,
    prefer_llm: bool = False,
    legend_only_prompt: bool = False,
    llm_primary: bool = True,
) -> LegendFigureJSON:
    """Rules fallback; when prefer_llm, LLM panels take priority by default."""
    base = _rules_from_chunk(chunk)
    if not prefer_llm or llm_generate is None:
        return base
    if legend_only_prompt:
        prompt = build_legend_llm_prompt(chunk.legend or "", figure_hint=chunk.figure_id)
    else:
        prompt = build_figure_chunk_llm_prompt(
            chunk.prompt_body(),
            figure_hint=chunk.figure_id,
        )
    try:
        raw = llm_generate(prompt)
        parsed = parse_legend_llm_response(raw)
    except Exception:
        return base
    if parsed is None:
        return base
    return _merge_llm_over_rules(base, parsed, llm_primary=llm_primary)


def extract_legends_json_from_docx(
    path: Path | str,
    *,
    prefer_llm: bool = False,
    llm_generate: Callable[[str], str] | None = None,
    use_figure_chunks: bool = True,
    figure_pdfs: list[Path] | None = None,
) -> tuple[list[LegendFigureJSON], list[FigureChunk]]:
    """Extract check-item JSON per figure; returns (items, chunks used)."""
    chunks = build_figure_chunks_from_docx(path) if use_figure_chunks else []
    if figure_pdfs and chunks:
        from pre_peer_checker.parsers.figure_panel_labels import (
            attach_panel_labels_to_chunks,
            collect_panel_labels_by_figure,
        )

        attach_panel_labels_to_chunks(
            chunks, collect_panel_labels_by_figure(list(figure_pdfs))
        )
    if not chunks:
        # Fallback: legend blocks only
        out: list[LegendFigureJSON] = []
        for leg in extract_structured_legends(path):
            out.append(
                extract_legend_json_hybrid(
                    leg.text,
                    figure_hint=leg.figure,
                    llm_generate=llm_generate,
                    prefer_llm=prefer_llm,
                )
            )
        return out, []

    out = [
        extract_check_items_from_chunk(
            ch,
            llm_generate=llm_generate,
            prefer_llm=prefer_llm,
            legend_only_prompt=False,
            llm_primary=True,
        )
        for ch in chunks
    ]
    return out, chunks


def extract_legends_with_backend(
    path: Path | str,
    *,
    prefer: str = "auto",
    model_id: str | None = None,
    profile_id: str | None = None,
    enabled: bool = False,
    figure_pdfs: list[Path] | None = None,
) -> tuple[list[LegendFigureJSON], dict[str, Any]]:
    """Extract legends/check-items; optionally refine with MLX/CUDA when enabled."""
    meta: dict[str, Any] = {
        "legend_llm_enabled": enabled,
        "backend": None,
        "figure_chunk_mode": True,
        "llm_primary": bool(enabled),
        "llm_profile": profile_id,
    }
    if not enabled:
        items, chunks = extract_legends_json_from_docx(path, figure_pdfs=figure_pdfs)
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
    meta["backends_probed"] = [b.__dict__ for b in probe_backends()]
    backend = select_backend(prefer, model_id=model_id, profile_id=profile_id)
    if backend is None:
        meta["backend"] = "none"
        meta["note"] = "no MLX/transformers backend; used rules only"
        meta["llm_primary"] = False
        items, chunks = extract_legends_json_from_docx(path, figure_pdfs=figure_pdfs)
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

    items, chunks = extract_legends_json_from_docx(
        path, prefer_llm=True, llm_generate=_gen, figure_pdfs=figure_pdfs
    )
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
        return {
            "requested": True,
            "used": True,
            "status": "active",
            "backends": backends,
            "profiles": uniq_profiles,
            "json_modes": uniq_modes,
            "n_figure_chunks": sum(int(m.get("n_figure_chunks") or 0) for m in metas),
            "message": "Legend LLM を本線使用: "
            + (", ".join(backends) or "backend")
            + extra,
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
            ' 導入例: pip install -e ".[mlx]"（Apple Silicon）'
            + profile_extra
        ),
    }


def legends_any_citation(legends: list[LegendFigureJSON]) -> bool:
    return any(leg.citation.mentioned for leg in legends)


def legends_to_artifact(legends: list[LegendFigureJSON]) -> list[dict[str, Any]]:
    return [leg.to_dict() for leg in legends]
