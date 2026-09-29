# 技術設計・推論エンジン

## 0. 絶対条件 — 読む＝LLM、比べる＝機械

```
  ┌─────────────────────────────────────────────────────────┐
  │ 読む（抽出・文脈・接地）          比べる（照合・Warning） │
  │  ローカル LLM / VLM 本線           決定論エンジンのみ     │
  │  Legend / Figチャンク / パネル地図   n・ベクトル・DAG・類似度 │
  │  → チェック項目 JSON               → 説明可能な Warning   │
  └─────────────────────────────────────────────────────────┘
```

- **読む**: 原稿の意味（パネル区切り、n、群ラベル 1x/a/b、出典）は LLM/VLM。規則の無限追加は禁止（フォールバックのみ）。
- **比べる**: Warning の確定根拠は決定論シグナルのみ。LLM に「不整合か」を最終判定させない。
- 要件正本: [REQUIREMENTS.md](REQUIREMENTS.md) §1.2.1 / [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) §4–§5。

## 設計ルール

### 汎用性（benchmark に過適合させない）

- `input/private_benchmark/` と `check_reference/` は開発・回帰の **基準ケース（benchmark）** であり、エンジンをこのデータに合わせ込まない。
- 照合ルールは汎用パターン `fixtures/patterns/pubpeer_patterns.json`（`P-*`）に、ケース固有の期待は `fixtures/gold/<case>/` に分離する。論文固有の正規表現を足し続けない。
- git に入れるのは匿名の合成 fixture（`fixtures/synthetic/`）のみ。実論文の図・数値・コメント原文は入れない。

### 実装の優先順位（迷ったときの原則）

0. **読む＝LLM、比べる＝機械**（絶対。LLM に最終有罪判定をさせない）
1. **Entity Linking（データ指紋・3層照合）** — Taxonomy を増やすより先に精度に効く
2. **PDF ベクター・パネル分割** — 複合 Fig で VLM 単独に頼らない
3. **スキーマ強制抽出** — 自由文の LLM 抽出に頼らない
4. **ネタ帳（照合方法の定義）** — 紐付けなしにルールだけ増やさない（FP が増えるだけ）
5. **$n$ は生データ行数を正**とする。未紐付け ≠ 未検出
6. **画像は同一セット内 → コーパス＋出典**の順。明示された共有は FP として抑制する
7. スクリプト DAG は加点要素。無いときは残渣・数字化・指紋で代替する

### モデル方針

- 配布本線は **7B**（text: `qwen2.5-7b-mlx` / `qwen2.5-7b-hf`、VLM: `qwen2.5-vl-7b`）。開発既定と配布既定は分離し、配布既定をいきなり大型モデルにしない。
- 32B は難しい span のラベル作り用の任意教師。教師ラベルは人手確認必須（生の 32B 出力をそのまま蒸留に使わない）。
- LoRA／蒸留は製品必須にしない。7B がスキーマ強制＋規則でも新しい論文種で繰り返し破綻したときだけ検討する。
- モデルプロファイル（`llm/model_registry.yaml`）には Apache-2.0 のモデルだけを載せる。

### やらないこと

- 「有罪／不正スコア」を学習させる end-to-end 分類器、DINOv2 のフル FT
- LLM 出力だけを根拠にした Warning 確定（最終有罪判定）
- PubPeer／Retraction Watch 記事サイトの自動スクレイプ、カタログ倫理の絶対ルール違反（[DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) 冒頭）
- 実行時の PubMed / Crossref などへの外部 API 問い合わせ（引用先はユーザー提供 PDF のみ）
- カタログの自動 merge、ルールの二重ファイル管理（正本は JSON、YAML は export）
- Taxonomy だけ増やして紐付けを後回しにすること
- Fig フォルダの必須化、偽 `.R` スクリプトの生成（`.R` / `.Rhistory` は読み取りのみ）
- 「≥95%」のような宣伝用の精度断定（計測はする）

## 全体アーキテクチャ

```
                  ┌────────────────────────────────────────────────────────┐
                  │                   入力ファイル群                       │
                  │   - R, Python, YAML, Excel, CSV, CZI, LIF, Word, PDF   │
                  └──────────────────────────┬─────────────────────────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
         【読む: ローカル LLM/VLM】                  【比べる素材: 決定論解析】
         - Figチャンク（Legend/Results/Methods）    - Python AST / tree-sitter-r
         - Legend → チェック項目 JSON               - 表・群ベクトル・統計再計算
         - VLM: Fig パネル地図・ラベル補助           - DINOv2 / LightGlue / Bioformats
                       │                                           │
                       └─────────────────────┬─────────────────────┘
                                             ▼
                                【決定論 照合・Warning】
                                - n 突合 / 点列同一性 / DAG / 画像類似
                                             │
                                             ▼
                                【WebUI / HTML レポート】
```

