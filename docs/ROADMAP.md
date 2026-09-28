# 開発〜配布ロードマップ

本システムの目的は、**広く生命科学論文の投稿前検証を行い、研究公正上・PubPeer 上で問題視されやすい不整合を未然に防ぐ**ことである。`input/private_benchmark` と `check_reference/` は開発・回帰の**基準ケース（benchmark）**であり、エンジン実装をこのデータに過適合させてはならない。汎用ルールは `fixtures/patterns/pubpeer_patterns.json`、ケース固有の期待は `fixtures/gold/` に分離する。

進捗の測り方: (1) 基準ケースで H1–H3 相当を検知できるか (2) 同一エンジンがパターン定義どおり他セットにも適用できるか (3) ネタ帳のギャップが `pattern_id` として定義され後続エンジンに渡せるか。

```
  [Phase 0 ✓] ──► … ──► [Phase 4 ✓ 初期] ──► [Phase 5 初版済] ──► [Phase 6 進行中] ──► [後続]
  ゴールド〜配布受入              研究公正ネタ帳           精度向上                 新決定論エンジン
    (0–4)                          (Warning照合方法)        (読む/紐付け/n)           (P0→P1)
```

最終更新: 2026-09-26（Phase 7 文献メタ＋引用整合 初期。正本: [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) §2 Taxonomy E4/E5）

### 開発の絶対条件

| | |
|--|--|
| **読む** | ローカル LLM/VLM が Legend／Fig チャンク／パネル地図 → チェック項目 JSON（本線）。規則の無限追加は禁止 |
| **比べる** | 表・DAG・点列・画像類似度など決定論のみで Warning 確定。LLM に最終有罪判定をさせない |
| 正本 | [REQUIREMENTS.md](REQUIREMENTS.md) §1.2.1 / [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) §4–§5 / [../handoff.example.md](../handoff.example.md) |

---

## 現状サマリ（2026-09-26）

| 領域 | 状態 | メモ |
|------|------|------|
| 決定論コア（H1/H2/H4） | **完了** | private_benchmark + 合成。H2b は OFF/ON とも本物ヒット（required_recall=1.0） |
| H3 画像再利用 | **合成＋実コーパス計測** | WebUI `cache/past_papers/`。計測: `scripts/dev_h3_corpus_eval.py` → `outputs/metrics/h3_corpus_report.json` |
| 隣接パターン（共有 Ctrl / multi-figure / script swap） | **完了** | `fixtures/synthetic/*` |
| ローカル WebUI + `install.sh` | **実装済 + Mac 受入済** | arm64 で実ケース完走。照合タブで過去論文 PDF コーパス取込（H3）可 |
| レポート可視化 | **実装済** | n 対照表・Fig チャンク・タグフィルタ。**正の n＝実験データ行数**注釈 + **降格** badge／フィルタ |
| Legend 読み取り | **方式固定** | `pdftotext -raw` + 出版4型規則 + 規則優先。7B: hyper 46/46・paper_01/02 完走。Mac 既定 `qwen2.5-7b-mlx` |
| LLM/VLM モデル選択 | **達成** | レジストリ + CLI/WebUI/GUI。**配布本線=7B**（横断 bakeoff／LoRA は製品不要） |
| Fig↔表の軟紐付け | **達成（初期）** | `resolve_table_link` — Fig フォルダ必須ではない |
| **Entity Linking（指紋 / soft FP / Figスコープ）** | **Phase 6B 初期達成** | Tier1/2/3 + soft 抑制 + script三角 + **LIF XML 顕微鏡メタ接地** |
| スクリプト DAG | **初期達成** | `.R`/`.Rmd`/`.Rhistory` / Python / Prism / Kaleida。偽 `.R` 生成は製品外 |
| 大容量メモリ監視 | **将来** | ピーク RSS 等の明示記録は後続。製品ゲートにしない |
| **研究公正ネタ帳** | **Phase 5 初版＋合成 CI 済** | Taxonomy A–F。1:1 matrix。PubPeer スクレイプ禁止 |
| **精度向上** | **Phase 6 主要達成** | LightGlue 校正・RW マップ・**H3 実コーパス計測**済。残任意: ORI/COPE シード・実論文フル通し。§5: [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) |
| **文献メタ＋引用整合** | **Phase 7 初期達成** | 原稿内 bib＋ユーザー提供 PDF（`cache/cited_papers/`）。根拠は情報カード。`--cited-papers` / WebUI 2b |

---

## LLM / VLM モデル選択 — **選択インフラ達成（2026-09-24）**

### 方針

- **絶対条件**: 読む＝LLM/VLM（本線）、比べる＝決定論。規則パーサはフォールバックのみ。
- **開発段階では大きめモデルを許可**（ホスト／DGX で **〜128GB** まで）。精度で勝つ構成を先に決める。
- **配布・低メモリ Mac** 向けは後から (1) 量子化 (2) **蒸留**（教師＝大型、生徒＝7B/3B 級）(3) 必要なら開発専用のリモート推論エンドポイント、で縮小する。製品の「完全ローカル・API 課金ゼロ」は配布経路の制約として維持し、開発評価用クラウド／大型 GPU は分離する。
- WebUI / CLI / GUI で **プロファイルを選択**可能。既定は `qwen2.5-7b-mlx` / `qwen2.5-vl-7b`。
- 精度レバーの詳細: [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) §5（**Phase 6**）。

### 実装済み（パス）

| 項目 | パス / 入口 |
|------|-------------|
| カタログ | `src/pre_peer_checker/llm/model_registry.yaml` |
| 解決ロジック | `llm/registry.py` → `backend.select_backend(..., profile_id=)` |
| CLI | `--llm-profile` / `--vlm-profile` / `--list-llm-profiles` |
| WebUI | プロファイル select + `/api/llm-status`（`text_profiles` / `vision_profiles`） |
| GUI | LLM / VLM コンボボックス |
| bakeoff | `python -m pre_peer_checker.eval.model_bakeoff` |
| 回帰 | `tests/test_model_registry.py`、`tests/test_webui.py`（プロファイル渡し） |

### 実装ゴール

- [x] `llm/model_registry.yaml` に候補 ID・役割（text / vision）・推奨 VRAM・バックエンド（MLX / CUDA / transformers）を列挙
- [x] CLI: `--llm-profile` / `--vlm-profile` / `--legend-llm-model` / `--list-llm-profiles`
- [x] WebUI / GUI: モデル／プロファイル選択＋状態表示
- [x] `eval/model_bakeoff.py`: プロファイル解決・バックエンド probe → JSON
- [x] GPU 上で同一 fixture の横断精度評価（exact / soft match）→ 抽出本線プロファイルを確定 — **Phase 6A 初期**（読む JSON 合成 + 既存 gold_eval）
- [x] ~~勝ち筋での大型横断 bakeoff／プロンプト再固定／LoRA~~ → **不要（方針 2026-09-25）**: 配布本線は **7B で十分**。プロンプト＋規則は固定済。LoRA／蒸留は 7B が新しい論文種で繰り返し破綻したときのみ任意。有罪分類器は作らない
- [x] VLM generate 経路（Fig 画像→パネル地図）の本線配線（ベクター優先・空時のみ補助。`--vlm-assist`。**Mac mlx-vlm 受入済**）— **Phase 6A 初期**

### 候補カタログ（レジストリ収録・2026-09）

| 優先 | 系統 | プロファイル例 | 役割 | メモ |
|------|------|----------------|------|------|
| **P0** | **Qwen2.5 / Qwen2.5-VL** | `qwen2.5-7b-mlx`, `qwen2.5-7b-hf`, `qwen2.5-32b-hf`, `qwen2.5-vl-*` | text + Fig 接地 | **本命一本化**。text 教師=`32b-hf`、配布=`7b-mlx`（逼迫時 3B） |
| **P1** | **InternVL3** | `internvl3-8b`, `internvl3-38b` | 文書・OCR | 任意比較（CUDA） |
| **補助** | **MinerU2.5** 等 | （未収録・前処理候補） | PDF/レイアウト OCR | VLM 代替ではない |

**除外**: Gemma 3（gated・本線外）、Pixtral / MiniCPM-V（Qwen2.5-VL 本命に一本化）。

### 開発マシンでの推奨スイープ（128GB 想定）

1. **Text（確定）**: **配布／CI 本線 = `qwen2.5-7b-mlx` / `qwen2.5-7b-hf`**。`32b-hf` は任意の難 span ラベル作り（製品必須ではない）
2. **抽出品質**: `fixtures/gold/private_benchmark/panel_extract_gold*.json` + `scripts/dev_panel_extract_eval.py` で panel/group/n を測る（Warning gold recall とは別軸）
3. **Vision**: **配布 = `qwen2.5-vl-7b`**（Mac mlx-vlm 受入済）。`vl-32b` は任意教師
4. **LoRA／蒸留**: **製品本線では行わない**。7B がスキーマ強制＋規則でも繰り返し破綻したときだけ、人手修正ラベルで任意検討（生 32B 出力を無審査で教師にしない）

#### panel 抽出教師セットの増やし方（おすすめ順）

「抽出教師を増やす」と「フル benchmark ケースを増やす」は分ける。`panel_extract_gold` は Legend→(figure, panel, group, n) のみ；Warning / Entity Linking とは別軸。

