# Benchmark metrics

`check_reference/`（PubPeer・説明資料など）は **検知観点の設計・ゴールド定義** に使い、エンドユーザー実行時の入力ではない。

適合率・再現率は `fixtures/gold/` の各 benchmark ケースに対する **相対指標** であり、生命科学論文全般での「検知率 100%」を意味しない。

## 定義

| 指標 | 定義 |
|------|------|
| **Recall** | 必須 gold 項目のうち、Warning がカバーした割合 |
| **Precision** | 出力 Warning のうち、いずれかの gold 項目を支持するものの割合 |

余分な Warning（gold 外）は実原稿ではあり得る。適合率を無理に 1.0 に寄せて過適合しないこと。

## 実行

```bash
python -m pre_peer_checker.eval.metrics_suite -o outputs/metrics/suite_report.json
# 公開合成ケースのみの example も fixtures/metrics/ に更新される
```

## LoRA について

**製品本線では LoRA は不要。** 照合は `fixtures/patterns/pubpeer_patterns.json` の決定論的パターンが本体。Legend の JSON 化は規則抽出＋任意の少数ショット LLM。LoRA は、多様な公開 Legend で規則／プロンプトが破綻したときだけのオプションであり、「有罪スコア」学習には使わない。

## H3 実コーパス適合率

合成以外の過去論文コーパスでの計測。定義は製品「検知率 100%」ではない。

| 指標 | 定義 |
|------|------|
| **gold_item_recall** | 合成 `image_reuse` / `image_partial` の required_recall |
| **citation_suppression** | 出典あり fixture で IMAGE_REUSE Warning=0 かつ cited アーティファクトあり |
| **distractor_fp_pairs** | 無関係 PubPeer 図との `cross_match_pairs` 数（低いほど良い） |
| **related_hit_pairs / H3 gold** | 関連コーパス投入時の交差ヒットと private_benchmark H3 項目 status |

```bash
# 合成のみ
python scripts/dev_h3_corpus_eval.py --synthetic-only

# 合成 + check_reference → cache/past_papers 取込 + private_benchmark
python scripts/dev_h3_corpus_eval.py
# → outputs/metrics/h3_corpus_report.json
```

実 PDF／抽出図は `cache/past_papers/`（git 外）。ペア一覧は `corpus_scan.artifacts.cross_match_pairs`。
