"""R textClipping 残渣と表の再計算統計の突合."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.data.stats_recalc import StatsResult, analyze_table_file
from pre_peer_checker.engine.case_profile import get_case_profile
from pre_peer_checker.parsers.r_residue import ResidueStats, parse_textclipping
from pre_peer_checker.warnings import WarningItem, WarningTag

_DUNNETT_LINE_RE = re.compile(
    r"(?P<label>\d+)\s*-\s*(?P<ref>\d+)\s*==\s*0\s+"
    r"(?P<est>[-\d.]+)\s+(?P<se>[\d.]+)\s+(?P<t>[-\d.]+)\s+(?P<p>[\d.eE+\-]+)",
)


@dataclass
class DunnettContrast:
    label: str
    ref: str
    estimate: float
    p_value: float


@dataclass
class ResidueParsed:
    stats: ResidueStats
    dunnett: list[DunnettContrast] = field(default_factory=list)


def _extract_plain_text(data: bytes) -> str:
    """Prefer readable console text embedded in Apple textClipping / webarchive blobs."""
    candidates: list[str] = []
    for dec in ("utf-8", "latin-1", "utf-16", "utf-16-le"):
        try:
            candidates.append(data.decode(dec, errors="ignore"))
        except Exception:
            continue
    # Score by presence of R console markers
    def score(t: str) -> int:
        keys = (
            "Welch Two Sample",
            "p-value",
            "mean of x",
            "Dunnett",
            "Estimate",
            "Simultaneous Tests",
        )
        return sum(k in t for k in keys) * 10 + t.count("p-value")

    best = max(candidates, key=score) if candidates else ""
    return best


def parse_residue_rich(path: Path | str) -> ResidueParsed:
    path = Path(path)
    base = parse_textclipping(path)
    text = _extract_plain_text(path.read_bytes())
    # Refresh means / p if richer text available
    if "mean of x" in text.lower() and base.means is None:
        from pre_peer_checker.parsers.r_residue import _MEAN_RE, _P_RE, _T_RE, _WELCH_RE, _DUNNETT_RE

        mm = _MEAN_RE.search(text)
        if mm:
            base.means = (float(mm.group(1)), float(mm.group(2)))
        pm = _P_RE.search(text)
        if pm:
            try:
                base.p_value = float(pm.group(1))
            except ValueError:
                pass
        tm = _T_RE.search(text)
        if tm:
            try:
                base.t_stat = float(tm.group(1))
            except ValueError:
                pass
        if _WELCH_RE.search(text):
            base.kind = "welch_t"
        elif _DUNNETT_RE.search(text):
            base.kind = "dunnett"
        base.raw_excerpt = text[:800]

    dunnett: list[DunnettContrast] = []
    if "Dunnett" in text or base.kind == "dunnett":
        base.kind = "dunnett"
        seen_c: set[tuple[str, str, float, float]] = set()
        for m in _DUNNETT_LINE_RE.finditer(text):
            c = DunnettContrast(
                label=m.group("label"),
                ref=m.group("ref"),
                estimate=float(m.group("est")),
                p_value=float(m.group("p")),
            )
            key = (c.label, c.ref, round(c.estimate, 4), c.p_value)
            if key in seen_c:
                continue
            seen_c.add(key)
            dunnett.append(c)
    return ResidueParsed(stats=base, dunnett=dunnett)


@dataclass
class ResidueTableMatch:
    residue: Path
    table: Path
    mean_error: float | None
    p_error: float | None
    matched: bool
    detail: str


def _welch_mean_error(residue_means: tuple[float, float], result: StatsResult) -> float | None:
    if len(result.groups) < 2:
        return None
    best = None
    from itertools import combinations

    for a, b in combinations(result.groups, 2):
        err = abs(a.mean - residue_means[0]) + abs(b.mean - residue_means[1])
        err2 = abs(a.mean - residue_means[1]) + abs(b.mean - residue_means[0])
        e = min(err, err2)
        if best is None or e < best:
            best = e
    return best


def _welch_p_error(residue_p: float, result: StatsResult) -> float | None:
    welch = [p for p in result.pairwise if p.get("test") == "welch_t"]
    if not welch:
        return None
    return abs(float(welch[0]["p_value"]) - residue_p)


def _dunnett_errors(
    contrasts: list[DunnettContrast],
    result: StatsResult,
    *,
    est_tol: float = 0.5,
) -> tuple[float | None, float | None]:
    by_key = {g.group: g for g in result.groups}
    # also numeric aliases
    for g in result.groups:
        by_key.setdefault(str(g.group), g)
    if not contrasts:
        return None, None
    est_errs: list[float] = []
    p_errs: list[float] = []
    for c in contrasts:
        g = by_key.get(c.label)
        ref = by_key.get(c.ref)
        if g is None or ref is None:
            continue
        est = g.mean - ref.mean
        est_errs.append(abs(est - c.estimate))
        # no exact p from our pairwise welch list for Dunnett; skip p or use rough
        p_errs.append(0.0)  # structural match on estimates is primary
    if not est_errs:
        return None, None
    mean_err = sum(est_errs) / len(est_errs)
    return mean_err, (sum(p_errs) / len(p_errs) if p_errs else None)


def score_residue_against_table(
    residue: ResidueParsed,
    result: StatsResult,
    *,
    mean_tol: float = 0.15,
    p_tol: float = 0.02,
    relative_mean_tol: float = 0.05,
) -> ResidueTableMatch:
    st = residue.stats
    mean_err = None
    p_err = None
    detail_parts: list[str] = []

    if st.kind == "welch_t" and st.means is not None:
        mean_err = _welch_mean_error(st.means, result)
        if st.p_value is not None:
            p_err = _welch_p_error(st.p_value, result)
        detail_parts.append(f"welch means={st.means} p={st.p_value}")
    elif st.kind == "dunnett" and residue.dunnett:
        mean_err, p_err = _dunnett_errors(residue.dunnett, result)
        detail_parts.append(f"dunnett contrasts={len(residue.dunnett)}")
    elif st.means is not None:
        mean_err = _welch_mean_error(st.means, result)
        detail_parts.append(f"means={st.means}")

    matched = False
    if mean_err is not None:
        scale = max(abs(x) for x in (st.means or (1.0,))) or 1.0
        tol = max(mean_tol, relative_mean_tol * scale * 2)
        if st.kind == "dunnett":
            tol = max(0.5, mean_tol)
        matched = mean_err <= tol
        if matched and p_err is not None and st.kind == "welch_t":
            # p must also be close when available
            matched = p_err <= p_tol or (
                st.p_value is not None
                and st.p_value < 1e-6
                and p_err < max(p_tol, st.p_value * 5)
            )

    return ResidueTableMatch(
        residue=st.path,
        table=result.path,
        mean_error=mean_err,
        p_error=p_err,
        matched=matched,
        detail="; ".join(detail_parts),
    )


def sibling_tables(residue_path: Path) -> list[Path]:
    out: list[Path] = []
    for folder in (residue_path.parent, residue_path.parent.parent):
        if not folder.is_dir():
            continue
        for p in folder.iterdir():
            if p.suffix.lower() in {".xlsx", ".xls", ".csv"} and p.is_file():
                out.append(p)
    # unique preserve order
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def match_residue_to_tables(
    residue_path: Path | str,
    table_results: dict[Path, StatsResult] | None = None,
) -> list[ResidueTableMatch]:
    residue_path = Path(residue_path)
    parsed = parse_residue_rich(residue_path)
    if parsed.stats.means is None and not parsed.dunnett:
        return []

    hits: list[ResidueTableMatch] = []
    tables = sibling_tables(residue_path)
    for t in tables:
        if table_results and t.resolve() in table_results:
            result = table_results[t.resolve()]
        else:
            try:
                result = analyze_table_file(t)
            except Exception:
                continue
        hits.append(score_residue_against_table(parsed, result))
    hits.sort(key=lambda h: (not h.matched, h.mean_error if h.mean_error is not None else 1e9))
    return hits


def warnings_from_residue_stats(
    residue_paths: list[Path],
    table_results: dict[Path, StatsResult] | None = None,
    *,
    mismatch_mean_floor: float = 1.0,
) -> list[WarningItem]:
    """Warn when a residue fails to match name-aligned sibling tables."""
    from pre_peer_checker.data.group_vectors import path_experiment_tokens

    out: list[WarningItem] = []
    for rp in residue_paths:
        if not str(rp).endswith("textClipping"):
            continue
        try:
            hits = match_residue_to_tables(rp, table_results)
        except Exception:
            continue
        if not hits:
            continue
        rtoks = path_experiment_tokens(rp) | {
            t
            for t in get_case_profile().side_label_tokens()
            if t in rp.name.lower().replace(" ", "")
        }
        expected = [
            h
            for h in hits
            if h.mean_error is not None
            and (
                (rtoks & path_experiment_tokens(h.table))
                or any(t in h.table.name.lower() for t in rtoks)
            )
        ]
        if not expected:
            continue
        if any(h.matched for h in expected):
            continue
        best = min(expected, key=lambda h: h.mean_error if h.mean_error is not None else 1e9)
        if best.mean_error is None or best.mean_error < mismatch_mean_floor:
            continue
        out.append(
            WarningItem(
                tag=WarningTag.STATS_MISMATCH,
                title=f"{rp.name}: 残渣統計が対応表と不一致",
                location=f"{rp.name} ↔ {best.table.name}",
                reason=(
                    f"ファイル名上対応しそうな表との mean_error={best.mean_error:.4g}"
                    + (f", p_error={best.p_error:.4g}" if best.p_error is not None else "")
                    + f"。{best.detail}。"
                    " textClipping の検定対象データと表が食い違っていないか確認してください。"
                ),
                sources=[str(rp), str(best.table)],
                metadata={
                    "pattern_id": "P-STATS-RECALC-MISMATCH",
                    "mean_error": best.mean_error,
                    "p_error": best.p_error,
                },
            )
        )
    return out


def warnings_residue_plot_table_divergence(
    *,
    residue_best_table: Path | None,
    plot_best_table: Path | None,
    residue_path: Path,
    plot_path: Path,
) -> list[WarningItem]:
    """When stats residue matches table A but Rplot matches conflicting table B."""
    if residue_best_table is None or plot_best_table is None:
        return []
    if residue_best_table.resolve() == plot_best_table.resolve():
        return []
    from pre_peer_checker.data.group_vectors import path_experiment_tokens

    profile = get_case_profile()

    def side(path: Path) -> str | None:
        toks = path_experiment_tokens(path)
        name = path.name.lower()
        hits = [
            s
            for s in profile.sides
            if any(t in name for t in s.all_tokens) or (s.all_tokens & toks)
        ]
        clean = [
            s
            for s in hits
            if not any(t in name for t in profile.other_side_tokens(s.name))
        ]
        return clean[0].name if clean else None

    r_side = side(residue_best_table) or side(residue_path)
    p_side = side(plot_best_table) or side(plot_path)
    if not r_side or not p_side or r_side == p_side:
        return []
    return [
        WarningItem(
            tag=WarningTag.DATA_SWAP,
            title="統計残渣と作図残渣が別データセットに一致",
            location=(
                f"{residue_path.name}↔{residue_best_table.name} / "
                f"{plot_path.name}↔{plot_best_table.name}"
            ),
            reason=(
                "textClipping の検定結果は一方の表と一致するが、"
                "同フォルダの Rplot 点列は別実験系の表と一致します。"
                " ggplot の data 取り違え（統計は正しい DF、作図だけ別 DF）の典型パターンです。"
            ),
            sources=[
                str(residue_path),
                str(residue_best_table),
                str(plot_path),
                str(plot_best_table),
            ],
            metadata={
                "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
                "also_pattern": "P-FILENAME-CONTENT-MISMATCH",
            },
        )
    ]
