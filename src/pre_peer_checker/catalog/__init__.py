"""公式オープンソースからの照合カタログ拡充パイプライン.

正本は ``fixtures/patterns/pubpeer_patterns.json``。
YAML（``rules/verification_catalog.yaml``）は人間可読 export。
提案は人手レビュー後にのみ merge（自動有罪判定・PubPeer スクレイプなし）。
"""

from __future__ import annotations

__all__ = ["cli"]
