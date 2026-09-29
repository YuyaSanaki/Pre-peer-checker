"""カタログ JSON 正本の読み書き・YAML export・resources 同期."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from pre_peer_checker.catalog.paths import (
    FIXTURES_PATTERNS,
    RESOURCES_PATTERNS,
    RULES_YAML,
)

# pattern_id → 人間可読 ERR_*（YAML ビュー用。実行時 ID は常に P-*）
ERR_ALIASES: dict[str, str] = {
    "P-DATA-SWAP-CROSS-CONDITION": "ERR_DATA_MISMATCH_001",
    "P-FILENAME-CONTENT-MISMATCH": "ERR_DATA_MISMATCH_002",
    "P-N-MISMATCH-LEGEND-VS-DATA": "ERR_SAMPLE_SIZE_001",
    "P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS": "ERR_SAMPLE_SIZE_002",
    "P-IMAGE-REUSE-UNCITED": "ERR_IMAGE_REUSE_001",
    "P-SHARED-CONTROL-UNDISCLOSED": "ERR_SHARED_CTRL_001",
    "P-STATS-RECALC-MISMATCH": "ERR_STATS_RECALC_001",
    "P-STAT-METHOD-INCONSISTENT": "ERR_STAT_METHOD_001",
    "P-CONFIG-ANNOTATION-MISMATCH": "ERR_CONFIG_001",
    "P-REF-LABEL-MISMATCH": "ERR_REF_LABEL_001",
    "P-REF-MISSING-ENTRY": "ERR_REF_MISSING_001",
    "P-REF-DUPLICATE-KEY": "ERR_REF_DUP_001",
    "P-REF-META-INCONSISTENT": "ERR_REF_META_001",
    "P-REF-ORPHAN-ENTRY": "ERR_REF_ORPHAN_001",
    "P-REF-PDF-META-MISMATCH": "ERR_REF_PDF_META_001",
    "P-REF-CLAIM-CONTRADICTION": "ERR_REF_CLAIM_001",
    "P-IMAGE-PARTIAL-REUSE": "ERR_IMAGE_PARTIAL_001",
    "P-BLOT-LANE-REUSE": "ERR_BLOT_LANE_001",
    "P-VECTOR-SUBSET-UNDISCLOSED": "ERR_VECTOR_SUBSET_001",
    "P-ERRORBAR-SEM-SD-MISMATCH": "ERR_ERRORBAR_001",
    "P-STAT-MULTIPLICITY-GAP": "ERR_MULTIPLICITY_001",
    "P-NUMERIC-CROSSREF-MISMATCH": "ERR_NUMERIC_XREF_001",
    "P-SCALE-MAG-INCONSISTENT": "ERR_SCALE_MAG_001",
    "P-COUNT-N-MISMATCH": "ERR_COUNT_N_001",
    "P-SOURCE-DUPLICATE-VALUES": "ERR_SOURCE_DUP_001",
    "P-SOURCE-RATIO-ARTIFACT": "ERR_SOURCE_RATIO_001",
    "P-DERIVED-VALUE-PRECISION": "ERR_DERIVED_PRECISION_001",
    "P-SURVIVAL-COUNT-NONINTEGER": "ERR_SURVIVAL_N_001",
    "P-METHODS-CLAIM-MISMATCH": "ERR_METHODS_CLAIM_001",
    "P-EXCLUSION-UNDECLARED": "ERR_EXCLUSION_001",
}


def load_patterns(path: Path | None = None) -> dict[str, Any]:
    p = path or FIXTURES_PATTERNS
    return json.loads(p.read_text(encoding="utf-8"))


def save_patterns(doc: dict[str, Any], path: Path | None = None) -> None:
    p = path or FIXTURES_PATTERNS
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_resources(src: Path | None = None, dst: Path | None = None) -> None:
    """fixtures → resources のバイト一致同期（packaging 契約）。"""
    s = src or FIXTURES_PATTERNS
    d = dst or RESOURCES_PATTERNS
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")


def pattern_index(doc: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    d = doc or load_patterns()
    return {p["id"]: p for p in d.get("patterns", [])}


def patterns_to_yaml_rules(doc: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """実行時正本 JSON → 人間可読 YAML ルール行."""
    d = doc or load_patterns()
    rows: list[dict[str, Any]] = []
    for pat in d.get("patterns", []):
        pid = pat["id"]
        tags = pat.get("warning_tags") or []
        category = tags[0] if tags else "Warning"
        rows.append(
            {
                "id": ERR_ALIASES.get(pid, f"ERR_{pid.replace('-', '_')}"),
                "pattern_id": pid,
                "category": category,
                "description": pat.get("abstract_rule", ""),
                "status": pat.get("status", "planned"),
                "priority": pat.get("priority"),
                "target": pat.get("inputs", []),
                "severity": "Warning",
                "taxonomy": pat.get("taxonomy", []),
                "source_kinds": pat.get("source_kinds", []),
                "not_sufficient": pat.get("not_sufficient", []),
            }
        )
    return rows


def export_verification_yaml(
    doc: dict[str, Any] | None = None,
    out: Path | None = None,
) -> Path:
    """JSON 正本から ``rules/verification_catalog.yaml`` を生成."""
    d = doc or load_patterns()
    dest = out or RULES_YAML
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "canonical_store": "fixtures/patterns/pubpeer_patterns.json",
        "note": (
            "この YAML は人間可読ビュー。実行時は JSON 正本を読む。"
            "編集する場合は JSON を更新してから export し直すか、"
            "propose の提案をレビューして JSON に merge する。"
        ),
        "catalog_policy": d.get("catalog_policy", {}),
        "rules": patterns_to_yaml_rules(d),
    }
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 100
    with dest.open("w", encoding="utf-8") as fh:
        yaml.dump(payload, fh)
    return dest


def assert_resources_in_sync() -> None:
    a = FIXTURES_PATTERNS.read_text(encoding="utf-8")
    b = RESOURCES_PATTERNS.read_text(encoding="utf-8")
    if a != b:
        raise AssertionError(
            "resources/pubpeer_patterns.json is out of sync with fixtures/patterns/"
        )