## 層別コンポーネント

### スクリプト・設定解析層（比べる素材）
- **Python**: 標準 `ast`（`pd.read_csv`, `sns.boxplot` 等の引数追跡）
- **R**: `tree-sitter-r`（`read.csv` → `ggplot` 依存関係の静的抽出）
- **YAML**: `ruamel.yaml`（群定義・パラメータ解析）

### 生データ・統計再計算層（比べる素材）
- `scipy.stats`, `statsmodels`
- 記述統計、Student/Welch t-test、One-way ANOVA + Tukey/Dunnett、Mann-Whitney U 等

### 画像照合層（比べる素材）
- `pylibCZIrw` (.czi), `readlif` / `aicsimageio` (.lif)
- DINOv2 (ViT-B/14) + LightGlue（PyTorch CUDA / MPS）

### 意味抽出層（読む＝LLM/VLM 本線）
- LLM: Qwen2.5-7B-Instruct（プロファイル選択可）— Legend / Figチャンク → チェック項目 JSON
- VLM: **Qwen2.5-VL-7B**（配布）／**32B**（DGX 教師）— パネル境界・軸ラベル・グラフ種別の視覚コンテキスト（PyMuPDF ベクター分割を優先）
- 規則パーサ: LLM 未導入・失敗時のフォールバックのみ

#### Legend 読み取り方式

| 段 | 内容 |
|----|------|
| 切出し | 出版 PDF は **`pdftotext -raw`**（段の順）。layout 抽出の左右混線は使わない |
| 規則 | 括弧の中身でパネル／群を分類。出版 4 型（パネル単位 `N=` / 時点リスト / 共有小文字 / 後置 `(n=)`）を `legend_struct` が先に取る |
| 7B | 規則が空だった文だけ埋める。**規則行は上書きしない**（空 group・同一パネルの別 n も残す） |
| 採点 | hard-span gold の `(figure, panel, group, n)`。7B の余分行は許容ノイズ（Warning 確定には使わない） |

回帰の入口: `scripts/dev_panel_extract_eval.py` / `scripts/dev_panel_extract_paper_eval.py`。出版 PDF 向けにプロンプトを足して既存ケースの括弧分類を崩さないこと。

**選定根拠・検知観点・精度向上**: [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md)

## パッケージ構成

```
pre_peer_checker/
├── warnings/       # Warning 型・細分類タグ
├── parsers/        # Python / R / YAML / Word / PDF / Figチャンク
├── data/           # 表データ読込・統計再計算
├── imaging/        # CZI/LIF・DINOv2 重複・過去論文 PDF→コーパス取込
├── llm/            # ローカル LLM/VLM（読む本線）+ モデルレジストリ
├── report/         # HTML レポート生成
└── pipeline/       # オーケストレーション
gui/                # PyQt6（任意）
web/                # ローカル WebUI（配布本線）
```

### ケース語彙プロファイル（`engine/case_profile.py`）

「このファイル／パネルはどの実験系（サイド）に属するか」を使う照合（作図↔表の取り違え、統計残渣↔表、ggsave 名↔作図データ、H2b）は、語彙をコードに埋め込まず **プロファイル** から読む。

- 既定: 合成 fixture 用の中立語彙（`alphaexp` / `betaexp`）のみ。パネル文字→サイドの推定はしない（特定の図レイアウトへの過適合防止）。
- ローカル拡張（git 外）: `rules/local_case_profile.json`、または環境変数 `PRE_PEER_CHECKER_CASE_PROFILE` でパス指定（`none` で既定のみ）。スキーマはモジュール docstring を参照。
- テスト: `tests/conftest.py` が既定プロファイルを強制。サイド依存の合成テストは `sided_profile` fixture を使う。`tests/private/`（git 外）はローカルプロファイルで実データ回帰を行う。

ホスト側キャッシュ（git 外）: `cache/catalog_active/`（照合カタログ）、`cache/past_papers/`（H3 用・過去論文 PDF から抽出した図。マシン内再利用のみ）、`cache/cited_papers/`（引用整合用・参考文献 PDF のテキスト／チャンク。H3 と分離）。
## Docker（DGX Spark 開発・学習）

| イメージターゲット | 役割 |
|--------------------|------|
| `runtime` | 照合 CLI・pytest |
| `train` | PyTorch + imaging。DINOv2 / 閾値校正・（例外時のみ）LoRA |

ホストの `input/`（検証対象）・`check_reference/`（照合ポイント参照）・`outputs/`・`models/` を `/data/*` にマウント。手順は [DOCKER.md](DOCKER.md)。

- **`input/`**: 実験データと原稿（照合される側）
- **`check_reference/`**: 本ソフトが拾うべき不整合ポイントの参照資料（PubPeer・説明資料等）。評価・観点設計用
