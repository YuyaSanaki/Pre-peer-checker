"""Warning 細分類タグとレポート用データ構造."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WarningTag(str, Enum):
    """要件定義書 1.4 の Warning 細分類."""

    DATA_SWAP = "Warning [データ取り違え]"
    CONFIG_MISMATCH = "Warning [設定・アノテーション不整合]"
    STATS_MISMATCH = "Warning [実験データとの不一致]"
    SAMPLE_SIZE = "Warning [サンプルサイズ記載誤記]"
    STAT_METHOD = "Warning [統計手法の不整合]"
    IMAGE_REUSE = "Warning [画像重複・再利用（要出典確認）]"
    CONTROL_SHARE = "Warning [コントロール群共有]"
    REF_INCONSISTENCY = "Warning [表記揺れ・参照不整合]"
    PRECISION = "Warning [有効桁・数値表示]"


# HTML レポート用バッジクラス（docs デザイン仕様）
TAG_BADGE_CLASS: dict[WarningTag, str] = {
    WarningTag.DATA_SWAP: "badge-mismatch",
    WarningTag.CONFIG_MISMATCH: "badge-mismatch",
    WarningTag.STATS_MISMATCH: "badge-mismatch",
    WarningTag.SAMPLE_SIZE: "badge-sample",
    WarningTag.STAT_METHOD: "badge-sample",
    WarningTag.IMAGE_REUSE: "badge-image",
    WarningTag.CONTROL_SHARE: "badge-image",
    WarningTag.REF_INCONSISTENCY: "badge-mismatch",
    WarningTag.PRECISION: "badge-precision",
}


@dataclass
class WarningItem:
    """単一の Warning 出力."""

    tag: WarningTag
    title: str
    location: str
    reason: str
    sources: list[str] = field(default_factory=list)
    code_snippet: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # HTML-only side-by-side preview (embedded images); kept out of to_dict / JSON
    figure_compare: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    def is_demoted(self) -> bool:
        meta = self.metadata or {}
        if meta.get("demoted"):
            return True
        if str(meta.get("severity") or "").lower() == "info":
            return True
        return self.title.startswith("【降格】")

    def to_dict(self) -> dict[str, Any]:
        demoted = self.is_demoted()
        return {
            "tag": self.tag.value,
            "badge_class": "badge-info" if demoted else TAG_BADGE_CLASS[self.tag],
            "title": self.title,
            "location": self.location,
            "reason": self.reason,
            "sources": self.sources,
            "code_snippet": self.code_snippet,
            "metadata": self.metadata,
            "demoted": demoted,
        }