1. **今ここ（初期達成）**: private_benchmark hard-span 12 件を人手確定（Fig1 L/M typo・Fig2 A/C/J・Fig5 E）。filled JSON は gitignore。追加 span は 30〜50 を目指して追記可
2. **7B を寄せる（初期達成）**: プロンプト強化 + `(k)/(l)→L/M` 規則 + LLM欠落パネルの規則補完で **panel recall 12/12**（forbid_fp=0）。**LoRA／蒸留は製品不要**（破綻時のみ任意）
3. **別論文は薄い case（初期達成）**: paper_01 / paper_02 各 10〜20 hard-span を人手確定。素材は `input/panel_extract/`、手順は `fixtures/gold/panel_extract/HUMAN_REVIEW.md`
4. **実験データは後から**: 手元に揃っている論文だけ、Warning gold / linking 用のフル case に格上げ。抽出精度のためだけに第 2・第 3 ケースの生データ一式は不要

#### Legend 読み取り方式（2026-09-25 固定）

| 段 | 内容 |
|----|------|
| 切出し | 出版 PDF は **`pdftotext -raw`**（段の順）。layout 混線は使わない |
| 規則 | 括弧の中身でパネル／群を分類。出版 4 型（パネル単位 `N=` / 時点リスト / 共有小文字 / 後置 `(n=)`）を `legend_struct` が先に取る |
| 7B | 規則が空だった文だけ埋める。**規則行は上書きしない**（空 group・同一パネルの別 n も残す） |
| 採点 | hard-span gold の `(figure, panel, group, n)`。7B の余分行は許容ノイズ（Warning 確定には使わない） |

7B 回帰（固定時点）: hyper **46/46**、paper_01 **27/27**、paper_02 **17/17**（forbid_fp=0）。入口: `scripts/dev_panel_extract_eval.py` / `scripts/dev_panel_extract_paper_eval.py`。

### やらないこと

- モデルに「有罪／不正スコア」を学習させる end-to-end 分類
- 配布既定をいきなり 70B 級にする（開発既定と配布既定は分離）
- 出版 PDF 向けにプロンプト一文を足して hyper の括弧分類を崩す

---

## 基準アセット（benchmark）

| パス | 役割 | 備考 |
|------|------|------|
| `input/private_benchmark/` | ローカル専用の照合対象（gitignore） | 実データの詳細パスは `handoff.md` のみ |
| `check_reference/` | 検知観点の手元参照（gitignore） | 個別論文名・コメントは git に入れない |
| `fixtures/patterns/` | **製品向け汎用パターン**（git 追跡） | `P-*` のみ |
| `fixtures/gold/demo/` + `synthetic/` | **合成回帰**（git 追跡） | 匿名・実データを含まない |
| `fixtures/gold/private_benchmark/` | プライベート benchmark 雛形 | **filled JSON は gitignore**。`*.example.json` のみ追跡 |

Docker: `input/` → `/data/input`、`check_reference/` → `/data/reference`（イメージに焼かない）。

### ゴールド検知ターゲット（private benchmark = 主評価）

| ID | 期待 Warning | 汎用 pattern_id | 自動検知（現状） |
|----|--------------|-----------------|------------------|
| H1 | `[データ取り違え]` | `P-DATA-SWAP-CROSS-CONDITION` | **達成**（合成＋ローカル benchmark） |
| H2a | `[サンプルサイズ記載誤記]` | `P-N-MISMATCH-LEGEND-VS-DATA` | **達成** |
| H2b | 同一プロットなのに Legend n 不一致 | `P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS` | **達成** |
| H3 | `[画像重複・再利用（要出典確認）]` | `P-IMAGE-REUSE-UNCITED` | **達成（合成＋実測）**；`outputs/metrics/h3_corpus_report.json` |
| H4 | `[データ取り違え]`（ファイル名≠中身） | `P-FILENAME-CONTENT-MISMATCH` | **達成** |

詳細パス・群スキーマの **filled** 定義と個別事例対応はローカル `handoff.md` / `fixtures/gold/private_benchmark/*.json`（gitignore）。

### 隣接パターン（汎化評価・合成）

| 典型パターン | pattern_id | 合成回帰 |
|--------------|------------|----------|
| 共有コントロール・端点欠落 | `P-SHARED-CONTROL-UNDISCLOSED` | `fixtures/synthetic/shared_control` |
| 別 Fig で同一群ベクトル | `P-DATA-SWAP-CROSS-CONDITION` | `fixtures/synthetic/cross_fig_reuse` |
| ggplot DF ≠ ggsave 名 | DAG / 残渣 | `fixtures/synthetic/script_swap` |
| 原稿画像 ≡ コーパス | `P-IMAGE-REUSE-UNCITED` | `fixtures/synthetic/image_reuse` |
| ソース同一値・比指紋 | `P-SOURCE-DUPLICATE-VALUES`, `P-SOURCE-RATIO-ARTIFACT` | `source_dup` / `source_ratio` |
| 統計残渣≠表 | `P-STATS-RECALC-MISMATCH` | `stats_recalc` |
| 生存非整数・Methods 矛盾・無宣言除外 | G11–G13 planned | 合成は後続 |

---

## Phase 0: ゴールド定義と入出力規約（〜第1週）— **完了**

- [x] `check_reference` から検知観点 JSON（パネル・群・期待タグ・根拠）→ `fixtures/gold/private_benchmark/gold_warnings.json` + 汎用 `fixtures/patterns/pubpeer_patterns.json`
- [x] `input/private_benchmark` の正規ルート固定 → `fixtures/gold/private_benchmark/case_manifest.json`
- [x] 評価コマンド規約 + ゴールド照合スクリプト → `python -m pre_peer_checker.eval.gold_eval`
- [x] Docker 実行・学習環境
- [x] 製品は一般配布・任意論文向け、実データは benchmark である旨を要件・本ロードマップに明記

**完了条件**: H1–H3 を人間がトレースできる対応表（ファイルパス ↔ パネル ↔ 期待 Warning）が fixtures に存在する。→ **達成**

---

## Phase 1: 実データ対応の決定論的コア（第1–5週 / DGX Spark）— **完了**

本ケースに無い形式（`.R` 本線・YAML・`.czi`）は後回し可。**今あるファイルだけで H1/H2 を出せる経路を最優先**する。

### 検知ゴール

| 必須ゴール | 状態 | 主な実装 |
|------------|------|----------|
| H1 データ取り違え | **達成**（Rplot + 出版 PDF のパネル間同一点列） | `pdf_plot_digitize` / `pdf_panel_plots` / 統計残渣食い違い |
| H2a n 誤記 | **達成** | `legend_struct` + `n_and_names` |
| H2b 同一プロットで n 不一致 | **達成** | `warnings_inconsistent_n_identical_plots` |
| H4 ファイル名≠中身 | **達成** | 残渣ヒューリスティック + Rplot↔xlsx |
| gold_eval 回帰 | **達成** | required recall 1.0（H3 はコーパス無しで skip） |
| H3 画像再利用 | **同一セット内は達成** / 外部は Phase 2 で合成達成 | `microscopy_scan` + DINOv2/fallback |

主なモジュール: `parsers/legend_struct.py`, `pdf_plot_digitize.py`, `pdf_panel_plots.py`, `r_residue.py`, `python_ast.py`, `r_treesitter.py`, `data/group_vectors.py`, `engine/n_and_names.py`, `engine/plot_table_match.py`, `engine/panel_plot_identity.py`, `engine/stats_residue_match.py`, `engine/script_dag.py`, `eval/gold_eval.py`, `pipeline/orchestrator.py`。回帰: `tests/test_phase1_engines.py`, `tests/test_gold_fixtures.py`, `tests/test_script_dag.py`, `tests/test_imaging_and_report.py`。

### 1A. パーサー（実在フォーマット優先）

- [x] Word: Figure Legend ブロック結合、パネル紐付け `n=9 (C)` 抽出（`legend_struct`）
- [x] PDF: 埋め込み画像書き出し（`pdf_images`）— パネル内重複比較
- [x] PDF: ggplot 風ベクトル PDF の点列数字化（`pdf_plot_digitize`）— `Rplot*.pdf` 本線
- [x] 出版多パネル PDF のパネル割当＋点列同一性（`pdf_panel_plots`）
- [x] Excel/CSV: 群ベクトル抽出（`group_vectors`）、作図用表の優先
- [x] R 残渣: `.textClipping` パーサ（`r_residue`）+ 残渣フォルダ混在ヒューリスティック
- [x] `.lif` / `.tif` / ラスタ ローダー連携（`microscopy` + 同一セット重複スキャン）。`.czi` / LIF 本線は `[imaging]` extra
- [x] Python `ast` / R tree-sitter 本線（`ScriptDAG` / `RScriptDAG`、`.ipynb` は nbformat）。`[r-ast]` extra（language-pack）または `tree_sitter_r`。未導入時は正規表現フォールバック

### 1B. 照合エンジン

