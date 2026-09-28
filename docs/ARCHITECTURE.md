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
- 要件正本: [REQUIREMENTS.md](REQUIREMENTS.md) §1.2.1 / [DETECTION_AND_MODELS.md](DETECTION_AND_MODELS.md) §4–§5 / [../handoff.example.md](../handoff.example.md)。

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
