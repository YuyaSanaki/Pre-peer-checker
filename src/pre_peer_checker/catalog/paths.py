"""パス解決（リポジトリ相対・キャッシュ）。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_PATTERNS = REPO_ROOT / "fixtures" / "patterns" / "pubpeer_patterns.json"
RESOURCES_PATTERNS = (
    REPO_ROOT / "src" / "pre_peer_checker" / "resources" / "pubpeer_patterns.json"
)
CATALOG_FIXTURES = REPO_ROOT / "fixtures" / "catalog"
RW_REASON_MAP = CATALOG_FIXTURES / "rw_reason_map.json"
ORI_SEED = CATALOG_FIXTURES / "ori_seed_rules.json"
COPE_SEED = CATALOG_FIXTURES / "cope_seed_rules.json"
RULES_YAML = REPO_ROOT / "rules" / "verification_catalog.yaml"
RW_CACHE_DIR = REPO_ROOT / "cache" / "retraction-watch-data"
RW_CSV_NAME = "retraction_watch.csv"
RW_GIT_URL = "https://gitlab.com/crossref/retraction-watch-data.git"