- [x] 群ベクトル完全一致（作図用表・別名ファイル）→ `[データ取り違え]` / `[コントロール群共有]`
- [x] Legend パネル n vs 生データ行数 → `[サンプルサイズ記載誤記]`（**H2a**）
- [x] 残渣フォルダの複数実験系ファイル名混在 → `[データ取り違え]`（**H4**）
- [x] Rplot 点列 ↔ 同フォルダ xlsx → ファイル名トークン衝突で Warning（**H1/H4**）
- [x] 別実験系ラベルの Rplot 同士で点列一致（**H1 副次**）
- [x] **H1 出版 PDF**: Figure パネル間点列同一性
- [x] **H2b**: 同一点列なのに Legend 記載 n がパネル間で不一致（`P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS`）
- [x] 誤検知抑制（表読込失敗・統計手法ヒントを Warning から除外し artifacts へ）
- [x] 統計再計算 vs textClipping 突合（Welch mean/p、Dunnett estimate）＋ **統計残渣↔作図残渣の食い違い**（H1 補強）
- [x] DINOv2 重複スキャン経路（`scan_image_duplicates_auto`）＋ gray/ahash フォールバック
- [x] LightGlue 精密マッチ（候補再検証；未導入時は ORB/NCC）— `imaging/lightglue_match.py`

### 1C. パイプライン / レポート

- [x] `input/private_benchmark` 一括走査 → HTML / JSON
- [x] Warning に根拠パス・`pattern_id` を付与
- [x] `gold_eval` を Phase 1 出力に対して固定（`pattern_id` 優先照合、`--run`、回帰テスト）
- [x] レポートのタグ別フィルタ＋検索（`html_report`）
- [x] Phase 2 橋渡し: Legend JSON スキーマ＋規則抽出（`llm/legend_schema` / `legend_extract`）、`--corpus` 外部 H3 照合

**Phase 1 完了条件**: Docker / ローカルで private_benchmark を投入し、**H1・H2a・H2b・H4 を Warning として安定再現**し、`gold_eval` required recall = 1.0。→ **達成**

### 評価コマンド（回帰）

```bash
# 合成 demo（CI 可）
python -m pre_peer_checker.eval.gold_eval --case demo --run --input fixtures/synthetic/demo

# 基準ケース（ローカル private gold + input が必要）
python -m pre_peer_checker.eval.gold_eval --case private_benchmark --run \
  --input input/private_benchmark/Manuscript/word \
  -o outputs/private_benchmark_gold_report.json

# または verify → eval
pre-peer-checker input/private_benchmark -o outputs/report.html --json outputs/warnings.json
python -m pre_peer_checker.eval.gold_eval --case private_benchmark --warnings outputs/warnings.json
```

pytest: `tests/test_gold_fixtures.py`（demo は常時、private_benchmark は input/gold があるときのみ）。

---

## Phase 2: 参照事例での検知率検証と意味抽出（第6–9週 / DGX）— **完了**

- [x] Figure Legend / Methods から群名・$n$・統計手法・$p$ を JSON 化する**スキーマ＋LLM プロンプト＋規則フォールバック**を確定 — `llm/legend_schema.py`（※現方針では LLM が読む本線）
- [x] 出版多パネル PDF の点列復元を強化（マーカー抽出拡張・ページ横断同一性・合成 PDF 回帰）。Excel/Rplot 無しでもパネル間同一性を検知可
- [x] **H3 経路**: `--corpus` で過去論文画像コーパスと DINOv2(+精密マッチ) 照合 + Legend 出典有無チェック。合成回帰 `fixtures/synthetic/image_reuse` で CI 可能。WebUI 照合タブで過去論文 PDF を `cache/past_papers/` に取込・選択可能（ローカル再利用のみ）
- [x] shared-control / multi-figure パターンを**合成ミニセット**で回帰（`shared_control` / `cross_fig_reuse` / `script_swap`）
- [x] `check_reference` 由来ゴールドに対する適合率・再現率を記録（`eval/metrics_suite`、`fixtures/metrics/`。「100%」主張なし）
- [x] ~~必要時のみ LoRA~~ → **製品本線では不要**（下記）。train スタブはキャッシュ／閾値校正用に残す
- [x] Legend LLM を MLX/CUDA に接続（`llm/backend.py`：auto=MLX→transformers）。`--legend-llm` で有効化。モデル未導入時は規則にフォールバック
- [x] （後続 Phase 3）モデルプロファイル選択 — 下記「LLM / VLM モデル選択」

### 抽出・LoRA 方針（絶対条件 2026-09-24）

| | |
|--|--|
| **不要（採用しない）** | PubPeer「有罪」分類器、DINOv2 フル FT、特定論文への過適合 LoRA、規則の無限追加 |
| **比べる（最終 Warning）** | 決定論（表・PDF・DAG・画像類似）のみ。説明可能な Warning |
| **読む（抽出・接地）** | LLM/VLM **本線**（プロファイル選択可）。規則はフォールバック／安全網 |
| **学習** | 抽出 JSON / パネル接地の SFT・LoRA・蒸留のみ（破綻時）。有罪スコア学習はしない |

**完了条件**: H1–H3 を自動検知。隣接主要パターンを fixture で回帰可能。→ **達成**（実コーパス適合率の継続計測は任意バックログ）

---

## Phase 3: Apple Silicon 最適化と GUI 統合（第10–13週）— **ほぼ完了**

> WebUI 本線・MLX Qwen・モデル選択・**VLM パネル地図補助**まで Mac 受入済。読む JSON ゴールド CI 済。**配布=7B で十分**（大型 bakeoff／LoRA は製品不要）。

### 実装済み（DGX / ホストで確認可）

- [x] PyQt6 GUI（フォルダ D&D、過去論文コーパス任意指定、QThread、進捗バー）— `pre-peer-checker-gui`（任意・開発用）
- [x] ローカル WebUI（親フォルダ `manuscript/` + `data/`、`pre-peer-checker-web`）— **非エンジニア向け本線**
  - ケースレイアウト検証、フォルダピッカー、`.zip` 自動展開
  - Legend LLM ON/OFF、**LLM/VLM プロファイル選択**、`/api/llm-status`（カタログ＋バックエンド）
- [x] 色分けインタラクティブ HTML レポート（タグ別フィルタ、検索、根拠ファイルへの `file://` リンク）
- [x] **照合カバレッジ**（何を見たか / skipped 理由）— `pipeline/run_coverage.py` → HTML / WebUI / GUI
- [x] **パネル n 対照表**（原稿 / 実験データ / 作図 / 統計）— `engine/n_matrix.py` → HTML レポート
  - 参照ファイル名（`file` / `file_display`）・未紐付け明示は **Phase 6B で強化済**
- [x] **Fig チャンク**（Legend + Results + Methods 束ね）— `parsers/figure_chunks.py`（LLM 読む本線の入力）
- [x] 入力拡張: 10x Genomics 行列の次元整合の軽量確認（`data/tenx_matrix.py`）、gzip / zip 収集
- [x] **LLM/VLM モデル選択** — `model_registry.yaml` / `registry.py` / bakeoff（上記セクション）

### Mac 受入（実施済み）

- [x] Apple Silicon（arm64）上で WebUI 完走 — 実ケース `~/Desktop/Manuscript`（`manuscript/` + `data/`、LIF/PDF/xlsx 多数、Warning 28 件・カバレッジ出力）。`outputs/web_report.html` 参照
- [x] 同マシンで `tests/test_webui.py` + `scripts/verify_packaging_layout.sh` 成功、`install.sh` / `Pre-peer-checker.command` 配置済み
- [x] **MLX Qwen 実推論** — `[mlx]` 導入、重み `Qwen2.5-7B-Instruct-4bit`（~4.3GB）取得済み。WebUI 照合で `legend_llm_status.status=active` / backend=`mlx (...Qwen2.5-7B-Instruct-4bit)` / `n_figure_chunks=9`（`outputs/web_warnings.json`）

### 残り（Phase 3 延長 → Phase 6 / 4 へ移管）

- [x] VLM 推論配線（パネル地図補助）→ **Phase 6A・Mac 受入済**。大型横断 bakeoff／LoRA は製品不要（7B 本線確定）
- [ ] （将来）16GB 級ピークメモリ・長時間安定性の明示記録 — 製品ゲート外
- [x] 対照表の抽出列が `hybrid-llm` になるレポートを再保存 → Phase 6 受入の一部

**完了条件（改訂）**: Apple Silicon で WebUI 完走、Legend LLM（MLX）active、モデル選択 UI・VLM 補助利用可 → **達成**。配布本線=7B 確定（大型 bakeoff／LoRA は製品不要）。

---

## DGX Spark で続けられるバックログ（Mac 不要）

`check_reference/` の PubPeer 観点は **設計・ゴールド用**。実数値・画像はコピーせず、匿名化合成を `fixtures/synthetic/` に置く。エンジンは常に `fixtures/patterns/` の汎用 `pattern_id` で動かす。

