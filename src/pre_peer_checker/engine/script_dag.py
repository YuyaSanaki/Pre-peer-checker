"""スクリプト DAG からデータ取り違え Warning を生成."""

from __future__ import annotations

import re
from pathlib import Path

from pre_peer_checker.engine.case_profile import get_case_profile
from pre_peer_checker.parsers.python_ast import ScriptDAG
from pre_peer_checker.parsers.r_treesitter import RScriptDAG
from pre_peer_checker.warnings import WarningItem, WarningTag

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")

# Tokens that are too generic for conflict detection
_STOP = frozenset(
    {
        "data",
        "df",
        "plot",
        "fig",
        "figure",
        "graph",
        "csv",
        "xlsx",
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "tif",
        "tiff",
        "rplot",
        "ggsave",
        "read",
        "table",
        "file",
        "path",
        "group",
        "size",
        "value",
        "aes",
        "geom",
        "box",
        "point",
        "the",
        "and",
        "for",
        "with",
    }
)


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    out: set[str] = set()
    for m in _TOKEN_RE.finditer(text):
        t = m.group(0).lower()
        if len(t) < 3 or t in _STOP:
            continue
        out.add(t)
        # also split CamelCase-ish glued tokens already lowercased
    return out


_GENERIC_ARM_TOKENS = frozenset({"ctrl", "control", "wt", "mut", "ko"})


def _experiment_tokens(text: str | None) -> set[str]:
    """Prefer distinctive experiment labels (profile side labels, wt, ctrl, …)."""
    toks = _tokens(text)
    distinctive = _GENERIC_ARM_TOKENS | get_case_profile().side_label_tokens()
    priority = {t for t in toks if t in distinctive}
    return priority or toks


def _has_side_label(*token_sets: set[str]) -> bool:
    sides = get_case_profile().side_label_tokens()
    return any(ts & sides for ts in token_sets)


def warnings_from_r_dag(path: Path | str, dag: RScriptDAG) -> list[WarningItem]:
    path = Path(path)
    warnings: list[WarningItem] = []

    # Same data expression used in multiple ggplot calls
    by_data: dict[str, list[int]] = {}
    for p in dag.plots:
        key = (p.data_expr or p.piped_from or p.arguments or "").strip()
        if not key:
            continue
        by_data.setdefault(key, []).append(p.lineno)
    for args, lines in by_data.items():
        uniq = sorted(set(lines))
        if len(uniq) >= 2:
            warnings.append(
                WarningItem(
                    tag=WarningTag.DATA_SWAP,
                    title="同一 data 引数の複数 ggplot",
                    location=f"{path.name} lines {uniq}",
                    reason=f"data/arguments={args[:120]}",
                    sources=[str(path)],
                    metadata={
                        "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
                        "backend": dag.backend,
                    },
                )
            )

    # ggsave filename vs resolved plot data tokens conflict
    plot_by_var = {p.assigned_to: p for p in dag.plots if p.assigned_to}
    for s in dag.saves:
        if not s.path:
            continue
        save_toks = _experiment_tokens(s.path)
        if not save_toks:
            continue
        plot = plot_by_var.get(s.plot_expr) if s.plot_expr else None
        data_expr = None
        if plot is not None:
            data_expr = plot.data_expr or plot.piped_from
        elif len(dag.plots) == 1:
            data_expr = dag.plots[0].data_expr or dag.plots[0].piped_from
        if not data_expr:
            continue
        # resolve through var_sources to file path when possible
        source = dag.var_sources.get(data_expr, data_expr)
        data_toks = _experiment_tokens(f"{data_expr} {source}")
        if not data_toks:
            continue
        # conflict if both sides have distinctive labels and they don't overlap
        if save_toks.isdisjoint(data_toks) and _has_side_label(save_toks, data_toks):
            warnings.append(
                WarningItem(
                    tag=WarningTag.DATA_SWAP,
                    title="保存ファイル名と作図データのラベル不一致",
                    location=f"{path.name}:{s.lineno}",
                    reason=(
                        f"ggsave/pdf path={s.path!r} suggests {sorted(save_toks)}; "
                        f"plot data={data_expr!r} (source={source!r}) suggests {sorted(data_toks)}"
                    ),
                    sources=[str(path)],
                    code_snippet=f"{s.function}(...); data={data_expr}",
                    metadata={
                        "pattern_id": "P-FILENAME-CONTENT-MISMATCH",
                        "backend": dag.backend,
                        "save_path": s.path,
                        "data_expr": data_expr,
                    },
                )
            )

    return warnings


def warnings_from_python_dag(path: Path | str, dag: ScriptDAG) -> list[WarningItem]:
    path = Path(path)
    warnings: list[WarningItem] = []

    by_data: dict[str, list[int]] = {}
    for p in dag.plots:
        key = (p.data_expr or "").strip()
        if not key:
            continue
        by_data.setdefault(key, []).append(p.lineno)
    for data, lines in by_data.items():
        if len(lines) >= 2:
            warnings.append(
                WarningItem(
                    tag=WarningTag.DATA_SWAP,
                    title="同一 data 引数の複数作図呼び出し",
                    location=f"{path.name} lines {lines}",
                    reason=f"data={data[:120]}",
                    sources=[str(path)],
                    metadata={"pattern_id": "P-DATA-SWAP-CROSS-CONDITION", "backend": dag.backend},
                )
            )

    for s in dag.saves:
        if not s.path:
            continue
        save_toks = _experiment_tokens(s.path)
        # match against read paths / var sources used in plots
        for p in dag.plots:
            data = p.data_expr
            if not data:
                continue
            source = dag.var_sources.get(data, data)
            data_toks = _experiment_tokens(f"{data} {source}")
            if not save_toks or not data_toks:
                continue
            if save_toks.isdisjoint(data_toks) and _has_side_label(save_toks, data_toks):
                warnings.append(
                    WarningItem(
                        tag=WarningTag.DATA_SWAP,
                        title="保存ファイル名と作図データのラベル不一致",
                        location=f"{path.name}:{s.lineno}",
                        reason=(
                            f"savefig path={s.path!r} suggests {sorted(save_toks)}; "
                            f"plot data={data!r} (source={source!r}) suggests {sorted(data_toks)}"
                        ),
                        sources=[str(path)],
                        metadata={
                            "pattern_id": "P-FILENAME-CONTENT-MISMATCH",
                            "backend": dag.backend,
                        },
                    )
                )
                break

    return warnings
