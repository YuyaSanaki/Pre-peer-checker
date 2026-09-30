"""Fig ↔ 表 Entity Linking（Tier1 指紋 / Tier2 統計 / soft パス）。

未投入データは ``data_missing``（入力ギャップ）として ``unlinked``（エンジン失敗）と区別する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pre_peer_checker.data.fingerprint import (
    DataFingerprint,
    fingerprint_from_vector,
    stats_close,
    values_exact_match,
)
from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.engine.n_and_names import (
    TableLink,
    figure_num_from_label,
    group_keys_for_panel,
    is_plot_quant_table,
    path_claims_figure,
    path_soft_compatible_with_figure,
    resolve_table_link,
)
from pre_peer_checker.engine.key_normalize import expand_key_set, keys_overlap_via_tier3
from pre_peer_checker.parsers.legend_struct import PanelN


class LinkStatus(str, Enum):
    LINKED = "linked"
    UNLINKED = "unlinked"
    DATA_MISSING = "data_missing"  # user likely forgot to drop files into data/


class LinkTier(str, Enum):
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"  # notation-normalized candidate keys (rules / LLM propose → machine adopt)
    SOFT = "soft"
    NONE = "none"


@dataclass(frozen=True)
class LegendNumericHint:
    """Legend-claimed mean (±err) for Tier2 disambiguation when n collides."""

    mean: float
    n: int | None = None
    group: str | None = None
    err: float | None = None


# "Control 1.2±0.1 (n=6)" / "mutant: 2.0 ± 0.2, n = 5"
_LEGEND_MEAN_N_RE = re.compile(
    r"(?P<label>[A-Za-z][\w+/.\-]{0,40}?)\s*[:=]?\s*"
    r"(?P<mean>\d+(?:\.\d+)?)\s*[±\+\-]\s*(?P<err>\d+(?:\.\d+)?)"
    r"(?:\s*[\(,;]?\s*n\s*=\s*(?P<n>\d+)\s*\)?)?",
    re.I,
)
_LEGEND_MEAN_EQ_RE = re.compile(
    r"(?:mean|average)\s*(?:of\s+(?P<label>[\w+/.\-]+))?\s*[=≈:]\s*"
    r"(?P<mean>\d+(?:\.\d+)?)"
    r"(?:\s*[\(,;]?\s*n\s*=\s*(?P<n>\d+)\s*\)?)?",
    re.I,
)


def parse_legend_numeric_hints(*texts: str | None) -> list[LegendNumericHint]:
    """Extract mean±err (n=…) style claims from legend / Results snippets."""
    out: list[LegendNumericHint] = []
    seen: set[tuple[float, int | None, str | None]] = set()
    for text in texts:
        if not text:
            continue
        for m in _LEGEND_MEAN_N_RE.finditer(str(text)):
            mean = float(m.group("mean"))
            err = float(m.group("err"))
            n = int(m.group("n")) if m.group("n") else None
            label = (m.group("label") or "").strip() or None
            key = (round(mean, 6), n, label.lower() if label else None)
            if key in seen:
                continue
            seen.add(key)
            out.append(LegendNumericHint(mean=mean, n=n, group=label, err=err))
        for m in _LEGEND_MEAN_EQ_RE.finditer(str(text)):
            mean = float(m.group("mean"))
            n = int(m.group("n")) if m.group("n") else None
            label = (m.group("label") or "").strip() or None
            key = (round(mean, 6), n, label.lower() if label else None)
            if key in seen:
                continue
            seen.add(key)
            out.append(LegendNumericHint(mean=mean, n=n, group=label, err=None))
    return out


@dataclass
class EntityLink:
    status: LinkStatus
    tier: LinkTier
    vector: GroupVector | None = None
    score: int = 0
    reason: str = ""
    fingerprint: DataFingerprint | None = None

    @property
    def table_link(self) -> TableLink | None:
        if self.vector is None or self.status != LinkStatus.LINKED:
            return None
        return TableLink(vector=self.vector, score=self.score, reason=self.reason)


def _nonempty(vectors: list[GroupVector]) -> list[GroupVector]:
    return [v for v in vectors if v.n > 0 and v.values]


def _is_other_figure_path(path: Path, fnum: str | None) -> bool:
    """True if path clearly belongs to a different figure number."""
    if not fnum:
        return False
    blob = str(path).lower().replace(" ", "").replace("_", "").replace("-", "")
    nums = re.findall(r"fig(?:ure)?(\d+)", blob)
    if not nums:
        return False
    target = fnum.lstrip("0") or fnum
    return all((n.lstrip("0") or n) != target for n in nums)


def _looks_like_manuscript_sidecar(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    if "word" in parts or ("manuscript" in parts and "data" not in parts):
        name = path.name.lower()
        if name.startswith("ref") or "reference" in name or name.endswith(".docx"):
            return True
        if path.suffix.lower() in {".xlsx", ".xls", ".csv"} and "data" not in parts:
            if "word" in parts:
                return True
    return False


def candidate_vectors(
    vectors: list[GroupVector],
    *,
    fnum: str | None = None,
    prefer_plot: bool | None,
    require_nonempty: bool = True,
    restrict_figure_path: bool = False,
) -> list[GroupVector]:
    """Table vectors usable for linking.

    By default does **not** filter by Fig-folder path — linking is content first,
    with the experiment-unit folder as a prior. Pass ``restrict_figure_path=True``
    only for legacy soft inventories.
    """
    pool = _nonempty(vectors) if require_nonempty else list(vectors)
    out: list[GroupVector] = []
    for v in pool:
        if v.source.suffix.lower() not in {".xlsx", ".xls", ".csv", ".pzfx"}:
            continue
        if _looks_like_manuscript_sidecar(v.source):
            continue
        is_plot = is_plot_quant_table(v.source)
        if prefer_plot is True and not is_plot:
            continue
        if prefer_plot is False and is_plot:
            continue
        if restrict_figure_path and fnum is not None:
            if _is_other_figure_path(v.source, fnum):
                continue
            if not path_soft_compatible_with_figure(v.source, fnum):
                continue
        out.append(v)
    return out


def candidate_table_paths(
    table_paths: list[Path] | None,
    *,
    fnum: str | None = None,
    prefer_plot: bool | None,
    restrict_figure_path: bool = False,
) -> list[Path]:
    """Inventory of table *files* (may not parse)."""
    out: list[Path] = []
    for p in table_paths or []:
        if p.suffix.lower() not in {".xlsx", ".xls", ".csv", ".pzfx"}:
            continue
        if _looks_like_manuscript_sidecar(p):
            continue
        is_plot = is_plot_quant_table(p)
        if prefer_plot is True and not is_plot:
            continue
        if prefer_plot is False and is_plot:
            continue
        if restrict_figure_path and fnum is not None:
            if _is_other_figure_path(p, fnum):
                continue
            if not path_soft_compatible_with_figure(p, fnum):
                continue
        out.append(p)
    return out


def bundle_has_data_files(
    vectors: list[GroupVector],
    *,
    prefer_plot: bool | None = False,
    table_paths: list[Path] | None = None,
) -> bool:
    """True if the run has any usable experimental/plot tables (global, not per-Fig)."""
    if candidate_table_paths(table_paths, prefer_plot=prefer_plot):
        return True
    return bool(
        candidate_vectors(
            vectors, prefer_plot=prefer_plot, require_nonempty=False
        )
    )


def figure_has_data_files(
    vectors: list[GroupVector],
    *,
    fnum: str | None,
    prefer_plot: bool | None = False,
    table_paths: list[Path] | None = None,
) -> bool:
    """Backward-compatible alias — inventory is global (folder name is not required)."""
    del fnum  # unused; kept for call-site compatibility
    return bundle_has_data_files(
        vectors, prefer_plot=prefer_plot, table_paths=table_paths
    )


def _group_ok(v: GroupVector, keys: set[str]) -> bool:
    if not keys:
        return v.group_key.lower() in {"all", ""}
    gk = v.group_key.lower()
    if gk in keys or gk.replace(" ", "") in keys:
        return True
    from pre_peer_checker.engine.key_normalize import normalize_group_token

    gn = normalize_group_token(v.group_key)
    if gn and gn in keys:
        return True
    # expanded key set may already hold normalized forms of legend labels
    return any(normalize_group_token(k) == gn for k in keys if k)


def _unique_by_source(hits: list[GroupVector]) -> GroupVector | None:
    sources = {h.source.resolve() for h in hits}
    if len(sources) != 1:
        return None
    # Prefer largest n within that source
    return max(hits, key=lambda h: h.n)


def _figure_scoped(
    candidates: list[GroupVector],
    fnum: str | None,
) -> list[GroupVector]:
    """Candidates that claim this figure — empty if none (no global fallback)."""
    if not fnum:
        return list(candidates)
    return [v for v in candidates if path_claims_figure(v.source, fnum)]


def script_supported_sources(
    pn: PanelN,
    script_artifacts: list[dict] | None,
) -> set[Path]:
    """Resolved table paths from scripts that mention this figure or sit under it."""
    out: set[Path] = set()
    if not script_artifacts:
        return out
    fnum = figure_num_from_label(pn.figure)
    for art in script_artifacts:
        path = str(art.get("path") or "")
        path_l = path.lower().replace(" ", "")
        script_ok = False
        if fnum and re.search(rf"fig(?:ure)?_?{fnum}", path_l):
            script_ok = True
        if not script_ok and fnum and path_claims_figure(Path(path), fnum):
            script_ok = True
        # Collect resolved reads always when script is fig-local; also when read path claims fig
        for rd in art.get("reads") or []:
            if not isinstance(rd, dict):
                continue
            rp = rd.get("resolved_path") or rd.get("path") or ""
            if not rp:
                continue
            rp_path = Path(str(rp))
            if script_ok or (fnum and path_claims_figure(rp_path, fnum)):
                try:
                    out.add(rp_path.resolve())
                except OSError:
                    out.add(rp_path)
    return out


def _prefer_script_supported(
    candidates: list[GroupVector],
    supported: set[Path],
) -> list[GroupVector]:
    if not supported or not candidates:
        return candidates
    boosted = [v for v in candidates if v.source.resolve() in supported]
    return boosted if boosted else candidates


def _tier1_by_anchor(
    candidates: list[GroupVector],
    anchor: GroupVector,
    *,
    fnum: str | None = None,
) -> EntityLink | None:
    hits = [
        v
        for v in candidates
        if v.source.resolve() != anchor.source.resolve()
        and values_exact_match(anchor.values, v.values)
    ]
    misplaced = False
    if fnum:
        local = [v for v in hits if path_claims_figure(v.source, fnum)]
        if local:
            hits = local
        elif hits:
            # Unique exact values in another Fig folder: misplaced file, still link.
            misplaced = True
    best = _unique_by_source(hits)
    if best is None:
        return None
    fp = fingerprint_from_vector(best)
    note = " · 別実験フォルダ" if misplaced else ""
    return EntityLink(
        status=LinkStatus.LINKED,
        tier=LinkTier.TIER1,
        vector=best,
        score=95 if misplaced else 100,
        reason=f"指紋一致{note} · hash={fp.value_hash} · n={fp.n}",
        fingerprint=fp,
    )


def _tier2_stats_to_anchor(
    candidates: list[GroupVector],
    anchor: GroupVector,
    *,
    fnum: str | None = None,
) -> EntityLink | None:
    af = fingerprint_from_vector(anchor)
    hits = [
        v
        for v in candidates
        if v.source.resolve() != anchor.source.resolve()
        and stats_close(af, fingerprint_from_vector(v), mean_eps=1e-3, sd_eps=1e-3)
    ]
    if fnum:
        local = [v for v in hits if path_claims_figure(v.source, fnum)]
        if local:
            hits = local
        elif hits:
            return None
    best = _unique_by_source(hits)
    if best is None:
        return None
    fp = fingerprint_from_vector(best)
    return EntityLink(
        status=LinkStatus.LINKED,
        tier=LinkTier.TIER2,
        vector=best,
        score=70,
        reason=f"統計近似 · n={fp.n} mean≈{fp.mean:.4g}",
        fingerprint=fp,
    )


def _tier1_unique_n(
    candidates: list[GroupVector],
    pn: PanelN,
    keys: set[str],
    *,
    fnum: str | None = None,
) -> EntityLink | None:
    """Unique source whose group has legend n (figure-scoped)."""
    pool = _figure_scoped(candidates, fnum)
    if fnum and not pool:
        return None
    n_hits = [v for v in pool if v.n == pn.n]
    if not n_hits:
        return None
    if keys:
        keyed = [v for v in n_hits if _group_ok(v, keys)]
        if keyed:
            n_hits = keyed
    best = _unique_by_source(n_hits)
    if best is None:
        return None
    sources = {v.source.resolve() for v in n_hits}
    if len(sources) != 1:
        return None
    fp = fingerprint_from_vector(best)
    return EntityLink(
        status=LinkStatus.LINKED,
        tier=LinkTier.TIER1,
        vector=best,
        score=90,
        reason=f"指紋·唯一n一致 · hash={fp.value_hash} · n={fp.n}",
        fingerprint=fp,
    )


def _tier2_unique_n_group(
    candidates: list[GroupVector],
    pn: PanelN,
    keys: set[str],
    *,
    fnum: str | None = None,
) -> EntityLink | None:
    if not keys:
        return None
    pool = _figure_scoped(candidates, fnum)
    if fnum and not pool:
        return None
    hits = [v for v in pool if v.n == pn.n and _group_ok(v, keys)]
    best = _unique_by_source(hits)
    if best is None:
        return None
    fp = fingerprint_from_vector(best)
    return EntityLink(
        status=LinkStatus.LINKED,
        tier=LinkTier.TIER2,
        vector=best,
        score=60,
        reason=f"n+group一致 · group={best.group_key} · n={fp.n}",
        fingerprint=fp,
    )


def _cross_folder_unique_n_group(
    candidates: list[GroupVector],
    pn: PanelN,
    keys: set[str],
    *,
    fnum: str | None = None,
) -> EntityLink | None:
    """Misplaced file: unique n+group outside folders/names that claim this figure.

    n-only is not enough — similar experiments reuse the same n across figures.
    """
    if not fnum or not keys:
        return None
    hits = [
        v
        for v in candidates
        if v.n == pn.n and _group_ok(v, keys) and not path_claims_figure(v.source, fnum)
    ]
    best = _unique_by_source(hits)
    if best is None:
        return None
    fp = fingerprint_from_vector(best)
    return EntityLink(
        status=LinkStatus.LINKED,
        tier=LinkTier.TIER2,
        vector=best,
        score=55,
        reason=f"n+group一致 · 別実験フォルダ · group={best.group_key} · n={fp.n}",
        fingerprint=fp,
    )


def _tier2_by_legend_mean(
    candidates: list[GroupVector],
    pn: PanelN,
    keys: set[str],
    hints: list[LegendNumericHint],
    *,
    fnum: str | None = None,
    mean_eps: float = 0.05,
    mean_rel: float = 0.08,
) -> EntityLink | None:
    """Disambiguate same-n table groups using Legend-claimed means."""
    if not hints:
        return None
    pool = _figure_scoped(candidates, fnum)
    if fnum and not pool:
        return None
    n_pool = [v for v in pool if v.n == pn.n]
    if len(n_pool) < 2 and not keys:
        # Still useful when a single candidate matches claimed mean+n
        pass
    if not n_pool:
        return None

    relevant = []
    key_l = {k.lower() for k in keys}
    for h in hints:
        if h.n is not None and h.n != pn.n:
            continue
        if keys and h.group:
            gl = h.group.lower()
            if gl not in key_l and gl.replace(" ", "") not in key_l:
                # still keep if token overlap via expand
                from pre_peer_checker.engine.key_normalize import normalize_group_token

                if normalize_group_token(h.group) not in {
                    normalize_group_token(k) for k in keys
                }:
                    continue
        relevant.append(h)
    if not relevant:
        relevant = [h for h in hints if h.n is None or h.n == pn.n]
    if not relevant:
        return None

    scored: list[tuple[float, GroupVector, LegendNumericHint]] = []
    for h in relevant:
        local: list[tuple[float, GroupVector]] = []
        for v in n_pool:
            if keys and not _group_ok(v, keys):
                if h.group:
                    continue
            fp = fingerprint_from_vector(v)
            tol = max(mean_eps, abs(h.mean) * mean_rel)
            gap = abs(fp.mean - h.mean)
            if gap <= tol:
                local.append((gap, v))
        if not local:
            continue
        local.sort(key=lambda t: t[0])
        best_gap, best_v = local[0]
        rivals = [
            v
            for gap, v in local
            if v.source.resolve() != best_v.source.resolve()
            and gap <= best_gap + 1e-9
        ]
        if rivals:
            continue
        scored.append((best_gap, best_v, h))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0])
    best_gap, best_v, best_h = scored[0]
    fp = fingerprint_from_vector(best_v)
    return EntityLink(
        status=LinkStatus.LINKED,
        tier=LinkTier.TIER2,
        vector=best_v,
        score=65,
        reason=f"Legend数値≈mean · claimed={best_h.mean:g} · n={fp.n}",
        fingerprint=fp,
    )


def _from_soft(
    soft: TableLink | None,
    *,
    missing_detail: str,
    unlinked_detail: str,
    empty_extract_detail: str,
    had_files: bool,
    had_nonempty: bool,
) -> EntityLink:
    if soft is not None and soft.vector.n > 0:
        fp = fingerprint_from_vector(soft.vector)
        return EntityLink(
            status=LinkStatus.LINKED,
            tier=LinkTier.SOFT,
            vector=soft.vector,
            score=soft.score,
            reason=soft.reason,
            fingerprint=fp,
        )
    if not had_files:
        return EntityLink(
            status=LinkStatus.DATA_MISSING,
            tier=LinkTier.NONE,
            reason=missing_detail,
        )
    if not had_nonempty:
        return EntityLink(
            status=LinkStatus.UNLINKED,
            tier=LinkTier.NONE,
            reason=empty_extract_detail,
        )
    return EntityLink(
        status=LinkStatus.UNLINKED,
        tier=LinkTier.NONE,
        reason=unlinked_detail,
    )


def _tier3_via_normalized_keys(
    pn: PanelN,
    vectors: list[GroupVector],
    *,
    base_keys: set[str],
    prefer_plot: bool,
    min_soft_score: int,
    key_alias_map: dict[str, set[str]] | None = None,
) -> EntityLink | None:
    """Retry soft / n+group link after expanding notation variants.

    Only fires when the match would *not* succeed on base keys alone
    (so Tier3 is the notation bridge, not a free soft upgrade).
    """
    expanded = expand_key_set(base_keys, extra_aliases=key_alias_map)
    if expanded <= base_keys:
        return None
    extra = sorted(expanded - base_keys)

    # Prefer unique n+group after expansion (stronger than soft path score)
    fnum = figure_num_from_label(pn.figure)
    cands = candidate_vectors(vectors, fnum=fnum, prefer_plot=prefer_plot)
    hit = _tier2_unique_n_group(cands, pn, expanded, fnum=fnum)
    if hit is not None and hit.vector is not None:
        if keys_overlap_via_tier3(
            base_keys, hit.vector.group_key, extra_aliases=key_alias_map
        ):
            return EntityLink(
                status=LinkStatus.LINKED,
                tier=LinkTier.TIER3,
                vector=hit.vector,
                score=max(55, hit.score - 5),
                reason=f"候補キー接地 · {hit.vector.group_key} ← {sorted(base_keys)[:3]}",
                fingerprint=hit.fingerprint,
            )

    soft = resolve_table_link(
        pn,
        vectors,
        prefer_plot=prefer_plot,
        extra_groups=extra,
        min_score=min_soft_score,
    )
    if soft is None or soft.vector.n <= 0:
        return None
    if not keys_overlap_via_tier3(
        base_keys, soft.vector.group_key, extra_aliases=key_alias_map
    ):
        return None
    fp = fingerprint_from_vector(soft.vector)
    return EntityLink(
        status=LinkStatus.LINKED,
        tier=LinkTier.TIER3,
        vector=soft.vector,
        score=max(40, soft.score - 5),
        reason=f"候補キー接地 · {soft.reason}",
        fingerprint=fp,
    )


def _source_data_link(pn: PanelN, vectors: list[GroupVector]) -> EntityLink | None:
    """Link via Source Data block headings (Fig. 4h / (Fig. 4b)); None if no such workbook."""
    from pre_peer_checker.data.source_data_blocks import (
        is_source_data_workbook,
        parse_source_data_blocks,
    )
    from pre_peer_checker.engine.source_data_checks import match_source_block_for_panel

    sd_vectors = [v for v in vectors if is_source_data_workbook(v.source)]
    if not sd_vectors:
        return None
    blocks = []
    for path in dict.fromkeys(v.source for v in sd_vectors):
        blocks.extend(parse_source_data_blocks(path))
    block, covered = match_source_block_for_panel(pn, blocks)
    if block is None:
        if covered:
            group = f" ({pn.group})" if pn.group else ""
            return EntityLink(
                status=LinkStatus.UNLINKED,
                tier=LinkTier.NONE,
                reason=f"Source Data に{group}を指すブロックなし（見出しのパネル記号を確認）",
            )
        return EntityLink(
            status=LinkStatus.DATA_MISSING,
            tier=LinkTier.NONE,
            reason=f"Source Data に {pn.figure} のブロックなし",
        )
    vec = next(
        (
            v
            for v in sd_vectors
            if v.source.resolve() == block.path and v.group_key == block.label
        ),
        None,
    )
    if vec is None:
        return None
    return EntityLink(
        status=LinkStatus.LINKED,
        tier=LinkTier.TIER1,
        vector=vec,
        score=100,
        reason=f"Source Data 見出し · {block.label}",
        fingerprint=fingerprint_from_vector(vec),
    )


def _without_source_data(
    vectors: list[GroupVector], table_paths: list[Path] | None
) -> tuple[list[GroupVector], list[Path] | None]:
    from pre_peer_checker.data.source_data_blocks import is_source_data_workbook

    vs = [v for v in vectors if not is_source_data_workbook(v.source)]
    ps = (
        [p for p in table_paths if not is_source_data_workbook(p)]
        if table_paths is not None
        else None
    )
    return vs, ps


def link_raw_for_panel(
    pn: PanelN,
    vectors: list[GroupVector],
    *,
    table_paths: list[Path] | None = None,
    **kwargs,
) -> EntityLink:
    """Best experimental table for a legend panel; Source Data blocks count as raw data."""
    sd = _source_data_link(pn, vectors)
    if sd is not None and sd.status == LinkStatus.LINKED:
        return sd
    if sd is not None:
        vectors, table_paths = _without_source_data(vectors, table_paths)
    link = _link_raw_generic(pn, vectors, table_paths=table_paths, **kwargs)
    if sd is not None and link.status != LinkStatus.LINKED:
        return sd
    return link


def link_plot_for_panel(
    pn: PanelN,
    vectors: list[GroupVector],
    *,
    table_paths: list[Path] | None = None,
    **kwargs,
) -> EntityLink:
    """Best plot table for a legend panel; Source Data blocks are the plotted values."""
    sd = _source_data_link(pn, vectors)
    if sd is not None and sd.status == LinkStatus.LINKED:
        return sd
    if sd is not None:
        vectors, table_paths = _without_source_data(vectors, table_paths)
    link = _link_plot_generic(pn, vectors, table_paths=table_paths, **kwargs)
    if sd is not None and link.status != LinkStatus.LINKED:
        return sd
    return link


def _link_raw_generic(
    pn: PanelN,
    vectors: list[GroupVector],
    *,
    plot_anchor: GroupVector | None = None,
    extra_groups: list[str] | None = None,
    min_soft_score: int = 6,
    table_paths: list[Path] | None = None,
    script_artifacts: list[dict] | None = None,
    legend_hints: list[LegendNumericHint] | None = None,
    key_alias_map: dict[str, set[str]] | None = None,
) -> EntityLink:
    """Best experimental (non-plot) table for a legend panel."""
    fnum = figure_num_from_label(pn.figure)
    keys = group_keys_for_panel(pn, extra_groups)
    if key_alias_map:
        keys = expand_key_set(keys, extra_aliases=key_alias_map)
    cands = candidate_vectors(vectors, fnum=fnum, prefer_plot=False)
    supported = script_supported_sources(pn, script_artifacts)
    cands_fp = _prefer_script_supported(cands, supported)
    had_nonempty = bool(cands)
    had_files = bundle_has_data_files(
        vectors, prefer_plot=False, table_paths=table_paths
    )
    hints = list(legend_hints or []) or parse_legend_numeric_hints(pn.context)

    if plot_anchor is not None and plot_anchor.n <= 0:
        plot_anchor = None

    if plot_anchor is not None:
        hit = _tier1_by_anchor(cands_fp, plot_anchor, fnum=fnum)
        if hit is not None:
            if supported and hit.vector and hit.vector.source.resolve() in supported:
                hit = EntityLink(
                    status=hit.status,
                    tier=hit.tier,
                    vector=hit.vector,
                    score=hit.score,
                    reason=hit.reason + " · script三角",
                    fingerprint=hit.fingerprint,
                )
            return hit
        hit = _tier2_stats_to_anchor(cands_fp, plot_anchor, fnum=fnum)
        if hit is not None:
            return hit

    hit = _tier1_unique_n(cands_fp, pn, keys, fnum=fnum)
    if hit is not None:
        if supported and hit.vector and hit.vector.source.resolve() in supported:
            hit = EntityLink(
                status=hit.status,
                tier=hit.tier,
                vector=hit.vector,
                score=hit.score,
                reason=hit.reason + " · script三角",
                fingerprint=hit.fingerprint,
            )
        return hit
    hit = _tier2_unique_n_group(cands_fp, pn, keys, fnum=fnum)
    if hit is not None:
        return hit
    hit = _tier2_by_legend_mean(cands_fp, pn, keys, hints, fnum=fnum)
    if hit is not None:
        return hit
    hit = _cross_folder_unique_n_group(cands_fp, pn, keys, fnum=fnum)
    if hit is not None:
        return hit

    soft = resolve_table_link(
        pn, vectors, prefer_plot=False, extra_groups=extra_groups, min_score=min_soft_score
    )
    linked = _from_soft(
        soft,
        missing_detail="データ未投入（実験表ファイルなし）",
        unlinked_detail="実験表未紐付け",
        empty_extract_detail="実験表はあるが有効な数値群を抽出できず",
        had_files=had_files,
        had_nonempty=had_nonempty,
    )
    if linked.status == LinkStatus.LINKED:
        if (
            linked.vector is not None
            and keys
            and keys_overlap_via_tier3(
                keys, linked.vector.group_key, extra_aliases=key_alias_map
            )
        ):
            return EntityLink(
                status=LinkStatus.LINKED,
                tier=LinkTier.TIER3,
                vector=linked.vector,
                score=linked.score,
                reason=f"候補キー接地 · {linked.reason}",
                fingerprint=linked.fingerprint,
            )
        return linked

    tier3 = _tier3_via_normalized_keys(
        pn,
        vectors,
        base_keys=group_keys_for_panel(pn, extra_groups),
        prefer_plot=False,
        min_soft_score=min_soft_score,
        key_alias_map=key_alias_map,
    )
    if tier3 is not None:
        return tier3
    return linked


def _link_plot_generic(
    pn: PanelN,
    vectors: list[GroupVector],
    *,
    raw_anchor: GroupVector | None = None,
    extra_groups: list[str] | None = None,
    min_soft_score: int = 6,
    table_paths: list[Path] | None = None,
    script_artifacts: list[dict] | None = None,
    legend_hints: list[LegendNumericHint] | None = None,
    key_alias_map: dict[str, set[str]] | None = None,
) -> EntityLink:
    """Best plot/quant table for a legend panel."""
    fnum = figure_num_from_label(pn.figure)
    keys = group_keys_for_panel(pn, extra_groups)
    if key_alias_map:
        keys = expand_key_set(keys, extra_aliases=key_alias_map)
    cands = candidate_vectors(vectors, fnum=fnum, prefer_plot=True)
    supported = script_supported_sources(pn, script_artifacts)
    cands_fp = _prefer_script_supported(cands, supported)
    had_nonempty = bool(cands)
    had_files = bundle_has_data_files(
        vectors, prefer_plot=True, table_paths=table_paths
    )
    hints = list(legend_hints or []) or parse_legend_numeric_hints(pn.context)

    if raw_anchor is not None and raw_anchor.n <= 0:
        raw_anchor = None

    if raw_anchor is not None:
        hit = _tier1_by_anchor(cands_fp, raw_anchor, fnum=fnum)
        if hit is not None:
            return hit
        hit = _tier2_stats_to_anchor(cands_fp, raw_anchor, fnum=fnum)
        if hit is not None:
            return hit

    hit = _tier1_unique_n(cands_fp, pn, keys, fnum=fnum)
    if hit is not None:
        return hit
    hit = _tier2_unique_n_group(cands_fp, pn, keys, fnum=fnum)
    if hit is not None:
        return hit
    hit = _tier2_by_legend_mean(cands_fp, pn, keys, hints, fnum=fnum)
    if hit is not None:
        return hit
    hit = _cross_folder_unique_n_group(cands_fp, pn, keys, fnum=fnum)
    if hit is not None:
        return hit

    soft = resolve_table_link(
        pn, vectors, prefer_plot=True, extra_groups=extra_groups, min_score=min_soft_score
    )
    linked = _from_soft(
        soft,
        missing_detail="データ未投入（作図表ファイルなし）",
        unlinked_detail="作図ファイル未紐付け",
        empty_extract_detail="作図表はあるが有効な数値群を抽出できず",
        had_files=had_files,
        had_nonempty=had_nonempty,
    )
    if linked.status == LinkStatus.LINKED:
        if (
            linked.vector is not None
            and keys
            and keys_overlap_via_tier3(
                keys, linked.vector.group_key, extra_aliases=key_alias_map
            )
        ):
            return EntityLink(
                status=LinkStatus.LINKED,
                tier=LinkTier.TIER3,
                vector=linked.vector,
                score=linked.score,
                reason=f"候補キー接地 · {linked.reason}",
                fingerprint=linked.fingerprint,
            )
        return linked

    tier3 = _tier3_via_normalized_keys(
        pn,
        vectors,
        base_keys=group_keys_for_panel(pn, extra_groups),
        prefer_plot=True,
        min_soft_score=min_soft_score,
        key_alias_map=key_alias_map,
    )
    if tier3 is not None:
        return tier3
    return linked


def summarize_input_gaps(rows_link_status: list[str]) -> dict[str, int]:
    """Count link statuses for coverage / reports."""
    out = {"linked": 0, "unlinked": 0, "data_missing": 0}
    for s in rows_link_status:
        if s in out:
            out[s] += 1
        elif s == LinkStatus.DATA_MISSING.value:
            out["data_missing"] += 1
    return out
