"""YAML 設定パーサー（群定義・パラメータ）."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def parse_yaml_config(path: Path | str) -> dict[str, Any]:
    yaml = YAML(typ="safe")
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.load(f)
    return data if isinstance(data, dict) else {"_root": data}


def extract_group_defs(config: dict[str, Any]) -> dict[str, Any]:
    """群定義らしきキーを抽出（柔軟マッチ）."""
    keys_of_interest = (
        "groups",
        "group",
        "conditions",
        "genotypes",
        "sample_size",
        "n",
        "n_samples",
        "samples",
    )
    found: dict[str, Any] = {}
    for key, value in config.items():
        if key.lower() in keys_of_interest:
            found[key] = value
        elif isinstance(value, dict):
            nested = extract_group_defs(value)
            if nested:
                found[key] = nested
    return found
