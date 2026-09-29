"""Legend n と表データの突合、ファイル名ヒューリスティック、H2b。"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector, path_experiment_tokens
from pre_peer_checker.engine.case_profile import get_case_profile
from pre_peer_checker.engine.plot_table_match import values_match_score
from pre_peer_checker.parsers.legend_struct import PanelN
from pre_peer_checker.parsers.pdf_plot_digitize import DigitizedPlot
from pre_peer_checker.warnings import WarningItem, WarningTag

# Shown on every n-related Warning: authority is raw rows, not visible dots.
N_AUTHORITY_FOOTER = (
    "正の n は生データ群の有効行数です（ggplot の可視ドット数は使いません）。"
)
N_AUTHORITY_META = {"n_authority": "raw_data_nrows"}


def is_plot_quant_table(path: Path) -> bool:
    """True for plot/quant exports (graph*.xlsx, Prism .pzfx, etc.).

    A parent folder named ``Fig1`` is an experiment unit, not a plot-table
    marker — otherwise ``data/Fig1/WT.xlsx`` would be excluded from raw linking.
    """
    suf = path.suffix.lower()
    if suf in {".pzfx", ".pzf"}:
        return True
    name = path.name.lower()
    if suf not in {".xlsx", ".xls", ".csv"}:
        return False
    return name.startswith("graph") or name.startswith("quant_")


def figure_num_from_label(figure: str) -> str | None:
    m = re.search(r"(S?\d+)", figure, re.I)
    return m.group(1).upper() if m else None


def _norm_path_token(name: str) -> str:
    return name.lower().replace(" ", "").replace("_", "").replace("-", "")


def _same_fig_num(a: str, b: str) -> bool:
    a_u, b_u = a.upper(), b.upper()
    if a_u.startswith("S") != b_u.startswith("S"):
        return False
    return (a_u.lstrip("0") or a_u) == (b_u.lstrip("0") or b_u)


def figure_num_from_dir_name(name: str) -> str | None:
    """Figure key implied by a directory name (``Fig1`` → ``1``, ``FigS2`` → ``S2``)."""
    t = _norm_path_token(name)
    m = re.search(r"fig(?:ure)?supp?(\d+)", t)
    if m:
        return f"S{m.group(1)}"
    m = re.search(r"figs(\d+)", t)
    if m:
        return f"S{m.group(1)}"
    m = re.search(r"fig(?:ure)?(\d+)", t)
    if m:
        return m.group(1)
    return None


def experiment_unit_dir(path: Path) -> Path:
    """Directory treated as one experiment bundle.

    Prefer the nearest ancestor named ``Fig1`` / ``FigS1`` / etc. Otherwise the
    immediate child of ``data/``. Files sitting directly in ``data/`` have
    ``data`` itself as the unit (discouraged layout).
    """
    p = Path(path)
    for parent in p.parents:
        if figure_num_from_dir_name(parent.name):
            return parent
        if parent.name.lower() == "data":
            break
    parts = p.parts
    for i, part in enumerate(parts[:-1]):
        if part.lower() != "data":
            continue
        if i + 1 < len(parts) - 1:
            return Path(*parts[: i + 2])
        return Path(*parts[: i + 1])
    return p.parent


def dir_claims_figure(path: Path, fnum: str | None) -> bool:
    """True when the experiment-unit folder is named for this figure."""
    if not fnum:
        return False
    claimed = figure_num_from_dir_name(experiment_unit_dir(path).name)
    return bool(claimed) and _same_fig_num(claimed, fnum)


def other_experiment_folder(path: Path, fnum: str | None) -> bool:
    """True when the experiment-unit folder is named for a *different* figure."""
    if not fnum:
        return False
    claimed = figure_num_from_dir_name(experiment_unit_dir(path).name)
    return bool(claimed) and not _same_fig_num(claimed, fnum)


def path_compatible_with_figure(path: Path, fnum: str | None) -> bool:
    if not fnum:
        return True
    blob = str(path).lower().replace(" ", "")
    if fnum.startswith("S"):
        n = fnum[1:]
        return bool(re.search(rf"figs{n}|fig_s{n}|supplement|suppinfo|figsupp", blob))
    if re.search(rf"figs{fnum}", blob):
        return False
    # Explicit other FigM blocks figure alias folders (alias folder under Fig5, etc.)
    other_figs = re.findall(r"fig(?:ure)?(\d+)", blob.replace("_", "").replace("-", ""))
    target = fnum.lstrip("0") or fnum
    has_other = any((n.lstrip("0") or n) != target for n in other_figs)
    has_self = any((n.lstrip("0") or n) == target for n in other_figs)
    if has_self:
        return True
    if has_other:
        return False
    if get_case_profile().alias_tokens_for_figure(fnum) & path_experiment_tokens(path):
        return True
    # Unscoped plot tables (quant_*.csv / graph*) — usable for any main-figure n check
    if is_plot_quant_table(path) and not re.search(r"fig\d+", blob):
        return True
    return False


def path_claims_figure(path: Path, fnum: str | None) -> bool:
    """True when the path *claims* this figure (for Tier1/2 scoping).

    Stricter than ``path_soft_compatible`` / unscoped ``quant_*``: rejects
    cross-figure unique-n hijacks (e.g. Fig3 table linked to Figure 1 solely
    because it is the only n=56 in the whole bundle).
    """
    if not fnum:
        return True
    blob = str(path).lower().replace(" ", "").replace("_", "").replace("-", "")
    if fnum.startswith("S"):
        n = fnum[1:]
        return bool(re.search(rf"figs{n}|fig_?s{n}|supplement|suppinfo|figsupp", blob))
    # Any explicit FigM (M≠fnum) → not this figure
    other = re.findall(r"fig(?:ure)?(\d+)", blob)
    if other:
        target = fnum.lstrip("0") or fnum
        if any((n.lstrip("0") or n) != target for n in other):
            # only reject if NONE of the numbers match target
            if all((n.lstrip("0") or n) != target for n in other):
                return False
    if re.search(rf"figs{fnum}", blob):
        return False
    if re.search(rf"fig(?:ure)?{fnum}[a-z]?", blob):
        return True
    # Profile figure aliases (lab folder names) only when no other FigN is present
    if not other and (
        get_case_profile().alias_tokens_for_figure(fnum) & path_experiment_tokens(path)
    ):
        return True
    return False


def path_soft_compatible_with_figure(path: Path, fnum: str | None) -> bool:
    """Looser than path_compatible — allows flat layouts without FigN folders."""
    if path_compatible_with_figure(path, fnum):
        return True
    if not fnum or fnum.startswith("S"):
        return True
    blob = str(path).lower().replace(" ", "").replace("_", "").replace("-", "")
    if re.search(rf"fig(?:ure)?{fnum}[a-z]?", blob):
        return True
    # No figure number anywhere in path → candidate for soft match (score decides)
    if not re.search(r"fig(?:ure)?\d+", blob):
        return True
    return False


def path_mentions_panel(path: Path, panel: str, fnum: str | None) -> bool:
    blob = str(path).lower().replace(" ", "").replace("_", "").replace("-", "")
    p = panel.lower()
    if fnum and re.search(rf"fig(?:ure)?{fnum}{p}\b", blob):
        return True
    if re.search(rf"(?:panel|pan|p){p}\b", blob):
        return True
    return False


def group_keys_for_panel(
    pn: PanelN,
    extra_groups: list[str] | None = None,
) -> set[str]:
    """Union of PanelN.group, legend groups, and panel-letter heuristics."""
    keys: set[str] = set(_panel_group_keys(pn.panel))
    raw_groups: list[str] = []
    if getattr(pn, "group", None):
        raw_groups.append(str(pn.group))
    for g in extra_groups or []:
        if g:
            raw_groups.append(str(g))
    for g in raw_groups:
        gl = g.strip().lower()
        if not gl:
            continue
        keys.add(gl)
        keys.add(gl.replace(" ", ""))
        keys.add(gl.replace("-", ""))
        if gl in {"wt", "wildtype", "wild-type", "wild type"}:
            keys |= {"wt", "0", "control", "ctrl", "cont"}
        elif gl in {"control", "ctrl", "cont", "vehicle"}:
            keys |= {"control", "ctrl", "cont", "wt", "0", "1"}
        elif gl in {"a", "b", "c", "d"} and len(gl) == 1:
            keys.add(gl)
    return keys


def _vector_group_matches(v: GroupVector, keys: set[str]) -> bool:
    if not keys:
        return False
    gk = v.group_key.lower().strip()
    if gk in keys:
        return True
    if v.group_key.isdigit() and v.group_key in keys:
        return True
    compact = gk.replace(" ", "").replace("-", "").replace("_", "")
    return compact in keys


def _side_conflict(path: Path, panel: str) -> bool:
    profile = get_case_profile()
    side = profile.side(_panel_side(panel))
    if side is None:
        return False
    tokens = path_experiment_tokens(path)
    name = path.name.lower()
    return bool(profile.other_side_tokens(side.name) & tokens) and not (
        side.all_tokens & tokens or side.name_hit(name)
    )


def score_table_for_panel(
    path: Path,
    panel: str,
    fnum: str | None,
    *,
    group_matched: bool,
    n_matched: bool,
) -> int:
    """Higher is better. Used for both raw and plot tables."""
    score = _score_candidate(path, panel)
    blob = str(path).lower().replace(" ", "")
    if dir_claims_figure(path, fnum):
        score += 12
    elif fnum and re.search(rf"fig(?:ure)?_?{fnum}", blob):
        score += 8
    if path_mentions_panel(path, panel, fnum):
        score += 12
    if other_experiment_folder(path, fnum):
        score -= 4
    elif path_compatible_with_figure(path, fnum):
        score += 6
    elif path_soft_compatible_with_figure(path, fnum):
        score += 2
    else:
        score -= 15
    if group_matched:
        score += 10
    if n_matched:
        score += 4
    if is_plot_quant_table(path):
        score += 3
    if path.suffix.lower() in {".xlsx", ".xls", ".csv"}:
        score += 1
    return score


@dataclass
class TableLink:
    vector: GroupVector
    score: int
    reason: str


def resolve_table_link(
    pn: PanelN,
    vectors: list[GroupVector],
    *,
    prefer_plot: bool | None = None,
    extra_groups: list[str] | None = None,
    min_score: int = 8,
) -> TableLink | None:
    """Best table vector for a legend panel (raw or plot).

    prefer_plot=True → graph*/quant_* only; False → exclude those; None → any.
    Prefers the experiment-unit folder named for this figure. A file sitting
    under a *different* Fig folder is admitted only with group+n (misplaced
    files), never on n-only (similar experiments in every figure).

    Soft FP guards: Fig-folder alone is not enough; near-tie across different
    files refuses the link (競合 → 未紐付け).
    """
    fnum = figure_num_from_label(pn.figure)
    keys = group_keys_for_panel(pn, extra_groups)
    hits: list[TableLink] = []
    for v in vectors:
        if v.source.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
            continue
        is_plot = is_plot_quant_table(v.source)
        if prefer_plot is True and not is_plot:
            continue
        if prefer_plot is False and is_plot:
            continue
        if _side_conflict(v.source, pn.panel):
            continue
        other_folder = other_experiment_folder(v.source, fnum)
        if not other_folder and not path_soft_compatible_with_figure(v.source, fnum):
            continue
        group_ok = _vector_group_matches(v, keys) if keys else False
        n_ok = v.n == pn.n
        panel_in_path = path_mentions_panel(v.source, pn.panel, fnum)
        claims_fig = path_claims_figure(v.source, fnum) if fnum else True
        if other_folder and not (group_ok and n_ok):
            continue
        # Soft FP guard: living under FigN/ is not a concrete panel signal
        if not (group_ok or n_ok or panel_in_path):
            continue
        if keys and not group_ok and not n_ok:
            continue
        # n-only (no group / panel name): must claim this figure (not soft flat alias)
        if n_ok and not group_ok and not panel_in_path and not claims_fig:
            continue
        sc = score_table_for_panel(
            v.source,
            pn.panel,
            fnum,
            group_matched=group_ok,
            n_matched=n_ok,
        )
        reasons: list[str] = []
        if group_ok:
            reasons.append(f"group={v.group_key}")
        if n_ok:
            reasons.append("n一致")
        if path_compatible_with_figure(v.source, fnum):
            reasons.append("figパス")
        elif other_folder:
            reasons.append("別実験フォルダ")
        elif path_soft_compatible_with_figure(v.source, fnum):
            reasons.append("軟紐付け")
        if panel_in_path:
            reasons.append("panel名")
        if is_plot:
            reasons.append("作図表")
        hits.append(TableLink(vector=v, score=sc, reason=" · ".join(reasons) or "候補"))
    if not hits:
        return None
    hits.sort(key=lambda h: (h.score, h.vector.n), reverse=True)
    best = hits[0]
    if best.score < min_score:
        # Allow unique strong-ish n match even below threshold
        n_hits = [h for h in hits if h.vector.n == pn.n]
        sources = {h.vector.source.resolve() for h in n_hits}
        if len(n_hits) == 1 and n_hits[0].score >= min_score - 4:
            best = n_hits[0]
            best = TableLink(
                vector=best.vector,
                score=best.score,
                reason=(best.reason + " · 唯一n一致").strip(" ·"),
            )
        elif (
            len(sources) == 1
            and n_hits
            and n_hits[0].score >= min_score - 4
            and keys
            and _vector_group_matches(n_hits[0].vector, keys)
        ):
            best = n_hits[0]
            best = TableLink(
                vector=best.vector,
                score=best.score,
                reason=(best.reason + " · 唯一n一致").strip(" ·"),
            )
        else:
            return None

    def _n_only(reason: str) -> bool:
        return (
            "n一致" in reason
            and "group=" not in reason
            and "panel名" not in reason
        )

    # Ambiguous n-only soft across multiple files → refuse
    if _n_only(best.reason):
        same_n_files = {
            h.vector.source.resolve()
            for h in hits
            if h.vector.n == best.vector.n
        }
        if len(same_n_files) > 1:
            return None
        if fnum and not path_claims_figure(best.vector.source, fnum):
            return None

    # Near-tie across different files:
    # - no group match → refuse (Fig-path / n-only FP)
    # - group matched → keep best, annotate 競合あり (multi-condition soft)
    rivals = [
        h
        for h in hits[1:]
        if h.score >= best.score - 2
        and h.vector.source.resolve() != best.vector.source.resolve()
    ]
    if rivals:
        best_has_group = bool(keys) and _vector_group_matches(best.vector, keys)
        if not best_has_group:
            return None
        return TableLink(
            vector=best.vector,
            score=best.score,
            reason=best.reason + " · 競合あり",
        )
    return best


def _panel_side(panel: str) -> str | None:
    return get_case_profile().panel_side(panel)


def _panel_group_keys(panel: str) -> set[str]:
    p = panel.upper()
    mapping = get_case_profile().panel_group_keys
    if p in mapping:
        return set(mapping[p])
    # Generic panel ids (A1, B2, …): digit hint → common biology group labels
    m = re.search(r"(\d+)$", p)
    if m:
        if m.group(1) == "1":
            return {"ctrl", "control", "wt", "0", "cont"}
        if m.group(1) == "2":
            return {"mut", "kd", "treat", "1", "2"}
    return set()

def _score_candidate(path: Path, panel: str) -> int:
    score = 0
    name = path.name.lower()
    tokens = path_experiment_tokens(path)
    profile = get_case_profile()
    side = profile.side(_panel_side(panel))
    if name.startswith("graph"):
        score += 10
    if side is not None:
        if side.all_tokens & tokens or side.name_hit(name):
            score += 5
        if profile.other_side_tokens(side.name) & tokens:
            score -= 5
    if name == "d.xlsx":
        score -= 20
    return score


@dataclass
class NMismatch:
    panel_n: PanelN
    vector: GroupVector
    data_n: int


def resolve_panel_vector(
    pn: PanelN,
    vectors: list[GroupVector],
) -> GroupVector | None:
    """Best plot-quant table vector for a legend panel (regardless of n match)."""
    from pre_peer_checker.engine.entity_link import LinkStatus, link_plot_for_panel

    hit = link_plot_for_panel(pn, vectors)
    if hit.status == LinkStatus.LINKED:
        return hit.vector
    soft = resolve_table_link(pn, vectors, prefer_plot=True, min_score=6)
    return soft.vector if soft else None


def resolve_raw_data_vector(
    pn: PanelN,
    vectors: list[GroupVector],
    *,
    extra_groups: list[str] | None = None,
) -> TableLink | None:
    """Best non-plot experimental table for a legend panel."""
    from pre_peer_checker.engine.entity_link import LinkStatus, link_raw_for_panel

    hit = link_raw_for_panel(pn, vectors, extra_groups=extra_groups)
    if hit.status == LinkStatus.LINKED and hit.vector is not None:
        return TableLink(vector=hit.vector, score=hit.score, reason=hit.reason)
    return resolve_table_link(
        pn, vectors, prefer_plot=False, extra_groups=extra_groups, min_score=6
    )


def match_legend_n_to_vectors(
    panel_ns: list[PanelN],
    vectors: list[GroupVector],
) -> list[NMismatch]:
    """Legend n vs linked table n. Skips data_missing / unlinked panels."""
    from pre_peer_checker.engine.entity_link import LinkStatus, link_plot_for_panel, link_raw_for_panel

    mismatches: list[NMismatch] = []
    seen: set[tuple[str, str, int]] = set()

    for pn in panel_ns:
        plot = link_plot_for_panel(pn, vectors)
        raw = link_raw_for_panel(
            pn,
            vectors,
            plot_anchor=plot.vector if plot.status == LinkStatus.LINKED else None,
        )
        # Prefer confidently linked raw, else plot; never warn on input gaps
        best: GroupVector | None = None
        if raw.status == LinkStatus.LINKED and raw.vector is not None:
            best = raw.vector
        elif plot.status == LinkStatus.LINKED and plot.vector is not None:
            best = plot.vector
        else:
            continue
        if best.n == pn.n:
            continue
        dedupe = (pn.figure, pn.panel, pn.n)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        mismatches.append(NMismatch(panel_n=pn, vector=best, data_n=best.n))
    return mismatches


def _plot_side_tokens(path: Path) -> set[str]:
    return path_experiment_tokens(path)


def _digitized_content_for_panel(
    pn: PanelN,
    plots: list[DigitizedPlot],
) -> tuple[tuple[float, ...], Path] | None:
    """Published-plot content for a panel from side-matching digitized Rplots.

    Prefer residue plots whose filename carries the side label (Rplot<Side>) and
    profile residue folders over incidental folders that merely contain the side
    token and also digitize as ggplot exports.
    """
    profile = get_case_profile()
    side = profile.side(_panel_side(pn.panel))
    keys = _panel_group_keys(pn.panel)
    if side is None or not keys:
        return None
    side_toks = side.all_tokens
    best_score = -1
    best_vals: tuple[float, ...] | None = None
    best_path: Path | None = None
    for plot in plots:
        toks = _plot_side_tokens(plot.path)
        if not (toks & side_toks):
            continue
        name = plot.path.name.lower()
        for g in plot.groups:
            gk = g.label.lower()
            if gk not in keys and not (g.label.isdigit() and g.label in keys):
                continue
            score = 10 + min(g.n, 20)  # do not let large unrelated n dominate
            if "rplot" in name:
                score += 5
            # Side-labelled residue plots beat <other>/<side>/Rplot.pdf etc.
            if side.name_hit(name):
                score += 100
            if toks & profile.residue_folder_tokens:
                score += 40
            # Path token alone (folder …<side>…) without filename cue: soft match only
            if any(t in toks and t not in name for t in side.tokens):
                score -= 30
            if score > best_score:
                best_score = score
                best_vals = g.values
                best_path = plot.path
    if best_vals is None or best_path is None:
        return None
    return best_vals, best_path


def _table_content_for_panel(
    pn: PanelN,
    vectors: list[GroupVector],
) -> tuple[tuple[float, ...], Path] | None:
    v = resolve_panel_vector(pn, vectors)
    if v is None:
        return None
    return v.values, v.source


def warnings_inconsistent_n_identical_plots(
    panel_ns: list[PanelN],
    vectors: list[GroupVector],
    digitized_plots: list[DigitizedPlot] | None = None,
    *,
    min_score: float = 0.9,
) -> list[WarningItem]:
    """H2b: same point cloud across panels but Legend n differs.

    説明順は常に固定: ①同一点列 ②Legend n 差 ③正の n 注釈。
    パネル表記は字母昇順で左↔右を揃える。
    """
    plots = digitized_plots or []
    # Deduplicate panel entries (Word may repeat figure legend blocks)
    uniq: dict[tuple[str, str], PanelN] = {}
    for pn in panel_ns:
        key = (pn.figure, pn.panel.upper())
        uniq.setdefault(key, pn)
    panels = sorted(uniq.values(), key=lambda p: (p.figure, p.panel.upper(), p.n))

    out: list[WarningItem] = []
    seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()

    for a, b in combinations(panels, 2):
        if figure_num_from_label(a.figure) != figure_num_from_label(b.figure):
            continue
        if a.n == b.n:
            continue
        keys_a, keys_b = _panel_group_keys(a.panel), _panel_group_keys(b.panel)
        # Prefer same group-role pairs (F↔N, G↔O); also allow any if content matches strongly
        same_role = bool(keys_a & keys_b)
        side_a, side_b = _panel_side(a.panel), _panel_side(b.panel)
        if side_a and side_b and side_a == side_b and not same_role:
            continue

        content_a = _digitized_content_for_panel(a, plots) or _table_content_for_panel(a, vectors)
        content_b = _digitized_content_for_panel(b, plots) or _table_content_for_panel(b, vectors)
        if content_a is None or content_b is None:
            continue
        vals_a, src_a = content_a
        vals_b, src_b = content_b
        score = values_match_score(vals_a, vals_b, abs_tol=0.3)
        if score < min_score:
            continue
        if not same_role and score < 0.99:
            continue

        left, right = a, b  # already combinations over sorted panels
        src_left, src_right = src_a, src_b
        dedupe = tuple(
            sorted([(left.figure, left.panel.upper()), (right.figure, right.panel.upper())])
        )
        if dedupe in seen:
            continue
        seen.add(dedupe)

        out.append(
            WarningItem(
                tag=WarningTag.SAMPLE_SIZE,
                title=(
                    f"{left.figure} panels {left.panel}/{right.panel}: "
                    "同一点列なのに Legend n が不一致"
                ),
                location=f"{left.panel} n={left.n} ↔ {right.panel} n={right.n}",
                reason=(
                    f"① 同一点列: 一致度 {score:.2f}"
                    f"（{src_left.name} / {src_right.name}）。"
                    f"② Legend n: {left.panel}={left.n} ≠ {right.panel}={right.n}。"
                    f"③ 正の n は生データ行数（可視ドット数ではない）。"
                    f"{N_AUTHORITY_FOOTER}"
                ),
                sources=[str(src_left), str(src_right)],
                metadata={
                    "pattern_id": "P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS",
                    "panel_a": left.panel,
                    "panel_b": right.panel,
                    "legend_n_a": left.n,
                    "legend_n_b": right.n,
                    "match_score": score,
                    "reason_order": ["identical_series", "legend_n_mismatch", "n_authority"],
                    **N_AUTHORITY_META,
                },
            )
        )
    return out


def warnings_from_n_mismatches(
    items: list[NMismatch],
    *,
    exclusion_mentioned: bool = False,
) -> list[WarningItem]:
    """Emit n mismatch; prefer P-EXCLUSION-UNDECLARED when data_n > legend_n
    and no exclusion criteria were found in Legend/Methods."""
    from pre_peer_checker.engine.count_n import is_count_table

    out: list[WarningItem] = []
    for m in items:
        # Count-domain tables are handled by P-COUNT-N-MISMATCH
        if is_count_table(m.vector.source):
            continue
        data_gt = m.data_n > m.panel_n.n
        if data_gt and not exclusion_mentioned:
            out.append(
                WarningItem(
                    tag=WarningTag.SAMPLE_SIZE,
                    title=(
                        f"{m.panel_n.figure} panel {m.panel_n.panel}: "
                        "除外基準なき n 間引きの疑い"
                    ),
                    location=(
                        f"{m.panel_n.figure} ({m.panel_n.panel}) / "
                        f"{m.vector.source.name}"
                    ),
                    reason=(
                        f"Legend 記載 n={m.panel_n.n} より生データ群 "
                        f"'{m.vector.group_key}' の有効行数 n={m.data_n} が多い一方、"
                        "Methods／Legend に除外基準の明示が見つかりません。"
                        f"{N_AUTHORITY_FOOTER}"
                    ),
                    sources=[str(m.vector.source)],
                    metadata={
                        "pattern_id": "P-EXCLUSION-UNDECLARED",
                        "panel": m.panel_n.panel,
                        "legend_n": m.panel_n.n,
                        "data_n": m.data_n,
                        "exclusion_mentioned": False,
                        **N_AUTHORITY_META,
                    },
                )
            )
            continue
        out.append(
            WarningItem(
                tag=WarningTag.SAMPLE_SIZE,
                title=f"{m.panel_n.figure} panel {m.panel_n.panel}: サンプルサイズの乖離",
                location=f"{m.panel_n.figure} ({m.panel_n.panel}) / {m.vector.source.name}",
                reason=(
                    f"Legend 記載 n={m.panel_n.n} に対し、"
                    f"生データ群 '{m.vector.group_key}' の有効行数は n={m.data_n} です。"
                    + (
                        "（除外基準の記載はあるため、除外未宣言パターンではなく n 不一致として出しています。）"
                        if data_gt and exclusion_mentioned
                        else ""
                    )
                    + f"{N_AUTHORITY_FOOTER}"
                ),
                sources=[str(m.vector.source)],
                metadata={
                    "pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA",
                    "panel": m.panel_n.panel,
                    "legend_n": m.panel_n.n,
                    "data_n": m.data_n,
                    "exclusion_mentioned": bool(exclusion_mentioned),
                    **N_AUTHORITY_META,
                },
            )
        )
    return out


def filename_content_warnings(table_paths: list[Path]) -> list[WarningItem]:
    by_dir: dict[Path, list[Path]] = defaultdict(list)
    for p in table_paths:
        by_dir[p.parent].append(p)

    profile = get_case_profile()
    residue_markers = ("plot_residue", "rplot", *sorted(profile.residue_folder_tokens))
    warnings: list[WarningItem] = []
    seen_dirs: set[Path] = set()
    for folder, files in by_dir.items():
        folder_r = folder.resolve()
        if folder_r in seen_dirs:
            continue
        blob_files = list(files) + list(folder.glob("Rplot*.pdf"))
        names = [f.name.lower() for f in blob_files]
        present = [s.name for s in profile.sides if any(s.name_hit(n) for n in names)]
        folder_l = str(folder).lower().replace(" ", "")
        if len(present) >= 2 and any(m in folder_l for m in residue_markers):
            seen_dirs.add(folder_r)
            warnings.append(
                WarningItem(
                    tag=WarningTag.DATA_SWAP,
                    title="作図残渣フォルダに複数実験系ラベルが混在",
                    location=str(folder),
                    reason=(
                        f"同一ディレクトリに複数の実験系（{' / '.join(present)}）のファイル名が共存しています。"
                        "ファイル名と参照データセットの不一致がないか確認してください。"
                    ),
                    sources=[str(f) for f in blob_files],
                    metadata={"pattern_id": "P-FILENAME-CONTENT-MISMATCH"},
                )
            )
    return warnings
