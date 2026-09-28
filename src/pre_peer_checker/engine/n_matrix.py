"""Figure × panel の n 対照表（原稿 / 実験データ / 作図 / 統計）。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector, path_experiment_tokens
from pre_peer_checker.engine.case_profile import get_case_profile
from pre_peer_checker.engine.entity_link import (
    EntityLink,
    LinkStatus,
    link_plot_for_panel,
    link_raw_for_panel,
)
from pre_peer_checker.engine.n_and_names import (
    _panel_side,
    figure_num_from_label,
    group_keys_for_panel,
    path_soft_compatible_with_figure,
)
from pre_peer_checker.parsers.legend_struct import PanelN
from pre_peer_checker.parsers.pdf_plot_digitize import DigitizedPlot


@dataclass
class NCell:
    n: int | None = None
    file: str | None = None
    file_display: str | None = None
    detail: str = ""
    link_status: str = ""
    link_tier: str = ""

    def display(self) -> str:
        if self.n is None and not self.file and not self.detail:
            return "—"
        bits: list[str] = []
        if self.n is not None:
            bits.append(f"n={self.n}")
        shown = self.file_display or (Path(self.file).name if self.file else None)
        if shown:
            bits.append(shown)
        if self.detail:
            bits.append(f"({self.detail})")
        return " ".join(bits) if bits else "—"


@dataclass
class NMatrixRow:
    figure: str
    panel: str
    manuscript: NCell
    data: NCell
    plot: NCell
    stats: NCell
    extractor: str = "rules"
    mismatch: bool = False
    group: str = ""
    data_link_status: str = ""
    plot_link_status: str = ""
    input_gap: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["manuscript_display"] = self.manuscript.display()
        d["data_display"] = self.data.display()
        d["plot_display"] = self.plot.display()
        d["stats_display"] = self.stats.display()
        d["panel_label"] = (
            f"{self.panel} [{self.group}]" if self.group else self.panel
        )
        return d

def _rel_display(path: Path | str | None, roots: list[Path] | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    for root in roots or []:
        try:
            return str(p.resolve().relative_to(Path(root).resolve()))
        except (ValueError, OSError):
            continue
    if len(p.parts) >= 2:
        return str(Path(p.parts[-2]) / p.name)
    return p.name


def _cell_from_path(
    *,
    n: int | None,
    path: Path | str | None,
    detail: str,
    roots: list[Path] | None,
    link_status: str = "",
    link_tier: str = "",
) -> NCell:
    if path is None and n is None and not detail:
        return NCell(link_status=link_status, link_tier=link_tier)
    file_s = str(path) if path is not None else None
    return NCell(
        n=n,
        file=file_s,
        file_display=_rel_display(path, roots) if path is not None else None,
        detail=detail,
        link_status=link_status,
        link_tier=link_tier,
    )


def _cell_from_link(link: EntityLink, roots: list[Path] | None) -> NCell:
    if link.status == LinkStatus.LINKED and link.vector is not None:
        return _cell_from_path(
            n=link.vector.n,
            path=link.vector.source,
            detail=link.reason,
            roots=roots,
            link_status=link.status.value,
            link_tier=link.tier.value,
        )
    return NCell(
        detail=link.reason,
        link_status=link.status.value,
        link_tier=link.tier.value,
    )

def _fig_in_blob(blob: str, fnum: str) -> bool:
    return bool(re.search(rf"fig(?:ure)?_?{fnum}", blob))


def _digitized_n_for_panel(
    pn: PanelN,
    plots: list[DigitizedPlot],
    *,
    extra_groups: list[str] | None = None,
) -> tuple[int | None, Path | None, str]:
    keys = group_keys_for_panel(pn, extra_groups)
    fnum = figure_num_from_label(pn.figure)
    profile = get_case_profile()
    side_toks = profile.side_tokens(_panel_side(pn.panel))
    all_side_toks = profile.all_side_tokens()
    best_score = -1
    best_n: int | None = None
    best_path: Path | None = None
    best_label = ""
    for plot in plots:
        if not path_soft_compatible_with_figure(plot.path, fnum):
            continue
        toks = path_experiment_tokens(plot.path)
        side_bonus = 0
        if side_toks:
            if toks & side_toks:
                side_bonus = 8
            elif toks and (all_side_toks & toks):
                continue
        for g in plot.groups:
            gk = g.label.lower()
            group_ok = (not keys) or gk in keys or (g.label.isdigit() and g.label in keys)
            n_ok = g.n == pn.n
            if not group_ok and not n_ok:
                continue
            score = 5 + g.n + side_bonus
            if group_ok:
                score += 10
            if n_ok:
                score += 4
            if "rplot" in plot.path.name.lower():
                score += 5
            if fnum and _fig_in_blob(str(plot.path).lower().replace(" ", ""), fnum):
                score += 6
            if score > best_score:
                best_score = score
                best_n = g.n
                best_path = plot.path
                best_label = g.label
    if best_path is None or best_score < 8:
        return None, None, ""
    detail = f"digitized:{best_label}" if best_label else "digitized"
    return best_n, best_path, detail


def _script_hint_for_panel(
    pn: PanelN,
    table: Path | None,
    script_arts: list[dict] | None,
    *,
    roots: list[Path] | None,
) -> NCell:
    """Find R/Python/.Rhistory that references the table or figure tokens."""
    if not script_arts:
        return NCell(detail="スクリプト未検出")
    fnum = figure_num_from_label(pn.figure)
    name = table.name.lower() if table else ""
    stem = table.stem.lower() if table else ""
    best: tuple[int, str, str] | None = None
    for art in script_arts:
        path = str(art.get("path") or "")
        if not path:
            continue
        blob = str(art).lower()
        score = 0
        detail = ""
        source_kind = str(art.get("source_kind") or "")
        if name and (name in blob or stem in blob):
            score += 20
            detail = "reads table"
        for rd in art.get("reads") or art.get("bindings") or []:
            if isinstance(rd, dict):
                src = str(
                    rd.get("resolved_path")
                    or rd.get("path")
                    or rd.get("source")
                    or rd.get("file")
                    or ""
                ).lower()
            else:
                src = str(rd).lower()
            if name and (name in src or stem in src or Path(src).name == name):
                score += 30
                detail = "reads table"
                if source_kind == "rhistory":
                    detail = "Rhistory→read"
                    score += 5
                elif source_kind == "prism":
                    detail = "Prism→table"
                    score += 5
                elif source_kind == "kaleida":
                    detail = "Kaleida→ref"
                    score += 5
                elif source_kind in {"python", "notebook"}:
                    detail = "Python→read"
                    score += 5
        # Prefer scripts sitting next to the table
        if table is not None and path:
            try:
                if Path(path).resolve().parent == Path(table).resolve().parent:
                    score += 15
                    detail = detail or "同ディレクトリ"
            except OSError:
                pass
        path_l = path.lower().replace(" ", "")
        if fnum and _fig_in_blob(path_l, fnum):
            score += 8
            detail = detail or "figパス"
        if pn.panel.lower() in path_l:
            score += 3
        for sv in art.get("saves") or art.get("plots") or []:
            if isinstance(sv, dict):
                sv_s = str(
                    sv.get("resolved_path") or sv.get("path") or sv.get("file") or sv
                ).lower()
            else:
                sv_s = str(sv).lower()
            if fnum and _fig_in_blob(sv_s, fnum):
                score += 10
                detail = detail or "savefig→fig"
        if score > 0 and (best is None or score > best[0]):
            kind_bit = f" · {source_kind}" if source_kind else ""
            best = (score, path, (detail or "script") + kind_bit)
    if best is None:
        return NCell(detail="スクリプト未紐付け")
    return _cell_from_path(n=None, path=best[1], detail=best[2], roots=roots)


def build_n_matrix(
    panel_ns: list[PanelN],
    vectors: list[GroupVector],
    *,
    digitized_plots: list[DigitizedPlot] | None = None,
    legend_json_by_panel: dict[tuple[str, str], dict] | None = None,
    script_artifacts: list[dict] | None = None,
    manuscript_by_figure: dict[str, str] | None = None,
    case_roots: list[Path] | None = None,
    table_paths: list[Path] | None = None,
    key_alias_map: dict[str, set[str]] | None = None,
) -> list[NMatrixRow]:
    """One row per unique (figure, panel, group) from legend extraction."""
    from pre_peer_checker.engine.entity_link import parse_legend_numeric_hints

    plots = digitized_plots or []
    roots = list(case_roots or [])
    paths = list(table_paths or [])
    ms_by_fig = manuscript_by_figure or {}
    alias_map = key_alias_map
    by_key: dict[tuple[str, str, str], PanelN] = {}
    for pn in panel_ns:
        key = (pn.figure, pn.panel.upper(), getattr(pn, "group", "") or "")
        by_key.setdefault(key, pn)

    rows: list[NMatrixRow] = []
    for (_fig, _panel, _grp), pn in sorted(
        by_key.items(),
        key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]),
    ):
        extractor = "rules"
        evidence = pn.context
        group = getattr(pn, "group", "") or ""
        extra_groups: list[str] = []
        if legend_json_by_panel:
            meta = legend_json_by_panel.get((pn.figure, pn.panel.upper()))
            if meta:
                extractor = str(meta.get("extractor") or extractor)
                evidence = str(meta.get("evidence_span") or evidence)
                for g in meta.get("groups") or []:
                    extra_groups.append(str(g))
                if not group and extra_groups:
                    group = extra_groups[0]

        detail_ms = evidence[:80] if evidence else extractor
        if group and group not in detail_ms:
            detail_ms = f"group={group} · {detail_ms}"
        ms_path = ms_by_fig.get(pn.figure) or ms_by_fig.get(
            pn.figure.replace("Figure", "Fig.")
        )
        manuscript = _cell_from_path(
            n=pn.n, path=ms_path, detail=detail_ms, roots=roots
        )

        fig_blob = ms_by_fig.get(pn.figure) or ""
        hints = parse_legend_numeric_hints(pn.context, evidence, fig_blob)

        # Plot first (often denser fingerprints), then raw anchored to plot
        plot_link = link_plot_for_panel(
            pn,
            vectors,
            extra_groups=extra_groups,
            table_paths=paths,
            script_artifacts=script_artifacts,
            legend_hints=hints,
            key_alias_map=alias_map,
        )
        raw_link = link_raw_for_panel(
            pn,
            vectors,
            plot_anchor=plot_link.vector if plot_link.status == LinkStatus.LINKED else None,
            extra_groups=extra_groups,
            table_paths=paths,
            script_artifacts=script_artifacts,
            legend_hints=hints,
            key_alias_map=alias_map,
        )
        # If raw linked via fingerprint and plot still soft/missing, re-link plot to raw
        if (
            raw_link.status == LinkStatus.LINKED
            and plot_link.tier.value in {"soft", "none"}
            and raw_link.vector is not None
        ):
            plot_relink = link_plot_for_panel(
                pn,
                vectors,
                raw_anchor=raw_link.vector,
                extra_groups=extra_groups,
                table_paths=paths,
                script_artifacts=script_artifacts,
                legend_hints=hints,
                key_alias_map=alias_map,
            )
            if plot_relink.status == LinkStatus.LINKED and plot_relink.tier.value in {
                "tier1",
                "tier2",
            }:
                plot_link = plot_relink

        dig_n, dig_path, dig_detail = _digitized_n_for_panel(
            pn, plots, extra_groups=extra_groups
        )

        data = _cell_from_link(raw_link, roots)

        if plot_link.status == LinkStatus.LINKED and plot_link.vector is not None:
            plot = _cell_from_link(plot_link, roots)
        elif dig_n is not None and dig_path is not None:
            plot = _cell_from_path(
                n=dig_n,
                path=dig_path,
                detail=dig_detail,
                roots=roots,
                link_status=LinkStatus.LINKED.value,
                link_tier="digitized",
            )
        else:
            plot = _cell_from_link(plot_link, roots)

        stats_table = (
            raw_link.vector.source
            if raw_link.status == LinkStatus.LINKED and raw_link.vector
            else (
                plot_link.vector.source
                if plot_link.status == LinkStatus.LINKED and plot_link.vector
                else None
            )
        )
        stats = _script_hint_for_panel(pn, stats_table, script_artifacts, roots=roots)
        if stats.file is None and stats_table is not None:
            n_stats = None
            if raw_link.status == LinkStatus.LINKED and raw_link.vector:
                n_stats = raw_link.vector.n
            elif plot_link.status == LinkStatus.LINKED and plot_link.vector:
                n_stats = plot_link.vector.n
            stats = _cell_from_path(
                n=n_stats,
                path=stats_table,
                detail="表から再集計（スクリプト未検出）",
                roots=roots,
            )
        elif stats.file is not None and stats.n is None:
            n_stats = None
            if raw_link.status == LinkStatus.LINKED and raw_link.vector:
                n_stats = raw_link.vector.n
            elif plot_link.status == LinkStatus.LINKED and plot_link.vector:
                n_stats = plot_link.vector.n
            stats = NCell(
                n=n_stats,
                file=stats.file,
                file_display=stats.file_display,
                detail=stats.detail or "script",
            )

        input_gap = raw_link.status == LinkStatus.DATA_MISSING
        # n 不一致は「紐付いたセル同士」だけ比較（未投入は不一致扱いにしない）
        linked_ns = [manuscript.n] if manuscript.n is not None else []
        if data.link_status == LinkStatus.LINKED.value and data.n is not None:
            linked_ns.append(data.n)
        if plot.link_status == LinkStatus.LINKED.value and plot.n is not None:
            linked_ns.append(plot.n)
        if stats.n is not None and stats_table is not None and not input_gap:
            linked_ns.append(stats.n)
        mismatch = len(set(linked_ns)) > 1

        rows.append(
            NMatrixRow(
                figure=pn.figure,
                panel=pn.panel.upper(),
                manuscript=manuscript,
                data=data,
                plot=plot,
                stats=stats,
                extractor=extractor,
                mismatch=mismatch,
                group=group,
                data_link_status=raw_link.status.value,
                plot_link_status=(
                    plot.link_status or plot_link.status.value
                ),
                input_gap=input_gap,
            )
        )
    return rows

def n_matrix_to_artifact(rows: list[NMatrixRow]) -> list[dict]:
    return [r.to_dict() for r in rows]


def _fig_match_key(label: str | None) -> str | None:
    if not label:
        return None
    m = re.search(r"(S?)(\d+)", label, re.I)
    if not m:
        return None
    supp = bool(m.group(1)) or "supp" in label.lower()
    return ("S" if supp else "") + (m.group(2).lstrip("0") or "0")


def attach_fig_pdf_counts(
    rows: list[dict],
    figure_panel_arts: list[dict],
    *,
    roots: list[Path] | None = None,
) -> None:
    """Add a reference-only ``fig_pdf`` cell (visible dots on the publication Figure PDF).

    Overlapping jitter dots make this undercount the real n, so it never feeds ``mismatch``.
    """
    by_key: dict[tuple[str, str], dict] = {}
    for art in figure_panel_arts or []:
        if not isinstance(art, dict):
            continue
        fkey = _fig_match_key(art.get("figure_id") or art.get("figure"))
        if not fkey:
            continue
        for p in art.get("panels") or []:
            panel = str(p.get("panel") or "").upper()
            if not panel:
                continue
            by_key.setdefault(
                (fkey, panel),
                {
                    "group_ns": [int(n) for n in p.get("group_ns") or []],
                    "n_markers": int(p.get("n_markers") or 0),
                    "file": str(art.get("path") or "") or None,
                    "file_display": _rel_display(art.get("path"), roots),
                    "page": art.get("page"),
                },
            )
    for r in rows:
        fkey = _fig_match_key(str(r.get("figure") or ""))
        panel = str(r.get("panel") or "").upper()
        cell = by_key.get((fkey, panel)) if fkey else None
        r["fig_pdf"] = cell
        if cell and cell["group_ns"]:
            r["fig_pdf_display"] = " / ".join(str(n) for n in cell["group_ns"])
        else:
            r["fig_pdf_display"] = "—"
