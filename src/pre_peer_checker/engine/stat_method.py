"""検定名の矛盾（Legend ↔ スクリプト／群構造）— P-STAT-METHOD-INCONSISTENT.

洪水回避: 明確な矛盾ゲートのみ。多重比較欠落は P-STAT-MULTIPLICITY-GAP に任せる。
"""

from __future__ import annotations

import re
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.warnings import WarningItem, WarningTag

_ANOVA_CLAIM = re.compile(
    r"\b(?:one[- ]way\s+)?anova\b|\bkruskal[- ]wallis\b",
    re.I,
)
_PAIRED_CLAIM = re.compile(
    r"\bpaired\s+t[- ]?tests?\b|\bwilcoxon\s+signed[- ]rank\b|\b対応のある\s*t",
    re.I,
)
_SCRIPT_T_ONLY = re.compile(
    r"\bt\.test\s*\(|\bttest_ind\s*\(|\bstudent'?s?\s+t[- ]?test\b",
    re.I,
)
_SCRIPT_ANOVA = re.compile(
    r"\baov\s*\(|\banova\s*\(|\bAnova\s*\(|\bkruskal\.test\s*\(|\boneway\b",
    re.I,
)
_PAIR_COL = re.compile(
    r"^(?:subject|pair|animal|mouse|fly|id|donor|replicate_id)$",
    re.I,
)


def _group_counts(vectors: list[GroupVector], *, min_n: int = 2) -> dict[Path, set[str]]:
    by: dict[Path, set[str]] = {}
    for v in vectors:
        if v.n < min_n:
            continue
        by.setdefault(v.source.resolve(), set()).add(v.group_key)
    return by


def warnings_from_stat_method(
    texts: list[str],
    vectors: list[GroupVector],
    *,
    script_texts: list[str] | None = None,
    script_paths: list[Path | str] | None = None,
    max_warnings: int = 2,
) -> list[WarningItem]:
    """Emit only on clear Legend↔script or Legend↔design contradictions."""
    blob = "\n".join(t for t in texts if t)
    if not blob.strip():
        return []

    paths = [Path(p) for p in (script_paths or [])]
    if script_texts is None and paths:
        script_texts = []
        for sp in paths:
            try:
                script_texts.append(sp.read_text(encoding="utf-8", errors="ignore")[:80_000])
            except Exception:
                continue
    scripts = "\n".join(s for s in (script_texts or []) if s)
    warnings: list[WarningItem] = []

    anova_m = _ANOVA_CLAIM.search(blob)
    if anova_m and scripts.strip() and _SCRIPT_T_ONLY.search(scripts) and not _SCRIPT_ANOVA.search(
        scripts
    ):
        span = anova_m.group(0)
        src = [str(p) for p in paths[:4]]
        loc = f"Figure 1 / {paths[0].name}" if paths else f"Figure / {span}"
        warnings.append(
            WarningItem(
                tag=WarningTag.STAT_METHOD,
                title="Legend は ANOVA なのにスクリプトは無補正 t のみ",
                location=loc,
                reason=(
                    f"Legend／Methods に「{span}」がある一方、解析スクリプト"
                    + (f"（{paths[0].name}）" if paths else "")
                    + "には t.test／ttest_ind のみで aov／anova が見つかりません。"
                ),
                sources=src,
                metadata={
                    "pattern_id": "P-STAT-METHOD-INCONSISTENT",
                    "claim": "anova",
                    "script_signal": "t_only",
                    "claim_span": span,
                },
            )
        )

    paired_m = _PAIRED_CLAIM.search(blob)
    if paired_m:
        counts = _group_counts(vectors)
        for path, keys in counts.items():
            if len(keys) < 3:
                continue
            if any(_PAIR_COL.match(k) for k in keys):
                continue
            span = paired_m.group(0)
            warnings.append(
                WarningItem(
                    tag=WarningTag.STAT_METHOD,
                    title="対応のある検定の記載なのに独立多群テーブル",
                    location=f"{path.name} ({len(keys)} groups)",
                    reason=(
                        f"Legend／Methods に「{span}」がある一方、"
                        f"{path.name} は {len(keys)} の独立群ラベル"
                        f"（{', '.join(sorted(keys)[:6])}）で、対応 ID 列の痕跡がありません。"
                    ),
                    sources=[str(path)],
                    metadata={
                        "pattern_id": "P-STAT-METHOD-INCONSISTENT",
                        "claim": "paired",
                        "n_groups": len(keys),
                        "groups": sorted(keys),
                        "claim_span": span,
                    },
                )
            )
            break

    return warnings[:max_warnings]
