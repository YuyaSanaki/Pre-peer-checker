"""数字化プロットと表群ベクトルの突合（H1 / H4 経路）。

正しい xlsx 同士が一致しなくても、Rplot*.pdf の点列が「ファイル名が示す
実験系」以外の表に一致すれば取り違えとして警告する。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector, path_experiment_tokens
from pre_peer_checker.engine.case_profile import get_case_profile
from pre_peer_checker.parsers.pdf_plot_digitize import DigitizedPlot
from pre_peer_checker.warnings import WarningItem, WarningTag


def values_match_score(
    a: tuple[float, ...],
    b: tuple[float, ...],
    *,
    abs_tol: float = 0.25,
) -> float:
    """Greedy nearest-neighbor coverage in [0, 1]."""
    if not a or not b:
        return 0.0
    used: set[int] = set()
    hits = 0
    for x in a:
        best_i: int | None = None
        best_d = abs_tol + 1.0
        for i, y in enumerate(b):
            if i in used:
                continue
            d = abs(x - y)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is not None and best_d <= abs_tol:
            used.add(best_i)
            hits += 1
    return hits / max(len(a), len(b))


@dataclass
class PlotTableHit:
    plot: DigitizedPlot
    table: Path
    group_scores: dict[str, float]
    mean_score: float
    n_groups_matched: int


def score_plot_against_table(
    plot: DigitizedPlot,
    vectors: list[GroupVector],
    *,
    abs_tol: float = 0.25,
    min_group_score: float = 0.85,
) -> PlotTableHit | None:
    """Best mean coverage of digitized groups against one table's group vectors."""
    if not vectors:
        return None
    by_key = {str(v.group_key): v for v in vectors}

    scores: dict[str, float] = {}
    for g in plot.groups:
        best = 0.0
        if g.label in by_key:
            best = values_match_score(g.values, by_key[g.label].values, abs_tol=abs_tol)
        for v in vectors:
            best = max(best, values_match_score(g.values, v.values, abs_tol=abs_tol))
        scores[g.label] = best

    if not scores:
        return None
    matched = sum(1 for s in scores.values() if s >= min_group_score)
    mean_score = sum(scores.values()) / len(scores)
    return PlotTableHit(
        plot=plot,
        table=vectors[0].source,
        group_scores=scores,
        mean_score=mean_score,
        n_groups_matched=matched,
    )


def best_table_for_plot(
    plot: DigitizedPlot,
    table_vectors: dict[Path, list[GroupVector]],
    *,
    abs_tol: float = 0.25,
) -> list[PlotTableHit]:
    hits: list[PlotTableHit] = []
    for _path, vecs in table_vectors.items():
        hit = score_plot_against_table(plot, vecs, abs_tol=abs_tol)
        if hit is not None:
            hits.append(hit)
    hits.sort(key=lambda h: (h.n_groups_matched, h.mean_score), reverse=True)
    return hits


def _name_tokens(path: Path) -> set[str]:
    return path_experiment_tokens(path) | {
        t
        for t in get_case_profile().all_side_tokens()
        if t in path.stem.lower().replace("_", "").replace("-", "").replace(" ", "")
    }


def _conflict_tokens(a: set[str], b: set[str]) -> bool:
    """True if experiment-side tokens disagree (side A vs side B of the case profile)."""
    profile = get_case_profile()
    for side in profile.sides:
        left, right = side.all_tokens, profile.other_side_tokens(side.name)
        if (a & left) and (b & right) and not (a & right) and not (b & left):
            return True
    return False


def warnings_from_plot_table_mismatch(
    plot: DigitizedPlot,
    hits: list[PlotTableHit],
    *,
    min_mean: float = 0.9,
    min_groups: int = 2,
) -> list[WarningItem]:
    """Emit warnings when a plot matches a table whose experiment tokens conflict with the filename."""
    if not hits:
        return []
    best = hits[0]
    if best.mean_score < min_mean or best.n_groups_matched < min_groups:
        return []

    plot_tok = _name_tokens(plot.path)
    best_tok = _name_tokens(best.table)
    if not plot_tok or not best_tok or not _conflict_tokens(plot_tok, best_tok):
        return []

    same_side = [
        h
        for h in hits
        if (_name_tokens(h.table) & plot_tok)
        and not _conflict_tokens(_name_tokens(h.table), plot_tok)
    ]
    expected = same_side[0] if same_side else None
    if expected is not None and expected.mean_score >= best.mean_score - 0.05:
        # Filename-aligned table matches about as well — not a clear swap.
        return []

    expected_note = ""
    if expected is not None:
        expected_note = (
            f" ファイル名側の表 {expected.table.name} との一致度は "
            f"{expected.mean_score:.2f} です。"
        )

    return [
        WarningItem(
            tag=WarningTag.DATA_SWAP,
            title=f"{plot.path.name}: プロット点列が別実験系の表と一致",
            location=f"{plot.path.name} ↔ {best.table.name}",
            reason=(
                f"PDF 数字化群の一致度 mean={best.mean_score:.2f} "
                f"(matched_groups={best.n_groups_matched}/{len(plot.groups)})。"
                f"ファイル名トークン {sorted(plot_tok)} に対し、"
                f"最良一致は {best.table.name}（{sorted(best_tok)}）。"
                f"{expected_note}"
                " ggplot の data 引数取り違えの可能性があります。"
            ),
            sources=[str(plot.path), str(best.table)]
            + ([str(expected.table)] if expected is not None else []),
            metadata={
                "pattern_id": "P-FILENAME-CONTENT-MISMATCH",
                "mean_score": best.mean_score,
                "group_scores": best.group_scores,
                "also_pattern": "P-DATA-SWAP-CROSS-CONDITION",
            },
        )
    ]


def warnings_from_cross_plot_identity(
    plots: list[DigitizedPlot],
    *,
    min_shared_groups: int = 2,
    min_score: float = 0.95,
) -> list[WarningItem]:
    """Flag when two distinctly labeled plot PDFs share nearly identical point groups."""
    out: list[WarningItem] = []
    for i, a in enumerate(plots):
        for b in plots[i + 1 :]:
            if not _conflict_tokens(_name_tokens(a.path), _name_tokens(b.path)):
                continue
            shared = 0
            scores: list[float] = []
            for ga in a.groups:
                best = 0.0
                for gb in b.groups:
                    best = max(best, values_match_score(ga.values, gb.values))
                scores.append(best)
                if best >= min_score:
                    shared += 1
            if shared >= min_shared_groups:
                out.append(
                    WarningItem(
                        tag=WarningTag.DATA_SWAP,
                        title="別実験系ラベルの Rplot 間で点列が一致",
                        location=f"{a.path.name} ↔ {b.path.name}",
                        reason=(
                            f"共有群 {shared}、スコア {scores}。"
                            "別条件のはずの作図残渣が同一データ由来の可能性があります。"
                        ),
                        sources=[str(a.path), str(b.path)],
                        metadata={
                            "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
                            "shared_groups": shared,
                            "scores": scores,
                        },
                    )
                )
    return out
