"""YAML 群定義 ↔ 実テーブル群ラベル — P-CONFIG-ANNOTATION-MISMATCH."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.warnings import WarningItem, WarningTag

_SPLIT = re.compile(r"[,;/|\s]+")


def _norm(label: str) -> str:
    s = str(label).strip().lower()
    s = s.replace("−", "-").replace("–", "-")
    s = re.sub(r"\s+", "", s)
    return s


def _flatten_yaml_labels(obj: Any) -> set[str]:
    out: set[str] = set()
    if obj is None:
        return out
    if isinstance(obj, str):
        for part in _SPLIT.split(obj):
            if part.strip():
                out.add(_norm(part))
        return out
    if isinstance(obj, (list, tuple, set)):
        for x in obj:
            out |= _flatten_yaml_labels(x)
        return out
    if isinstance(obj, dict):
        # Prefer values that look like group name lists; also take keys under groups/
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in {"n", "sample_size", "n_samples", "replicates"}:
                continue
            if isinstance(v, (int, float)) and kl not in {"groups", "genotypes", "conditions"}:
                # genotype: n mapping → keys are labels
                out.add(_norm(k))
                continue
            out |= _flatten_yaml_labels(v)
            if kl in {"name", "label", "id", "genotype", "group", "condition"}:
                out |= _flatten_yaml_labels(k)
        return out
    out.add(_norm(obj))
    return out


def warnings_from_config_annotation(
    yaml_artifacts: list[dict[str, Any]],
    vectors: list[GroupVector],
    *,
    min_yaml_labels: int = 2,
    min_table_groups: int = 2,
) -> list[WarningItem]:
    """YAML-configured genotypes/groups with no overlap against table group keys."""
    table_groups: set[str] = set()
    table_sources: list[Path] = []
    for v in vectors:
        if v.n < 1:
            continue
        table_groups.add(_norm(v.group_key))
        table_sources.append(v.source)
    if len(table_groups) < min_table_groups:
        return []

    warnings: list[WarningItem] = []
    for art in yaml_artifacts:
        path = Path(art.get("path") or "config.yaml")
        groups_blob = art.get("groups") or {}
        yaml_labels = _flatten_yaml_labels(groups_blob)
        # Drop empties / pure numbers
        yaml_labels = {x for x in yaml_labels if x and not x.isdigit()}
        if len(yaml_labels) < min_yaml_labels:
            continue
        overlap = yaml_labels & table_groups
        if overlap:
            continue
        sample_yaml = ", ".join(sorted(yaml_labels)[:6])
        sample_tab = ", ".join(sorted(table_groups)[:6])
        warnings.append(
            WarningItem(
                tag=WarningTag.CONFIG_MISMATCH,
                title="YAML 群定義とテーブル群ラベルが一致しない",
                location=path.name,
                reason=(
                    f"設定 {path.name} の群／genotype（例: {sample_yaml}）と、"
                    f"実データの群キー（例: {sample_tab}）に共通集合がありません。"
                ),
                sources=[str(path)] + [str(p) for p in table_sources[:3]],
                metadata={
                    "pattern_id": "P-CONFIG-ANNOTATION-MISMATCH",
                    "yaml_labels": sorted(yaml_labels),
                    "table_groups": sorted(table_groups),
                },
            )
        )
    return warnings
