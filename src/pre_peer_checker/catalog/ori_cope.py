"""ORI / COPE 人手シード規則の読込."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.paths import COPE_SEED, ORI_SEED


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_ori_seeds(path: Path | None = None) -> dict[str, Any]:
    return load_json(path or ORI_SEED)


def load_cope_seeds(path: Path | None = None) -> dict[str, Any]:
    return load_json(path or COPE_SEED)


def seed_pattern_ids(*docs: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for doc in docs:
        for rule in doc.get("rules", []):
            out.update(rule.get("pattern_ids") or [])
    return out
