# Catalog expansion fixtures

Open-source inputs for the verification-catalog pulse (no PubPeer scrape).

| File | Role |
|------|------|
| `rw_reason_map.json` | RW Reason tag → `pattern_id`（製品外は `pattern_ids: []` + note） |

| `ori_seed_rules.json` | ORI abstract rules (human-curated seeds) |
| `cope_seed_rules.json` | COPE abstract rules (human-curated seeds) |
| `sample_rw_snippet.csv` | Tiny RW CSV for CI (no network) |

Canonical runtime catalog: `../patterns/pubpeer_patterns.json`  
Human-readable export: `../../rules/verification_catalog.yaml`  
Active overlay (local): `../../cache/catalog_active/`（WebUI Apply 先。git 外）

## 運用入口

| 入口 | 内容 |
|------|------|
| CLI | `python -m pre_peer_checker.catalog pulse --allow-fixture-fallback` ほか `pull-rw` / `mine` / `propose` |
| WebUI | 「カタログ更新」タブ — PubPeer PDF D&D → 仕分け → Apply → Issue Web Intent / PR CLI コピー（PAT 不要） |
| CI | `.github/workflows/catalog-pulse.yml`（提案のみ・自動 merge なし） |

PubPeer PDF は人手保存のみ。取込先は `cache/`。共有（Issue/PR）は **抽象ルールと `pattern_id` だけ**（PDF・コメント全文は載せない）。

**絶対ルール**（正本: [DETECTION §倫理](../../docs/DETECTION_AND_MODELS.md) / `catalog_policy.absolute_rules`）:

1. 表現を取らない（原文・長い引用・PDF 再配布なし）
2. アイデア／検査仕様に抽象化する
3. コメント者を名指し・攻撃しない
