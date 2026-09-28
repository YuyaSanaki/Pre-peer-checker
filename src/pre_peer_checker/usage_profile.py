"""利用区分（install.sh で選択）— ライセンス上の利用条件が付くコンポーネントの切替に使う.

- ``academic``: 大学・非営利組織による非商用研究
- ``commercial``: 上記以外（企業・商用研究・判断がつかない場合を含む）

優先順: 環境変数 ``PRE_PEER_CHECKER_USAGE`` > リポジトリ直下 ``usage_profile.json`` >
``commercial``（未選択時は利用条件の緩い側に倒す）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ACADEMIC = "academic"
COMMERCIAL = "commercial"
USAGES = (ACADEMIC, COMMERCIAL)

ENV_VAR = "PRE_PEER_CHECKER_USAGE"
USAGE_FILE = Path(__file__).resolve().parents[2] / "usage_profile.json"


def _normalize(value: object) -> str | None:
    v = str(value or "").strip().lower()
    return v if v in USAGES else None


def current_usage() -> str:
    env = _normalize(os.environ.get(ENV_VAR))
    if env:
        return env
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return COMMERCIAL
    return _normalize(data.get("usage") if isinstance(data, dict) else None) or COMMERCIAL


def lightglue_features(usage: str | None = None) -> str:
    """SuperPoint は Magic Leap の非商用研究ライセンスのため academic のみで使う."""
    return "superpoint" if (_normalize(usage) or current_usage()) == ACADEMIC else "aliked"
