# Fixtures

検証・回帰・学習評価用。**コードと汎用／合成 fixture は git 管理する。**  
実原稿が特定できるパス・実測 n・パネル対応が入ったゴールドだけ gitignore。

| パス | git | 内容 |
|------|-----|------|
| `patterns/pubpeer_patterns.json` | 追跡 | 製品向け汎用検知パターン（`status: implemented|planned`。ネタ帳は研究公正文献＋人手） |
| `catalog/` | 追跡 | RW/ORI/COPE シード・マッピング（拡充パルス用。PDF 本体は置かない） |
| `gold/demo/` | 追跡 | 合成回帰ケース（CI） |
| `gold/shared_control/` | 追跡 | 共有コントロール（端点欠落）合成回帰 |
| `gold/image_reuse/` | 追跡 | H3 画像再利用（合成コーパス）回帰 |
| `gold/cross_fig_reuse/` | 追跡 | 多パネル同一ベクトル回帰 |
| `gold/script_swap/` | 追跡 | R スクリプト DF 取り違え＋保存名不一致 |
| `synthetic/demo/` | 追跡 | 合成 CSV / legend |
| `synthetic/shared_control/` | 追跡 | 共有コントロール端点欠落ミニセット |
| `synthetic/image_reuse/` | 追跡 | H3: 原稿 PNG + 過去論文コーパス（同一画素・匿名） |

製品 WebUI の過去論文 PDF 取込先は fixture ではなく `cache/past_papers/`（git 外・ローカル再利用のみ）。CI 回帰は上表の `synthetic/image_reuse` + `--corpus` を使う。

| `synthetic/ref_biblio/` + `gold/ref_biblio/` | 追跡 | 参考文献メタ: 本文 cite の References 欠落・番号重複・年の自己矛盾 |
| `synthetic/ref_claim/` + `gold/ref_claim/` | 追跡 | 引用整合: 合成の引用先 PDF（`cited_pdfs/`）との年不一致・主張の増減／数値矛盾 |

参考文献 PDF の製品取込先は `cache/cited_papers/`（git 外）。CI は `synthetic/ref_claim/cited_pdfs` を `--cited-papers` で渡す（`gold_eval` はこのフォルダがあれば自動で使う）。

| `synthetic/cross_fig_reuse/` | 追跡 | 別 Fig で同一群ベクトル |
| `synthetic/script_swap/` | 追跡 | ggplot の DF 名と ggsave ファイル名の不一致 |
| `synthetic/source_dup/` | 追跡 | G9: 同一表内独立群の値完全一致 |
| `synthetic/source_ratio/` | 追跡 | G10: 同一表内整数倍比指紋 |
| `synthetic/stats_recalc/` | 追跡 | D5: Welch textClipping ≠ 兄弟 CSV |
| `patterns/pattern_synthetic_matrix.json` | 追跡 | pattern_id 1:1 CI カバレッジ正本 |
| `gold/source_dup/` / `source_ratio/` / `stats_recalc/` | 追跡 | 上記の gold_eval ケース |
| `gold/private_benchmark/*.example.json` | 追跡 | プライベートケース用スキーマ雛形 |
| `gold/private_benchmark/case_manifest.json` | **ignore** | 実データ由来の正規パス・群 n |
| `gold/private_benchmark/gold_warnings.json` | **ignore** | 実データ由来の期待 Warning |
| `gold/panel_extract/` | 追跡（雛形） | 別論文の薄い抽出教師。`HUMAN_REVIEW.md` + schema。filled は ignore |
| `input/panel_extract/README.md` | 追跡 | PDF/Word 置き場の説明。中身の PDF は ignore |
| `input/` / `check_reference/` | **ignore** | 実データ本体 |
| `metrics/` | 追跡（example） | gold 相対の適合率・再現率記録。`python -m pre_peer_checker.eval.metrics_suite` |

学習・推論コード: `src/pre_peer_checker/`（`eval/`, `llm/`, `imaging/`, …）、`docker/train_stub.py` — すべて追跡対象。

## ローカル（プライベート benchmark）

```bash
cd fixtures/gold/private_benchmark
cp case_manifest.example.json case_manifest.json
cp gold_warnings.example.json gold_warnings.json
# 実 input/ に合わせて編集（コミットしない）
```

## CI / 公開可能な回帰

```bash
# 合成 demo（CI 可）
python -m pre_peer_checker.eval.gold_eval --case demo --run --input fixtures/synthetic/demo
# 共有コントロール（端点欠落）
python -m pre_peer_checker.eval.gold_eval --case shared_control --run --input fixtures/synthetic/shared_control
# H3 画像再利用（コーパス付き）
python -m pre_peer_checker.eval.gold_eval --case image_reuse --run \
  --input fixtures/synthetic/image_reuse/manuscript \
  --corpus fixtures/synthetic/image_reuse/corpus
# 多パネル同一ベクトル
python -m pre_peer_checker.eval.gold_eval --case cross_fig_reuse --run --input fixtures/synthetic/cross_fig_reuse
# 誤参照 .R（ggplot DF ≠ ggsave 名）
python -m pre_peer_checker.eval.gold_eval --case script_swap --run --input fixtures/synthetic/script_swap
# G9/G10 ソース指紋
python -m pre_peer_checker.eval.gold_eval --case source_dup --run --input fixtures/synthetic/source_dup
python -m pre_peer_checker.eval.gold_eval --case source_ratio --run --input fixtures/synthetic/source_ratio
# D5 統計残渣不一致
python -m pre_peer_checker.eval.gold_eval --case stats_recalc --run --input fixtures/synthetic/stats_recalc
# E4/E5 参考文献メタ・引用整合
python -m pre_peer_checker.eval.gold_eval --case ref_biblio --run --input fixtures/synthetic/ref_biblio
python -m pre_peer_checker.eval.gold_eval --case ref_claim --run --input fixtures/synthetic/ref_claim \
  --cited-papers fixtures/synthetic/ref_claim/cited_pdfs
# pattern_id 1:1 CI
pytest -q tests/test_pattern_id_matrix.py
pytest -q
```

照合は Warning の `metadata.pattern_id` を優先する（単文字パネルトークンの誤一致を避ける）。  
`--run` 時は `run_coverage` を warnings JSON に同梱し、`layers`（parser / linking / match）で欠落層を診断する:

```bash
python -m pre_peer_checker.eval.gold_eval --case demo --run --input fixtures/synthetic/demo --layers \
  -o outputs/metrics/demo_layer_report.json
```

プライベート private_benchmark は `input/` + filled gold がある環境でのみ `test_private_benchmark_phase1_gold_recall` が走る。