| # | 内容 | 状態 |
|---|------|------|
| **1** | **H3 合成コーパス**: 同一 PNG を原稿側 + 「過去論文」側に置き、`--corpus` で `P-IMAGE-REUSE-UNCITED` を CI 回帰（出典ありは抑制） | **達成** — `fixtures/synthetic/image_reuse` |
| **2** | **多パネル同一ベクトルミニセット**: 別 Fig / 条件ラベルなのに同一群ベクトル → `P-DATA-SWAP-CROSS-CONDITION` / 共有疑い | **達成** — `fixtures/synthetic/cross_fig_reuse` |
| **3** | **誤参照 `.R` + 正しい xlsx**: スクリプト DAG で作図 DF 取り違えを回帰 | **達成** — `fixtures/synthetic/script_swap` |
| **4** | レポート可視化（カバレッジ・n 対照表・Fig チャンク・LLM 状態） | **達成** — HTML / WebUI |
| 5 | GUI / WebUI 磨き（進捗の細分化、コーパス指定 UI 強化） | 一部達成 — WebUI: 過去論文 PDF → ローカル H3。GUI フォルダ指定は既存 |
| 6 | Spark GPU で DINOv2 / LightGlue 通し、合成ペアで閾値校正 | **達成（LightGlue）** — CUDA 実測 must_neg≤17 / must_pos≥153 → 35 確定。DINOv2 通しは任意 |
| 7 | Legend LLM を `[llm-cuda]` で通し、規則との差分確認 | 任意 |
| **7b** | ~~モデル横断 bakeoff~~ | **完了扱い（不要）** — Qwen 7B 本線確定。InternVL 等の横断比較は任意 |
| 7c | ~~7B 蒸留／抽出 LoRA~~ | **製品不要** — 7B 破綻時のみ任意 |
| 8 | 実過去論文コーパスでの H3 適合率記録（匿名化・非公開データ） | **記録済** — `scripts/dev_h3_corpus_eval.py` → `outputs/metrics/h3_corpus_report.json` |

シミュレート方針: PubPeer PDF から「何が問題か」だけ抽出し、合成 CSV / PNG / 短い docx / `.R` で再現。git に実論文図は入れない。

---

## Phase 4: 配布・起動（WebUI）・実機受入（第14–15週）— **達成（初期）**

> **本線は `.dmg` ではない。** `./install.sh` → ショートカット → ローカル WebUI。Developer Program / 公証は不要（[PACKAGING.md](PACKAGING.md)）。  
> **オフライン受入は要件から削除**（完全ローカル・API 課金ゼロは維持）。メモリ監視は将来実装。

### 実装済み

- [x] PyInstaller 仕様 + Linux CLI スモークスクリプト（任意・開発回帰用）
- [x] 配布レイアウト検証（`scripts/verify_packaging_layout.sh`）— WebUI / `install.sh` 中心
- [x] ローカル WebUI + `install.sh` + Mac/Linux 起動ランチャ（`Pre-peer-checker.command` / `scripts/start_webui.sh`）
- [x] ケース入力規約（親/`manuscript`+`data`、zip 自動展開）とライセンス整理（README / PACKAGING）
- [x] `.dmg` / Developer ID 公証は **廃止**（レガシースクリプトは deprecated）

### 受入

- [x] Apple Silicon 上で WebUI 受入 — 実ケース（private_benchmark 相当スライス / `Desktop/Manuscript`）完走。合成 demo は pytest / `gold_eval` 経路でカバー（WebUI 親フォルダ規約は `test_webui` が demo 相当を生成）
- [ ] （将来）大容量顕微鏡画像のメモリ・安定性の明示検証（ピーク RSS 等）— 製品ゲート外

**完了条件**: Docker 無しで `install.sh` 後のショートカットから、**API 課金ゼロのまま**基準ケースを再現 → **達成**（WebUI 経路 Mac 完走）。

手続きの正本: [PACKAGING.md](PACKAGING.md)。

---

## Phase 5: 研究公正ネタ帳（Warning／照合方法カタログ）— **カタログ初版済**

> **カタログ先出し**: 精度（Phase 6）の前に「何を Warning するか」を固める。PubPeer は有罪教師ではなく **照合方法の発見源**（サイト自動スクレイプは禁止）。決定論 `pattern_id` を増やす（ML 有罪分類器は作らない）。正本: [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) §1–§2.0.1。

### 目的

研究公正上・投稿前に機械照合できる不整合の **ネタ帳**を優先度付きで定義し、`fixtures/patterns/pubpeer_patterns.json` と DETECTION に落とす。エンジン実装は **後続**（`status: planned` までがカタログゲート）。

### 収集源ポリシー（PubPeer スクレイプ無し）

