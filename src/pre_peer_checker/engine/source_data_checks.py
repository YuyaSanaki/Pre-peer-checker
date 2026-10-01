"""Checks on journal Source Data workbooks (titled blocks per figure panel).

* P-SOURCE-DATA-CROSS-FIGURE-REUSE — identical measurement columns under different figures
* P-SOURCE-DATA-SUMMARY-MISMATCH   — stated Mean/SD/SEM vs recomputed from the rows
* P-SOURCE-DATA-POPULATION-SD      — "SD" rows equal STDEVP (divide by n)
* P-SOURCE-DATA-LEGEND-N           — block row count vs legend n of the referenced panel
* P-SOURCE-DATA-PANEL-REF-DUP      — two conditions labelled with the same image panel
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from pre_peer_checker.data.source_data_blocks import (
    FigureRef,
    SourceColumn,
    SourceDataBlock,
    content_signature,
    match_group,
)
from pre_peer_checker.parsers.legend_struct import PanelN
from pre_peer_checker.warnings import WarningItem, WarningTag


def _decimals(x: float) -> int | None:
    s = repr(float(x))
    if "e" in s or "E" in s:
        return None
    frac = s.split(".")[1] if "." in s else ""
    frac = frac.rstrip("0")
    return len(frac) if len(frac) <= 6 else None


def _close(stated: float, x: float) -> bool:
    """Typed summaries are rounded; formula results carry full precision."""
    if not math.isfinite(stated) or not math.isfinite(x):
        return False
    d = _decimals(stated)
    if d is not None:
        return abs(stated - x) <= 0.5 * 10 ** (-d) + 1e-12
    return abs(stated - x) <= 1e-6 * max(abs(stated), abs(x)) + 1e-12


def _mean(v: list[float]) -> float:
    return sum(v) / len(v)


def _sd(v: list[float], ddof: int) -> float:
    if len(v) - ddof <= 0:
        return float("nan")
    m = _mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - ddof))


def _fmt(x: float) -> str:
    return f"{x:.4g}"


# --- cross-figure reuse -------------------------------------------------------

_REF_STRIP_RE = re.compile(r"\(?\s*(?:extended\s+data\s+)?fig(?:ure)?s?\.?\s*S?\d+[a-z]?\s*\)?", re.IGNORECASE)


def _condition_key(title: str) -> str:
    t = _REF_STRIP_RE.sub(" ", title.lower())
    return re.sub(r"[\s,;:/()\-–]+", " ", t).strip()


def _column_signature(col: SourceColumn) -> tuple[float, ...] | None:
    vals = col.numeric()
    if len(vals) < 5 or len({round(v, 9) for v in vals}) < 3:
        return None
    return tuple(round(v, 9) for v in vals)


def _fig_key(b: SourceDataBlock) -> str:
    return b.figure.label() if b.figure is not None else f"{b.path.name}:{b.sheet}"


def warnings_from_source_data_reuse(
    blocks: list[SourceDataBlock],
    *,
    disclosure: str = "none",
) -> list[WarningItem]:
    grouped: dict[tuple[str, str, bool], list[tuple[SourceDataBlock, SourceDataBlock, list[tuple[str, str]], bool]]] = defaultdict(list)
    for a, b in combinations(blocks, 2):
        if a is b or (a.path == b.path and a.sheet == b.sheet and a.header_cell == b.header_cell):
            continue
        same_panel = _fig_key(a) == _fig_key(b) and set(a.panel_refs) == set(b.panel_refs)
        if same_panel:
            continue
        pairs: list[tuple[str, str]] = []
        ordered = True
        for ca in a.columns:
            sa = _column_signature(ca)
            if sa is None:
                continue
            for cb in b.columns:
                sb = _column_signature(cb)
                if sb is None or len(sa) != len(sb):
                    continue
                if sa == sb:
                    pairs.append((ca.header, cb.header))
                elif sorted(sa) == sorted(sb):
                    pairs.append((ca.header, cb.header))
                    ordered = False
        if not pairs:
            continue
        same_condition = _condition_key(a.title) == _condition_key(b.title)
        fa, fb = sorted((_fig_key(a), _fig_key(b)))
        grouped[(fa, fb, same_condition)].append((a, b, pairs, ordered))

    out: list[WarningItem] = []
    for (fa, fb, same_condition), items in grouped.items():
        if same_condition and disclosure == "explicit":
            continue
        lines = []
        sources: list[str] = []
        n_cols = 0
        for a, b, pairs, ordered in items:
            n_cols += len(pairs)
            order_note = "行順まで一致" if ordered else "並べ替えると一致"
            cols = "、".join(f"「{x}」↔「{y}」" for x, y in pairs)
            lines.append(
                f"{a.location}「{a.label}」 ↔ {b.location}「{b.label}」"
                f"（n={a.n}、{order_note}: {cols}）"
            )
            for s in (str(a.path), str(b.path)):
                if s not in sources:
                    sources.append(s)
        if same_condition:
            tag = WarningTag.CONTROL_SHARE
            title = f"Source Data: 同じ測定値が別 Figure に再掲（{fa} ↔ {fb}）"
            tail = (
                "同一実験の再掲であれば、Legend か Methods に再利用の旨を明記してください。"
                "独立実験として示しているなら、取り違えの可能性があります。"
            )
            if disclosure == "weak":
                tail += "（本文に共有・再利用を示唆する記述がありますが、該当 Figure の明示は確認できません）"
        else:
            tag = WarningTag.DATA_SWAP
            title = f"Source Data: 条件の異なるブロックで測定値が完全一致（{fa} ↔ {fb}）"
            tail = "別条件のはずの測定値が一致しています。ブロックの貼り間違いが無いか確認してください。"
        out.append(
            WarningItem(
                tag=tag,
                title=title,
                location=f"{fa} ↔ {fb}",
                reason="；".join(lines) + "。" + tail,
                sources=sources,
                metadata={
                    "pattern_id": "P-SOURCE-DATA-CROSS-FIGURE-REUSE",
                    "same_condition": same_condition,
                    "n_blocks": len(items),
                    "n_columns": n_cols,
                },
            )
        )
    return out


# --- stated summary rows --------------------------------------------------------


@dataclass
class _SummaryFinding:
    block: SourceDataBlock
    column: SourceColumn
    population_sd: bool = False
    excluded_rows: tuple[int, ...] = ()
    sem_denominator: str | None = None
    unexplained: tuple[str, ...] = ()


def _subsets(vals: list[float]) -> list[tuple[tuple[int, ...], list[float]]]:
    out = [((), vals)]
    if len(vals) >= 4:
        for i in range(len(vals)):
            out.append(((i,), vals[:i] + vals[i + 1 :]))
    return out


def _check_column(block: SourceDataBlock, col: SourceColumn) -> _SummaryFinding | None:
    vals = col.numeric()
    s = col.summary
    if len(vals) < 3 or not any(k in s for k in ("mean", "sd", "sem")):
        return None
    f = _SummaryFinding(block=block, column=col)
    unexplained: list[str] = []

    use = vals
    if "mean" in s and not _close(s["mean"], _mean(vals)):
        hits = [(idx, sub) for idx, sub in _subsets(vals)[1:] if _close(s["mean"], _mean(sub))]
        if len(hits) == 1:
            f.excluded_rows = hits[0][0]
            use = hits[0][1]
        else:
            unexplained.append(f"Mean 記載 {_fmt(s['mean'])} / 再計算 {_fmt(_mean(vals))}")

    sd_used: float | None = None
    if "sd" in s:
        sd1, sd0 = _sd(use, 1), _sd(use, 0)
        if _close(s["sd"], sd1):
            sd_used = sd1
        elif _close(s["sd"], sd0):
            f.population_sd = True
            sd_used = sd0
        else:
            unexplained.append(f"SD 記載 {_fmt(s['sd'])} / 再計算 {_fmt(sd1)}")

    if "sem" in s:
        m = len(use)
        sd_ref = s.get("sd", sd_used if sd_used is not None else _sd(use, 1))
        cands = {
            "ok": [_sd(use, 1) / math.sqrt(m), _sd(use, 0) / math.sqrt(m), sd_ref / math.sqrt(m)],
            "n-1": [sd_ref / math.sqrt(m - 1), _sd(use, 1) / math.sqrt(m - 1)],
        }
        if any(_close(s["sem"], x) for x in cands["ok"]):
            pass
        elif any(_close(s["sem"], x) for x in cands["n-1"]):
            f.sem_denominator = "√(n−1)"
        else:
            unexplained.append(f"SEM 記載 {_fmt(s['sem'])} / 再計算 {_fmt(_sd(use, 1) / math.sqrt(m))}")

    f.unexplained = tuple(unexplained)
    if f.population_sd or f.excluded_rows or f.sem_denominator or f.unexplained:
        return f
    return None


def warnings_from_source_data_summaries(blocks: list[SourceDataBlock]) -> list[WarningItem]:
    findings = [
        f
        for b in blocks
        for c in b.columns
        if (f := _check_column(b, c)) is not None
    ]
    out: list[WarningItem] = []

    pop = [f for f in findings if f.population_sd]
    n_sd_cols = sum(1 for b in blocks for c in b.columns if "sd" in c.summary)
    if pop:
        listed = "；".join(
            f"{f.block.label}「{f.column.header}」(n={len(f.column.numeric())})" for f in pop[:8]
        )
        more = f" ほか {len(pop) - 8} 列" if len(pop) > 8 else ""
        out.append(
            WarningItem(
                tag=WarningTag.STAT_METHOD,
                title="Source Data の SD が母標準偏差（STDEVP・n で割る）で計算されている",
                location=f"{len(pop)}/{n_sd_cols} 列",
                reason=(
                    f"記載 SD は標本 SD（n−1 で割る STDEV）ではなく、n で割る母標準偏差と一致します: "
                    f"{listed}{more}。標本の s.d. は √(n/(n−1)) 倍大きくなります。"
                    "Legend の「Mean ± s.d.」と図のエラーバーがどちらで描かれているか確認してください。"
                ),
                sources=sorted({str(f.block.path) for f in pop}),
                metadata={"pattern_id": "P-SOURCE-DATA-POPULATION-SD", "n_columns": len(pop)},
            )
        )

    by_block: dict[int, list[_SummaryFinding]] = defaultdict(list)
    for f in findings:
        if f.excluded_rows or f.sem_denominator or f.unexplained:
            by_block[id(f.block)].append(f)
    for fs in by_block.values():
        b = fs[0].block
        parts: list[str] = []
        excluded = False
        for f in fs:
            n = len(f.column.numeric())
            bits: list[str] = []
            if f.excluded_rows:
                excluded = True
                rows = ", ".join(str(i + 1) for i in f.excluded_rows)
                bits.append(
                    f"Mean/SD/SEM は {n} 行中 {rows} 行目を除いた {n - len(f.excluded_rows)} 件の値と一致"
                )
            if f.sem_denominator:
                bits.append(f"SEM が SD/{f.sem_denominator} と一致（n={n} なら SD/√{n}）")
            bits.extend(f.unexplained)
            parts.append(f"「{f.column.header}」: " + "、".join(bits))
        title = (
            f"Source Data の集計行が一部の行だけで計算されている: {b.label}"
            if excluded
            else f"Source Data の記載 Mean/SD/SEM が生データから再現できない: {b.label}"
        )
        out.append(
            WarningItem(
                tag=WarningTag.STATS_MISMATCH,
                title=title,
                location=b.location,
                reason=(
                    "；".join(parts)
                    + "。Excel の集計式の参照範囲と、図に使われた値・n を確認してください。"
                ),
                sources=[str(b.path)],
                metadata={
                    "pattern_id": "P-SOURCE-DATA-SUMMARY-MISMATCH",
                    "n_rows": b.n,
                    "excluded_rows": sorted({i + 1 for f in fs for i in f.excluded_rows}),
                },
            )
        )
    return out


# --- legend n and panel references --------------------------------------------


def _pn_figure(pn: PanelN) -> FigureRef | None:
    m = re.search(r"(S?\d+)", pn.figure, re.IGNORECASE)
    if not m:
        return None
    low = pn.figure.lower()
    extended = "extended" in low or low.startswith("ed ") or "supplement" in low
    return FigureRef(number=m.group(1).upper(), panel=(pn.panel or "").lower(), extended=extended)


def _legend_index(panel_ns: list[PanelN]) -> dict[tuple[str, bool, str], dict[str, PanelN]]:
    idx: dict[tuple[str, bool, str], dict[str, PanelN]] = defaultdict(dict)
    for pn in panel_ns:
        if pn.n_max is not None:
            continue
        ref = _pn_figure(pn)
        if ref is None or not ref.panel:
            continue
        group = (pn.group or "").strip().lower()
        idx[(ref.number, ref.extended, ref.panel)].setdefault(group, pn)
    return idx


def _image_ref(b: SourceDataBlock) -> str | None:
    """Single image-panel letter named in the block title, same figure number."""
    if b.figure is None:
        return None
    letters = {
        r.panel
        for r in b.panel_refs
        if r.panel and r.number == b.figure.number and r.extended == b.figure.extended
    }
    return next(iter(letters)) if len(letters) == 1 else None


def match_source_block_for_panel(
    pn: PanelN,
    blocks: list[SourceDataBlock],
) -> tuple[SourceDataBlock | None, bool]:
    """(block for this legend panel/group, whether Source Data covers the figure)."""
    ref = _pn_figure(pn)
    if ref is None:
        return None, False
    fig_blocks = [
        b
        for b in blocks
        if b.figure is not None
        and b.figure.number == ref.number
        and b.figure.extended == ref.extended
    ]
    if not fig_blocks:
        return None, False
    panel_blocks = [b for b in fig_blocks if ref.panel and ref.panel in b.all_panels] or [
        b for b in fig_blocks if not b.all_panels
    ]
    if not panel_blocks:
        return None, True
    group = (pn.group or "").strip().lower()
    cands = [b for b in panel_blocks if _image_ref(b) == group] if group else panel_blocks
    if group and not cands:
        cands = [b for b in panel_blocks if match_group(b, pn.group)]
    cands = _distinct(cands)
    if len(cands) > 1:
        by_group_n = [
            b for b in cands if b.n_comparable and any(len(v) == pn.n for _, v in b.groups)
        ]
        cands = _distinct(by_group_n or [b for b in cands if b.n == pn.n])
    return (cands[0] if len(cands) == 1 else None), True


def _distinct(blocks: list[SourceDataBlock]) -> list[SourceDataBlock]:
    """Drop copies of the same table (a file duplicated under two names)."""
    seen: set[tuple] = set()
    out: list[SourceDataBlock] = []
    for b in blocks:
        sig = content_signature(b)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(b)
    return out


def warnings_from_source_data_panels(
    blocks: list[SourceDataBlock],
    panel_ns: list[PanelN],
) -> list[WarningItem]:
    legend = _legend_index(panel_ns)
    out: list[WarningItem] = []

    by_panel: dict[tuple[str, bool, str], list[SourceDataBlock]] = defaultdict(list)
    for b in _distinct(blocks):
        if b.figure is None:
            continue
        for p in b.all_panels:
            by_panel[(b.figure.number, b.figure.extended, p)].append(b)

    for key, bs in by_panel.items():
        groups = legend.get(key, {})
        refs: dict[str, list[SourceDataBlock]] = defaultdict(list)
        for b in bs:
            r = _image_ref(b)
            if r:
                refs[r].append(b)
        referenced = set(refs)
        mislabeled: set[int] = set()

        for letter, dup in refs.items():
            if len({_condition_key(b.title) for b in dup}) < 2:
                continue
            hints: list[str] = []
            for b in dup:
                pn = groups.get(letter)
                if pn is not None and pn.n == b.n:
                    continue
                cands = [
                    g for g, p in groups.items() if g and g not in referenced and p.n == b.n
                ]
                if len(cands) == 1:
                    mislabeled.add(id(b))
                    hints.append(
                        f"「{b.title}」(n={b.n}) は Legend の ({cands[0]}) n={groups[cands[0]].n} に相当"
                    )
            fig = dup[0].figure.label() if dup[0].figure else ""
            names = "、".join(f"{b.location}「{b.title}」" for b in dup)
            out.append(
                WarningItem(
                    tag=WarningTag.REF_INCONSISTENCY,
                    title=f"Source Data: {fig} の異なる条件が同じパネル (Fig. {key[0]}{letter}) を参照",
                    location=f"{dup[0].path.name} / {fig}",
                    reason=(
                        f"{names} がいずれも Fig. {key[0]}{letter} を指しています。"
                        + ("；".join(hints) + "。" if hints else "")
                        + "ブロック見出しのパネル記号を確認してください。"
                    ),
                    sources=sorted({str(b.path) for b in dup}),
                    metadata={"pattern_id": "P-SOURCE-DATA-PANEL-REF-DUP", "panel": letter},
                )
            )

        for b in bs:
            if id(b) in mislabeled:
                continue
            letter = _image_ref(b)
            pn: PanelN | None = None
            if letter is not None:
                pn = groups.get(letter)
            elif len(bs) == 1 and len(groups) == 1:
                pn = next(iter(groups.values()))
            if pn is None:
                continue
            group_name = "" if letter is not None else (pn.group or "")
            data_n = b.legend_n_conflict(pn.n, group_name)
            if data_n is None:
                continue
            fig = b.figure.label() if b.figure else pn.figure
            group_txt = f" ({pn.group})" if pn.group else ""
            bgroups = b.groups
            per_group = (
                "（群別: " + "、".join(f"{g} n={len(v)}" for g, v in bgroups[:8]) + "）"
                if len(bgroups) > 1
                else ""
            )
            out.append(
                WarningItem(
                    tag=WarningTag.SAMPLE_SIZE,
                    title=f"{fig}{group_txt}: Legend の n と Source Data の行数が不一致",
                    location=f"{b.location}「{b.title}」",
                    reason=(
                        f"Legend は n={pn.n}（{pn.context.strip()[:80]}）ですが、"
                        f"Source Data の該当ブロックは {data_n} 行です{per_group}。"
                        "正の n は生データ群の有効行数です。除外した個体があれば Legend に明記してください。"
                    ),
                    sources=[str(b.path)],
                    metadata={
                        "pattern_id": "P-SOURCE-DATA-LEGEND-N",
                        "legend_n": pn.n,
                        "data_n": data_n,
                        "n_authority": "raw_data_nrows",
                    },
                )
            )
    return out