| 優先 | ソース | 取り方 |
|------|--------|--------|
| **P0** | **Retraction Watch Database**（[Crossref GitLab](https://gitlab.com/crossref/retraction-watch-data) / API） | 公式 CSV／Git。撤回理由タグ→類型 |
| **P0** | **ORI Case Summaries** | 公開事例から不一致シグナルを抽象化（有罪ラベル化しない） |
| **P0** | **COPE Case Database** | 編集部ケースの類型コード |
| **P0** | 学術文献（**Bik et al. 2016** 画像 Cat I–III 等） | DOI＋類型メモ |
| **P0** | `check_reference/`・benchmark | 既カバー対応 |
| **P1** | PubPeer **人手閲覧**＋WebUI PDF D&D | 抽象ルールだけ。PDF は `cache/`。原文非 git |
| **不可** | PubPeer／RW **記事サイト**の自動一括スクレイプ | ToS・方針違反 |

採用基準: **機械照合できるシグナル**。「意図」「捏造」断定なし。

### Verification Taxonomy（要約）

大分類 **A 画像 / B データ取り違え / C n / D 統計 / E 表記 / F ソースデータ指紋**。詳細対応表は DETECTION §2.0.1。既存 `P-*` と planned ギャップをこの体系にマップ済み。

### 既カバー / ギャップ

既カバー: `P-DATA-SWAP-*`, `P-FILENAME-CONTENT-MISMATCH`, `P-N-*`, `P-IMAGE-REUSE-UNCITED`, `P-SHARED-CONTROL-*`, `P-STATS-*`, `P-STAT-METHOD-*`, `P-CONFIG-*`, `P-REF-*`。  
ギャップ（planned）: （カタログ上の照合パターンは一通り初期実装済。精緻化・Tier3 は後続）。  
**実装済に昇格（2026-09-25 初期）**: `P-SOURCE-DUPLICATE-VALUES`, `P-SOURCE-RATIO-ARTIFACT`, `P-EXCLUSION-UNDECLARED`, `P-IMAGE-PARTIAL-REUSE`, `P-BLOT-LANE-REUSE`, `P-VECTOR-SUBSET-UNDISCLOSED`, `P-NUMERIC-CROSSREF-MISMATCH`, `P-METHODS-CLAIM-MISMATCH`, `P-ERRORBAR-SEM-SD-MISMATCH`, `P-STAT-MULTIPLICITY-GAP`, `P-SURVIVAL-COUNT-NONINTEGER`, `P-SCALE-MAG-INCONSISTENT`, `P-COUNT-N-MISMATCH`。

### 拡充パイプライン（公式オープンソース・実装済）

```
① RW Crossref Git ──► cache/ 日次 pull ──► Reason タグ集計（生命科学フィルタ）
② ORI / ③ COPE 人手シード ──► 抽象ルール
④ Bik 等文献シード（DETECTION §1.3）
⑤ PubPeer PDF（人手保存・WebUI D&D）──► cache/ のみ ──► 抽象ルール抽出
        │
        ▼
 propose / キュレーションキュー（自動 merge なし）
        │
        ├──► 人手レビュー → Apply → cache/catalog_active/（照合時に優先）
        ├──► 任意: fixtures/patterns/pubpeer_patterns.json へ反映
        └──► 任意: GitHub Issue（抽象案） / PR（fixtures 差分）で開発レポへ共有
        │
        ▼ export
 rules/verification_catalog.yaml（ERR_* エイリアスの人間可読ビュー）
```

- CLI: `python -m pre_peer_checker.catalog pulse` / `pre-peer-checker-catalog`
- WebUI: 「カタログ更新」タブ（PDF D&D・pull・仕分け・Apply・Issue/PR）
- マッピング: `fixtures/catalog/rw_reason_map.json` + `ori_seed_rules.json` + `cope_seed_rules.json`
- 日次: `.github/workflows/catalog-pulse.yml`（提案アーティファクトのみ）
- 正本は **JSON のみ**。YAML は export。PubPeer 自動スクレイプ禁止。PDF・コメント全文は git / Issue・PR に載せない。

### チェックリスト

- [x] 収集源ポリシーを DETECTION に同期（RW / ORI / COPE / 文献シード）
- [x] Verification Taxonomy A–F ↔ `pattern_id` 対応表（DETECTION §2.0.1）
- [x] 既カバー／ギャップ対応表を DETECTION §2 に記載
- [x] `pubpeer_patterns.json` に `status: planned` の新 `pattern_id`（resources 同期）
- [x] 各ギャップに `inputs` / `abstract_rule` / `not_sufficient` / 合成 fixture 方針 / `sources`
- [x] Phase 6 向け **P0 抽出キー**表
- [x] **`check_reference` PubPeer 15** の観点を §1.1 / G9–G13 / Taxonomy F·C3 に反映（2026-09-24）
- [x] **RW/ORI/COPE カタログ拡充パイプライン**（`pre_peer_checker.catalog` + 日次パルス）
- [x] **WebUI カタログ更新タブ**（pull → Qwen 仕分け → 人手キュレーション → アクティブカタログで照合）
- [x] **PubPeer PDF D&D 取込**（cache のみ・抽象ルール抽出・サイト自動取得なし）
- [x] **開発レポへの共有**（標準 JSON schema 1.1 · Issue=Web Intent · PR=CLI コピー。PAT 不要）
- [x] （任意）RW Crossref の未マップ Reason 上位を人手で `rw_reason_map.json` に追加 — **2026-09-26**: 照合可能 Reason を pattern 割当＋製品外は空マップ明示。生命科学 Reason 質量 ≈99.8% カバー
- [ ] （任意）文献シード・ORI／COPE を人手で数本読み、シード規則を精緻化
- [x] planned / 実装済 `pattern_id` ごとの合成ミニ fixture＋単体テスト整備 — `pattern_synthetic_matrix.json` + `tests/test_pattern_id_matrix.py`（ci_required は必須再現；deferred/planned は理由付き）
- [x] `gold_eval` 拡張スイートで層別失敗レポート（パーサー vs 紐付け vs 照合）— `eval/layers.py` + `--layers`

**完了条件**: カタログ初版（DETECTION + patterns + Taxonomy + オープンソース方針）→ **達成**。合成 1:1 CI ゲート → **達成（初期）**。残 planned エンジン・RW 追従は後続（**Taxonomy 増だけでは精度は上がらない** — Phase 6 必須）。

**やらないこと**: PubPeer 自動スクレイプ、有罪 ML、**カタログ倫理の絶対ルール違反**（原文・長い引用・PDF 再配布／有罪断定／コメント者の名指し・攻撃）、論文固有 regex 無限追加、ルールの二重ファイル管理、カタログの自動 merge。正本: [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) 冒頭。

---

## Phase 6: 精度向上 — Entity Linking / スキーマ強制 / パネル分割 — **進行中**

> 指針の正本: [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) §5（**3大ボトルネック・3層照合**）。絶対条件は崩さない。  
> **旧 Phase 5**。カタログ増だけでは FP が増える → **紐付けと決定論照合が本線**。

目標: Entity Linking・読む JSON の安定性・複合 Fig パネル接地を上げ、n 誤記・取り違えを説明可能な Warning として安定再現する。

**現状（2026-09-26）**: 6A–6E + LIF/CZI + 配布=7B + G13/連続スケール + Tier2/3 + **LightGlue CUDA 実機校正（DEFAULT_MIN_MATCHES=35）**。

### 精度の前提（スコープ不足の認識）

| ボトルネック | Phase で潰す場所 |
|--------------|------------------|
| ① Entity Linking（列↔変数↔Legend↔パネル） | **6B** データ指紋＋3層照合 |
| ② LLM 抽出の揺れ | **6A** スキーマ強制 |
| ③ 複合 Fig のパネル bbox | **6A** PDFベクター分割（VLM は補助） |

### 6A. 読む — スキーマ強制＋パネル分割

- [x] Fig 単位チャンク・LLM 本線・基本スキーマ（`legend_schema` / `LEGEND_JSON_SCHEMA`）
- [x] 文脈プロンプト（括弧＝パネルか群か）＋ **読む JSON ゴールド拡充**（shared D/E/G/H・N–Q・J 遺伝子型・chunk+panel_labels）
- [x] **スキーマ強制（初期）**: `coerce_legend_dict` / `parse_legend_llm_response` で型矯正・不正パネル行破棄（stdlib）
- [x] **スキーマ強制（本線配線）**: `llm/json_mode.py` — Outlines(MLX/transformers) → 失敗時 `free+coerce`。検証: `scripts/dev_legend_json_mode_verify.py`（Mac: `--prefer mlx --require-outlines`）。extra `llm-json`
- [x] **PDFベクター・パネル分割（初期）**: `pdf_panel_geometry` — 太字1文字ラベル → 軸平行矩形 → PNG 切り出し。レポート hotspot と同一幾何。DINOv2/LightGlue は imaging 任意
- [x] **VLM パネル地図補助（初期）**: ベクター分割が空の出版 Fig に限り mlx-vlm / transformers-VL（`--vlm-assist`）。検証: `scripts/dev_vlm_panel_map_verify.py`（**Mac**: `--prefer mlx --synthetic --require-vlm` → `vlm_used`・A/B OK）
- [x] **読む JSON ゴールド（合成 CI）**: `fixtures/gold/legend_json/legend_json_synthetic_matrix.json` + `tests/test_legend_json_gold.py`（Fig2A / Fig4K / Fig5E 型）。評価: `scripts/dev_legend_json_gold_eval.py`
- [x] **Phase 5 P0 抽出キー（スキーマ）**: `error_bar_type` / `independence_claims` / `exclusion_criteria`（coerce + rules 検知 + プロンプト）。エンジン突合は後続
- [x] **GPU bakeoff → 本線プロファイル（初期）**: 読む JSON 合成で rules/7B OK（forbid_fp=0）。**配布 text=`7b-mlx` / CUDA=`7b-hf` / VLM=`vl-7b`**。32B・蒸留・LoRA は製品不要（任意）
- [x] Outlines / transformers JSON schema / MLX JSON mode の本線配線（任意 `llm-json`；未導入時は coerce）
- [x] VLM generate 経路（Fig→パネル地図）の補助配線（ベクター優先）
- [x] ~~大型横断 bakeoff／勝ち筋 LoRA~~ → **製品不要（2026-09-25）**
### 6B. Entity Linking — 3層照合エンジン

| 項目 | 状態 | 実装メモ |
|------|------|----------|
| 軟紐付け・ファイル名・未紐付け明示 | **達成** | `resolve_table_link` 等 |
| **Tier1** DAG＋データ指紋ハッシュ完全一致 | **初期達成** | `fingerprint` + `entity_link`（指紋・唯一n）。**Figスコープ**＋スクリプト resolved read 三角加点 |
| **Tier2** 統計・n 近似（ε） | **達成** | plot↔raw 統計近似 + **Legend mean±err 突合**（`_tier2_by_legend_mean`） |
| **入力ギャップ**（data 未投入） | **達成** | `data_missing` ≠ `unlinked`。カバレッジ `input_gaps` |
| **Tier3** 表記揺れ候補（LLM→機械採用） | **達成** | 規則提案 + **`propose_llm_key_aliases`**（table 完全一致のみ）。採用は機械 |
| 三角照合スコア＋採用根拠レポート | **初期達成** | n_matrix に `link_tier` / `link_status` / reason。**レポート UI に tier バッジ表示** |
| スクリプト DAG 本線リンク | **初期達成** | `.R` / `.Rmd` / **`.Rhistory`（読み取りのみ）** / **Python·ipynb** / **Prism `.pzfx`** / **KaleidaGraph `.qpd/.qpc`**。絶対パスは basename→局所表。`.pzf` は検出のみ。**偽 `.R` 生成は製品機能にしない** |
| soft FP / 競合抑制 | **初期達成** | Figパス単独不採用・groupなし近接タイ拒否・`group=all` 無料パス廃止。group一致競合は注釈維持 |
| Figスコープ（横断抑制） | **初期達成** | `path_claims_figure` — 他 Fig 表記がある経路では Fig1 エイリアス無効 |
| 顕微鏡接地 | **達成** | LIF + **CZI** XML → mag / µm/px / FOV。`input/data/confocal`・`input/data/czi` |

チェックリスト:

- [x] 軟紐付け・対応表ファイル名・未紐付け
- [x] soft FP 抑制（初期）: Figパス単独不採用 / groupなし近接タイ拒否 / `group=all` 無料パス廃止
- [x] データ指紋（数値配列／群別記述統計）による紐付け本線（初期）+ **data_missing（未投入）**
- [x] 3層スコアリング（Tier1→2→soft→**Tier3 候補キー**）と n_matrix 根拠（レポート UI バッジ表示）
- [x] Tier1/2 **Figスコープ** + スクリプト resolved read **三角加点**（横断 Fig 唯一nハイジャック防止）
- [x] 残 soft `n一致 · figパス` 抑制（n-only は claim Fig + 一意）
- [x] 候補キー接地（規則提案 → 機械スコア採用；**LLM 提案フック** `propose_llm_key_aliases`）
- [x] スクリプト DAG 本線（`.Rhistory` 読み取り・パス basename 解決）+ Python / Prism `.pzfx` / KaleidaGraph（偽 `.R` 書き出しはしない）
- [x] 顕微鏡接地（LIF + CZI XML・実データ検証・合成 CI）
- [x] 雑多 xlsx スキーマからの数値群抽出強化（空 graph シート回避・ratio(%) 復元・sentinel 100% 除外）

### 6C. n 精度・FP 抑制

- [x] n 対照表で参照ファイルが分かる／未紐付け明示
- [x] soft / 横断 Fig の誤紐付け抑制（初期・6B と重複記載）
- [x] **FP 抑制（初期）**: 明示 shared control → 全抑制；弱い記載 → `【降格】` + `severity=info`
- [x] 残 soft `n一致·figパス` 抑制（n-only は Fig claim 必須・複数ファイルなら拒否）
- [x] **正の n＝生データ行数**を Warning 理由・metadata・n 対照表注釈で一貫強調
- [x] 弱い shared-control の **【降格】** をレポートで区別（badge / フィルタ）
- [x] 出典あり（reproduced from）の画像再利用との一般化をレポート UI まで
- [x] `hybrid-llm` 列付きレポート受入再保存
- [x] 同一点列 × Legend n の説明順固定

### 6E. レポート UI / 各エンジンの精緻化 — **進行中**

> 6C 残・deferred・画像精緻化をここに集約。Taxonomy 追加より **説明可能性と FP 抑制**を先に。

#### レポート UI

| 項目 | 状態 | メモ |
|------|------|------|
| タグフィルタ・検索・根拠 `file://` | **達成** | `html_report` |
| n 対照表 + Fig ホットスポット + Legend 連動 | **達成** | |
| 正の n 注釈・降格 badge／フィルタ | **達成** | |
| link_tier バッジ（tier1/2/3/soft） | **達成（初期）** | 6B |
| 出典あり画像一致の **情報カード**（Warning 抑制のままレポート表示） | **達成** | `cited_image_matches` → coverage / HTML |
| 抽出列 `hybrid-llm` の明示表示・受入 | **達成** | `.extractor-badge.hybrid-llm` |
| 同一点列×Legend n の **説明文テンプレ固定** | **達成** | H2b reason ①②③ + `reason_order` |

#### エンジン精緻化（初期実装の次）

| 項目 | 状態 | メモ |
|------|------|------|
| planned P0–P2 初期エンジン + 合成 CI | **達成** | matrix `ci_required` |
| deferred: `P-STAT-METHOD-INCONSISTENT` / `CONFIG` / `REF` の Warning 配線 | **達成（初期）** | 明確矛盾ゲートのみ。合成 CI |
| 部分画像: LightGlue／回転・連続スケール | **達成** | 90/180/270° NCC + scale 0.55–1.0 step 0.05 + 任意 LightGlue |
| 多重比較: 対比較ペア列挙 | **達成（初期）** | `pairs` / `claimed_pairs` metadata |
| G13: 除外 ID トレース・生存曲線再計算 | **達成** | set equality（undeclared + phantom）。合成 `exclusion_id_trace` / `exclusion_id_phantom` |
| LightGlue 必須経路の閾値 | **達成（CUDA 実測）** | must_neg_max=17 / must_pos_min=153 → **`DEFAULT_MIN_MATCHES=35`**。`fixtures/gold/lightglue_calib/` + `dev_lightglue_threshold_calib.py` |
| 顕微鏡メタ接地 | **達成（LIF+CZI）** | LIF UTF-16 / CZI UTF-8 XML 直読み。実 `input/data/confocal`・`input/data/czi`。合成 `lif_meta`/`czi_meta` |

### 6D. 評価・完了条件

- [x] 軟紐付け単体テスト
- [x] `gold_eval` で H1–H4＋合成の **層別** Precision/Recall — 合成＋private_benchmark とも required_recall=1.0・gold miss 層なし。ボトルネックは gold miss ではなく **n_matrix 紐付け精度**（Legend LLM off で計測継続）
- [x] pattern_id 1:1 合成テストの CI 化（`pattern_synthetic_matrix` + `test_pattern_id_matrix`；旧 deferred 3 件も ci_required）
- [x] 複合マルチパネル PDF の bbox ゴールデン — `fixtures/gold/panel_bbox/panel_bbox_synthetic_gold.json` + `tests/test_panel_bbox_gold.py`
- [x] 配布既定プロファイルでも必須ゴールドを満たす — `effective_llm_profile_id`（MLX→7b-mlx / 無し→**7b-hf+cuda 高精度**）。CI: `test_default_profile_gold`。Mac/フル: `dev_default_profile_gold.py` / `FULL_DIST_GOLD=1`
- [x] Legend LLM **on** 再計測（GB10 / 7B）— H2b 修正後 **required_recall=1.0**。旧 3B 計測の 0.75 は解消済み

**完了条件**: (1) PDFベクター分割または同等のパネル接地 (2) スキーマ強制抽出 (3) Tier1/2 が Warning 本線で、LLM は読む／候補のみ (4) 三角照合の採用根拠がレポートに出る (5) gold_eval 層別が CI に載る。

**やらないこと**: Taxonomy だけ増やして紐付けを後回し、LLM 最終有罪判定、Fig フォルダ必須化、「≥95%」の宣伝用断定（計測はする）。

---

## Phase 7: 文献メタ＋引用整合（ユーザー提供 PDF）— **初期達成（2026-09-26）**

| 項目 | 内容 |
|------|------|
| 原稿内 | `parsers/references.py` + `engine/ref_biblio.py` — missing / duplicate / meta self-conflict |
| 引用先 PDF | `cache/cited_papers/`（H3 と分離）。CLI `--cited-papers`、WebUI「2b. 参考文献 PDF」 |
| bib↔PDF | `engine/ref_pdf_meta.py` — DOI／年／タイトル Hard Warning |
| 主張整合 | 規則 claim 抽出 + BM25 スコープ検索。根拠は HTML 情報カード。Hard は数値・極性矛盾のみ |
| 合成 CI | `fixtures/synthetic/ref_biblio` / `ref_claim` + `tests/test_ref_citation.py` |
| Taxonomy | E4 / E5（[DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md)） |

**やらないこと**: 実行時の PubMed/Crossref API、H3 `past_papers` への全文混在、LLM だけの有罪判定。

---

## 後続: 新決定論エンジン（Phase 5 カタログの実装）

Phase 5 の `planned` を **P0→P1** で合成 fixture＋エンジン化。**Phase 6 の Entity Linking が弱いと FP が増える**ため、精度基盤と並行または直後に着手する。

---

## 次の優先アクション

1. **完了**: `gold_eval --layers`（合成＋hyper: required_recall=1.0・gold miss なし）
2. **完了（初期）**: Phase 6B 指紋 Tier1/2 + `data_missing`
3. **完了**: 雑多 Excel 抽出 — hyper Fig1 raw Tier1
4. **完了**: `.Rhistory` / `.Rmd` DAG（basename 局所解決）
5. **完了**: Python / Prism `.pzfx` / KaleidaGraph
6. **完了（開発フィクスチャ）**: `from_Rhistory.R` を hyper 対応フォルダへ配置（製品は生成しない）
7. **完了（初期）**: soft 競合・誤紐付け抑制
8. **完了（初期）**: Tier1/2 Figスコープ + script三角（横断 Fig ハイジャック防止）
9. **完了（初期）**: **6C** shared-control FP 抑制（明示→抑制 / 弱い記載→降格）+ 残 soft n-only 抑制
10. **完了**: Legend LLM **on** で private_benchmark 層別再計測（CUDA/GB10）
10b. **完了（初期）**: Qwen text bakeoff + 専門家レビュー — 難しい span では **32B > 7B**。Gemma 除外
10c. **方針固定**: text 教師=`qwen2.5-32b-hf`、Mac 配布=`qwen2.5-7b-mlx`。panel 抽出ゴールド＋`dev_panel_extract_eval.py` を追加
11. **完了（初期・panel 抽出）**: hard-span + paper_01/02 + 読み取り方式固定（上表）。フル case 化は Warning／linking が要るときだけ
12. **完了（初期）**: H2b LLM on 回帰 — サイド名付き Rplot 優先 + rules lock + gold panel/evidence。OFF/ON とも required_recall=1.0
13. **完了（初期）**: 正の n＝生データ行数を Warning／n 対照表で明示 + 降格カードのレポート表示
14. **完了（初期・6A）**: PDF ベクターパネル矩形＋切り出し（`pdf_panel_geometry`）+ Legend JSON coerce。JSON mode は #16
15. **完了（初期）**: Phase 5 残 — `pattern_id` 1:1 合成 CI（matrix）+ G9/G10 エンジン初期 + `stats_recalc` 合成。deferred: STAT-METHOD / CONFIG / REF
16. **完了（6A JSON mode + Mac 受入）**: Outlines 本線配線。Mac で `outlines-mlx` + parse OK
17. **完了（初期・VLM 補助 + Mac 受入）**: ベクター空時パネル地図（`--vlm-assist`）。Mac `mlx-vlm` + 画像パネル文字合成で A/B・`n_vlm=2` OK
18. **方針**: オフライン受入は要件削除。メモリ監視は将来（製品ゲート外）。Phase 4 初期達成
19. **完了（初期・読む JSON ゴールド CI）**: `legend_json_synthetic_matrix` + `test_legend_json_gold`（Fig2A/C/M・Fig4K・Fig5E・typo）
20. **完了（typo merge 抑制）**: `_drop_llm_n_stolen_from_rules` — rules 所有 empty-group n の別パネル付け替えを drop。7B bakeoff 再計測で forbid_fp=0 を確認
21. **完了（P0 抽出キー + 本線プロファイル初期）**: `error_bar_type` / `independence_claims` / `exclusion_criteria` をスキーマ・rules・coerce に反映。配布=7B / 教師=32B / VLM=7B を確認済として固定
22. **完了（bbox ゴールデン + P-EXCLUSION-UNDECLARED 初期）**: 2×2 合成 PDF pct CI。除外未宣言は data_n>legend_n かつ exclusion 未検出で Warning（合成 `exclusion_undeclared`）
23. **完了（初期・Tier3 + レポート根拠）**: 候補キー接地（`key_normalize`・`link_tier=tier3`）+ n 対照表に tier バッジ／reason 表示。artifacts `tier3_key_candidates`
24. **完了（2026-09-25）**: planned P0 初期 5 本 — `P-IMAGE-PARTIAL-REUSE` / `P-BLOT-LANE-REUSE` / `P-VECTOR-SUBSET-UNDISCLOSED` / `P-NUMERIC-CROSSREF-MISMATCH` / `P-METHODS-CLAIM-MISMATCH`
25. **完了（2026-09-25）**: planned P1–P2 初期 5 本 — `P-ERRORBAR-SEM-SD-MISMATCH` / `P-STAT-MULTIPLICITY-GAP` / `P-SURVIVAL-COUNT-NONINTEGER` / `P-SCALE-MAG-INCONSISTENT` / `P-COUNT-N-MISMATCH`
26. **完了（2026-09-25）**: 6C 残 — 出典あり画像のレポート情報カード（`cited_image_matches`）／抽出列 `hybrid-llm` バッジ／H2b reason ①②③ 固定
27. **完了（2026-09-25）**: §6E 初期 — 顕微鏡メタ接地（sidecar）／deferred 3（STAT-METHOD・CONFIG・REF）CI／部分画像回転 NCC±LightGlue／多重比較ペア列挙／除外 ID トレース＋生存曲線再計算
28. **完了（2026-09-25）**: LIF XML 顕微鏡メタ接地 — UTF-16 ヘッダ直読み（Magnification/Objective/µm/px）。実 `input/data/confocal`（20×）＋合成 `lif_meta` CI。FOV≪scale bar も検知
29. **完了（2026-09-25）**: 配布既定プロファイル必須ゴールド — MLX 無しは `qwen2.5-7b-hf`+cuda 高精度（prefer=none 廃止）。`effective_llm_profile_id`
30. **完了（2026-09-25）**: CZI メタ接地 — UTF-8 XML（NominalMagnification/Objective/µm/px）。実 `input/data/czi`（10×）＋合成 `czi_meta` CI
31. **完了（2026-09-25）**: G13 set equality — Methods↔テーブル除外 ID 双方向（undeclared + phantom）。合成 `exclusion_id_phantom`
32. **完了（2026-09-25）**: LightGlue `DEFAULT_MIN_MATCHES=35` + `require_lightglue`；部分画像連続スケール（0.55–1.0 / 0.05）。校正: `dev_lightglue_threshold_calib.py`
33. **方針確定（2026-09-25）**: 配布本線=**7B**。大型横断 bakeoff／プロンプト再固定／LoRA は **製品不要**（32B は任意教師）
34. **完了（2026-09-25）**: 読む JSON ゴールド拡充（DEGH／N–Q／J 遺伝子型／chunk+panel_labels）+ Tier2 Legend mean 突合 + Tier3 LLM 提案フック
35. **完了（2026-09-26）**: LightGlue 実機校正（CUDA）— must_neg≤17 / must_pos≥153、rot90≈47。`DEFAULT_MIN_MATCHES=35` 確定。gold `lightglue_calib`
36. **完了（2026-09-26）**: Phase 7 初期 — 文献メタ＋引用整合（ユーザー提供 PDF）。`P-REF-MISSING-ENTRY` / `DUPLICATE-KEY` / `META-INCONSISTENT` / `PDF-META-MISMATCH` / `CLAIM-CONTRADICTION` + 根拠情報カード
36. **完了（2026-09-26）**: RW Reason マップ追記 — 照合可能 Reason→pattern、製品外は空マップ明示（生命科学 Reason 質量 ≈99.8%）
37. **完了（2026-09-26）**: H3 実コーパス適合率 — 合成 recall=1.0・出典抑制 OK・distractor FP=0。関連 PubPeer 抽出との交差=0（埋め込み図≠原稿 confocal）。`dev_h3_corpus_eval.py`
38. **完了（2026-09-26）**: Phase 7 初期 — 文献メタ＋引用整合（ユーザー提供 PDF）。`P-REF-*` bib/PDF/claim + 根拠情報カード。`tests/test_ref_citation.py`
39. **次**: ORI·COPE シード精緻化（任意）／実論文フル通し／配布受入スモーク

---

## 実装上の優先順位（迷ったときの原則）

0. **読む＝LLM、比べる＝機械**（絶対。LLM 最終有罪判定はしない）
1. **Entity Linking（データ指紋・3層照合）** — Taxonomy 増より先に精度に効く
2. **PDFベクター・パネル分割** — 複合 Fig で VLM 単独に頼らない
3. **スキーマ強制抽出** — 自由文 LLM 抽出をやめる
4. **ネタ帳（Phase 5）** — 照合方法の定義。紐付けなしにルールだけ増やさない
5. **$n$ は生データ行数を正**。未紐付け ≠ 未検出
6. **画像は同一セット内 → コーパス＋出典**；明示共有は FP 抑制
7. スクリプト DAG は加点。無いときは残渣・数字化・指紋で代替

詳細仕様は [REQUIREMENTS.md](REQUIREMENTS.md) / [ARCHITECTURE.md](ARCHITECTURE.md) / [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) §5、開発前提は [../handoff.example.md](../handoff.example.md)。

---

## 改訂履歴

| 日付 | 内容 |
|------|------|
| 2026-09-26 | **Phase 7 初期**: 文献メタ＋引用整合（ユーザー提供 PDF）。`parsers/references` / `cited_paper_ingest` / `ref_biblio` / `ref_pdf_meta` / `ref_claim`。Taxonomy E4/E5。合成 `ref_biblio`/`ref_claim` CI |
| 2026-09-26 | **LightGlue 実機校正（CUDA）**: SuperPoint+LightGlue 合成 must_neg_max=17 / must_pos_min=153（rot90 hard≈47）。`DEFAULT_MIN_MATCHES=35` 確定。`fixtures/gold/lightglue_calib` + `test_lightglue_calib` |
| 2026-09-26 | **RW Reason マップ追記**: 照合可能 Reason（Results 改ざん・出典・細胞株汚染等）を pattern 割当。調査／査読／Paper Mill 等は空マップ+note。sample CSV 拡充・CI。生命科学 Reason 質量 ≈99.8% |
| 2026-09-26 | **H3 実コーパス適合率**: `eval/h3_corpus_eval.py` + `dev_h3_corpus_eval.py`。合成 required_recall=1.0・citation_suppression OK・distractor_fp=0。`cross_match_pairs` ログ。PubPeer→`cache/past_papers` |
| 2026-09-25 | **読む JSON ゴールド拡充 + Tier2/3**: matrix に DEGH／N–Q／J／chunk+labels。`_tier2_by_legend_mean`；`propose_llm_key_aliases`（n_matrix 前採用） |
| 2026-09-25 | **方針: 配布=7B・bakeoff/LoRA 製品不要**: 横断モデル bakeoff・勝ち筋 LoRA をクローズ。次は読む JSON ゴールド拡充＋ Tier2 Legend 数値／Tier3 LLM 提案フック |
| 2026-09-25 | **G13 + LightGlue + 連続スケール**: 除外 ID set equality（phantom 合成）／`DEFAULT_MIN_MATCHES=35`・`require_lightglue`／部分画像 scale grid 0.55–1.0 step 0.05／`dev_lightglue_threshold_calib.py` |
| 2026-09-25 | **Tier3 + レポート根拠（初期）**: `key_normalize` 候補キー接地（`geneX mutant`↔`geneX-/-` 等）→ `link_tier=tier3`。n 対照表に tier バッジ／reason。artifacts `tier3_key_candidates` |
| 2026-09-25 | **bbox ゴールデン + P-EXCLUSION-UNDECLARED 初期**: `panel_bbox_synthetic_gold` CI。除外未宣言エンジン（data_n>legend_n ∧ 除外基準なし）+ 合成 `exclusion_undeclared`。catalog implemented |
| 2026-09-25 | **CZI メタ接地**: UTF-8 XML 直読み（pylibCZIrw 不要）。実 `CG15533…czi` = 10×/0.45・µm/px≈0.22。合成 `czi_meta` CI。FOV vs scale bar 共通化 |
| 2026-09-25 | **配布既定プロファイル（高精度）**: MLX 無しは `prefer=none` せず `qwen2.5-7b-hf`+cuda。`effective_llm_profile_id` / `distribution_legend_llm_kwargs`。CI は代表ケース、フルは `FULL_DIST_GOLD=1` |
| 2026-09-25 | **配布既定プロファイル必須ゴールド**: `tests/test_default_profile_gold.py` — 既定 `qwen2.5-7b-mlx`/`vl-7b`。MLX 無時 prefer=none で ci_required required_recall=1.0。`scripts/dev_default_profile_gold.py` / metrics_suite `--legend-llm` |
| 2026-09-25 | **LIF 顕微鏡メタ接地**: UTF-16 XML 直読み（readlif 不要）。実 confocal 全 LIF が 20×/0.75。合成 `lif_meta`（40× vs Legend 63×）+ FOV vs scale bar。`input/data/confocal` → private benchmark へ symlink |
| 2026-09-25 | **§6E 初期**: 顕微鏡メタ接地（`.meta.json`）／deferred 3 件 Warning+合成 CI／部分画像 90–270° NCC+任意 LightGlue／多重比較 `pairs`／除外 ID トレース＋生存曲線再計算 |
| 2026-09-25 | **6C 残完了**: 出典あり画像を `cited_image_matches`→HTML 情報カード／抽出列 `hybrid-llm` バッジ／H2b reason ①②③+`reason_order`。§6E にレポートUI・エンジン精緻化バックログ |
| 2026-09-25 | **P0 抽出キー + 本線プロファイル初期**: `error_bar_type` / `independence_claims` / `exclusion_criteria` を `LEGEND_JSON_SCHEMA`・rules 検知・coerce。配布 text=7B-mlx / CUDA=7B-hf / 教師=32B / VLM=7B |
| 2026-09-25 | **typo merge 抑制**: `_drop_llm_n_stolen_from_rules`（rules 所有 n の別パネル付け替え drop）。stub + CUDA 7B で LJ-F1-typo-kl forbid_fp=0 |
| 2026-09-25 | **読む JSON 合成ゴールド CI + bakeoff 初回**: matrix + `test_legend_json_gold`。rules 15/15。CUDA 7B/32B hybrid: expect 15/15・forbid_fp=1（LJ-F1-typo-kl）。オフライン要件削除・メモリ監視は将来 |
| 2026-09-25 | **Mac 受入**: `dev_vlm_panel_map_verify --prefer mlx --synthetic --require-vlm` → `vlm_used`・A/B・`n_vlm=2`（GenerationResult unwrap + 画像パネル文字合成） |
| 2026-09-25 | **VLM パネル地図補助（初期）**: `panel_map` / `vlm_backend` / `--vlm-assist`。ベクター空時のみ。`dev_vlm_panel_map_verify.py`。extras `vlm-mlx` / `vlm-cuda` |
| 2026-09-25 | **Mac 受入**: `dev_legend_json_mode_verify --prefer mlx --require-outlines` → `json_mode=outlines-mlx`・parse OK |
| 2026-09-25 | **6A JSON mode**: `llm/json_mode.py`（Outlines MLX/transformers → free+coerce）。`scripts/dev_legend_json_mode_verify.py`（Mac `--require-outlines`）。extra `llm-json` |
| 2026-09-25 | **pattern_id 1:1 合成 CI**: `pattern_synthetic_matrix.json` + `test_pattern_id_matrix`。G9/G10（`source_values`）+ `stats_recalc` 合成。次は 6A 残 JSON mode／VLM |
| 2026-09-25 | **6A 初期**: `pdf_panel_geometry`（ラベル→矩形→PNG crop）+ `coerce_legend_dict` スキーマ強制。artifacts `figure_panel_regions`。残: Outlines/MLX JSON mode・VLM |
| 2026-09-25 | 正の n＝生データ行数を Warning / n 対照表注釈で統一。降格 Warning をレポートで badge・フィルタ表示。次は 6A |
| 2026-09-25 | **Legend 読み取り方式を固定**: `pdftotext -raw` + 出版4型規則 + 規則優先マージ。7B 回帰 hyper 46/46・paper_01 27/27・paper_02 17/17 |
| 2026-09-24 | 別論文用 `fixtures/gold/panel_extract/` + `input/panel_extract/` と人手修正スキーマ（HUMAN_REVIEW / JSON Schema）を追加 |
| 2026-09-25 | H2b: サイド名付き Rplot・残渣フォルダ優先 + rules lock + gold の panel/evidence 照合。OFF/ON とも required_recall=1.0 |
| 2026-09-24 | panel 抽出教師の増やし方を固定: 現行 hard-span 30〜50 → 7B プロンプト寄せ → 別論文は原稿のみ薄い case → 実験データ付きフル case は後から。次アクション 11 に反映 |
| 2026-09-24 | panel 抽出: Fig2 C/J ゴールド確定。プロンプト強化 + `(k)/(l)→L/M` + dose 誤パネル抑制 + LLM欠落パネルの規則補完。**7B recall 12/12**（forbid_fp=0） |
| 2026-09-24 | **方針固定**: Legend text 教師=`Qwen2.5-32B`、Mac 配布=`7B-MLX`。panel 抽出ゴールド／`dev_panel_extract_eval` 追加。Gemma 除外。**教師ラベルは人手確認必須**（生 32B 出力のまま蒸留しない） |
| 2026-09-24 | **カタログ倫理の絶対ルール**を DETECTION / REQUIREMENTS / handoff / `catalog_policy.absolute_rules` に固定（表現を取らない・抽象化・名指し攻撃しない） |
| 2026-09-24 | Phase 5 ブラッシュアップ: 抽出プロンプト固定・共有 JSON 1.1・Issue Web Intent / PR CLI コピー（PAT 不要） |
| 2026-09-24 | Phase 5: WebUI カタログ更新に **PubPeer PDF D&D**（cache のみ）と **Issue/PR 共有**（抽象ルールのみ）を追加。DETECTION §1.4 / README 同期 |
| 2026-09-24 | 6C: shared-control 明示→抑制・弱い記載→【降格】info。soft n-only（複数ファイル / 非 claim Fig）拒否。Legend/チャンクから disclosure 収集 |
| 2026-09-24 | ROADMAP 現状同期: 6B（指紋/soft抑制/Figスコープ/script三角/多形式DAG）を初期達成として反映。次は 6C shared-control・残 soft・LLM on 再計測・6A |
| 2026-09-24 | **公式オープンソース拡充パイプライン**（RW pull / Reason mine / ORI·COPE 種 / YAML export / 日次 `catalog-pulse`）。`P-EXCLUSION-UNDECLARED` 追加。手元参照は git 外 |
| 2026-09-24 | 公開ドキュメントから個別論文・著者名を除去。対応表は gitignore の `handoff.md` へ。合成 fixture / private gold の識別名を中立 ID に改名 |
| 2026-09-24 | **`check_reference` 拡充（PubPeer 14）** → Taxonomy F / G9–G12 / `P-SOURCE-*`・`P-SURVIVAL-COUNT-NONINTEGER`・`P-METHODS-CLAIM-MISMATCH` をカタログ反映 |
| 2026-09-24 | Tier1/2 Figスコープ + script三角: 横断Figの唯一nハイジャック防止。スクリプト resolved read を優先 |
| 2026-09-24 | soft 誤紐付け抑制: Figフォルダだけでは不採用、group なし近接タイ→未紐付け、`group=all` 無料パス廃止。group 一致の競合は注釈付きで維持（demo D2） |
| 2026-09-24 | **方針**: 偽 `.R` 生成は製品化しない。開発フィクスチャとして `from_Rhistory.R` を hyper 対応フォルダへ配置（57本・非原本ヘッダ）。本線は `.R` / `.Rhistory` 読み取り |
| 2026-09-24 | Python / Prism `.pzfx` / KaleidaGraph 対応。指紋 Tier1 で Prism↔CSV。`.pzf` は検出のみ |
| 2026-09-24 | `.Rhistory`/`.Rmd` を収集・DAG 化。Drive 絶対パスを basename で局所解決。data_missing を Fig フォルダ依存から外し、hyper フル data で Fig2–5 linked |
| 2026-09-24 | 雑多 Excel 抽出: 複数シート選択・ratio(%) 復元・指紋 2dp 許容。hyper Fig1 raw が Tier1 で graph に接続（linked 7 / unlinked 2） |
| 2026-09-24 | Phase 6B 初期: `fingerprint` + `entity_link`（Tier1/2/soft）。**data_missing≠unlinked** と coverage `input_gaps`。hyper 再計測で Fig2–5 未投入を明示 |
| 2026-09-24 | private_benchmark `gold_eval --layers` 再実行: gold miss なし。ボトルネックは n_matrix 未紐付（linking カバレッジ）と判明 → Phase 6B 指紋を優先 |
| 2026-09-24 | Phase 6 を **Entity Linking / 3層照合 / PDFベクター分割 / スキーマ強制**に拡張。Taxonomy 増だけでは FP 増と明記。次アクションを gold_eval 層別→6B 指紋に更新 |
| 2026-09-24 | Phase 5 に **公式オープンソース**（RW Crossref / ORI / COPE / Bik）と **Taxonomy A–E** を接続。PubPeer スクレイプ禁止と RW 公式データ可を分離 |
| 2026-09-24 | **Phase 並べ替え**: Phase 5＝研究公正ネタ帳、旧精度向上を **Phase 6** に繰り下げ。新エンジンは後続 |
| 2026-09-24 | Phase 6（旧5）を **進行中** に更新。軟紐付け・対応表参照ファイル名・未紐付け明示を 6B/6C 達成として反映 |
| 2026-09-24 | **精度向上フェーズを追加**（当時 Phase 5）— 読む / Fig↔データ紐付け / n。DETECTION §5 と対応 |
| 2026-09-24 | **読む＝LLM、比べる＝機械**を絶対条件として REQUIREMENTS / ARCHITECTURE / DETECTION / handoff と整合 |
| 2026-09-24 | LLM/VLM **モデル選択インフラ達成**（registry / CLI / WebUI / GUI / bakeoff） |
| 2026-09-24 | Phase 3/4 Mac WebUI + MLX Qwen 受入を反映（それ以前の進捗） |
