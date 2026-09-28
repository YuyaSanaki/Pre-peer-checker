# Pre-peer-checker

> [!WARNING]
> **現在開発中（アルファ版）です。** 仕様・CLI・出力形式は予告なく変わります。検知精度は検証途中であり、Warning が出ないことは「問題なし」を意味しません。出力は投稿前セルフチェックの補助として扱い、最終判断は必ず人が行ってください。

論文投稿前（peer review 前）の研究データ・解析コード・原稿・Figure の整合性を、**完全ローカル**で自動検証するソフトウェアです。

外部 API 課金なし・完全ローカル（原稿やデータを外部に送信しません）。開発は DGX Spark、利用は **clone →** `./install.sh` **→ ショートカットで WebUI**（Apple Silicon Mac / Linux）。

**開発の絶対条件**: **読む＝LLM/VLM**（Legend・Fig チャンク → チェック項目 JSON）、**比べる＝機械**（表・DAG・画像類似度で Warning 確定）。詳細は [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) §1.2.1。

## コラボレーターの方へ（精度検証へのご協力のお願い）

いまは手元の原稿・データで試していただき、**検知漏れ（見逃し）と誤検知** を集めて精度を上げる段階です。

1. 下記「セットアップ」でインストールし、WebUI または CLI で自分の原稿フォルダを照合する（処理はすべて手元のマシン内で完結します）。
2. 結果を Slack などで教えてください。次の情報があると助かります。
  - 使用環境（OS / Mac or Linux / GPU 有無）と、LLM/VLM 補助の ON/OFF
  - 誤検知: Warning のタグ・`pattern_id`・何が正しかったか
  - 見逃し: 本来拾ってほしかった不整合の種類（例: Legend の n と生データ行数の不一致）
  - エラー時: コンソールのエラーメッセージ
3. **未発表の原稿・生データ・画像・個人情報はそのまま送らないでください。** 共有する場合は、数値や名前を伏せた最小の再現例（合成データ）にしてください。

再現用の合成データは `[fixtures/synthetic/](fixtures/synthetic/)` にあります。追加してほしい検知パターンの提案も歓迎します（[docs/DETECTION_AND_MODELS.md](docs/DETECTION_AND_MODELS.md) の `pattern_id` 体系を参照）。

## ドキュメント

- [Handoff 骨子（公開）](handoff.example.md) — 詳細な個別ケース名はローカル `handoff.md`（gitignore）
- [要件定義書](docs/REQUIREMENTS.md)
- [技術設計](docs/ARCHITECTURE.md)
- [検知観点 & モデル選定・精度指針](docs/DETECTION_AND_MODELS.md)（§1.4: カタログ拡充 — RW/ORI/COPE・PDF D&D・Issue/PR）
- [ロードマップ](docs/ROADMAP.md)（Phase 0–2 コア完了、Phase 4 配布受入、**Phase 5 ネタ帳初版済**、**Phase 6 精度向上・進行中**、**Phase 7 文献メタ＋引用整合・初期達成**）
- [Fixtures（汎用パターン / ゴールド）](fixtures/README.md)
- [Docker 実行・学習](docs/DOCKER.md)
- [配布・起動（WebUI）](docs/PACKAGING.md)



## 現状


| モジュール       | 内容                                                                                    |
| ----------- | ------------------------------------------------------------------------------------- |
| `parsers/`  | Python AST / R tree-sitter / YAML / Word / PDF / Fig チャンク / 参考文献・本文 cite / 引用先 PDF 取込 |
| `data/`     | Excel・CSV 群推定と統計再計算（比べる）                                                              |
| `engine/`   | 決定論照合（n・群ベクトル・統計・参照・参考文献メタ・引用主張）                                                      |
| `imaging/`  | CZI/LIF ローダ、画像重複スキャン（軽量フォールバック + DINOv2 口）                                            |
| `llm/`      | ローカル LLM/VLM（読む本線）+ モデルレジストリ                                                          |
| `catalog/`  | 照合カタログ拡充（RW pulse・PubPeer PDF 取込・Issue/PR 共有）                                         |
| `pipeline/` | オーケストレーション                                                                            |
| `report/`   | HTML Warning レポート（n 対照表・カバレッジ）                                                        |
| `web/`      | ローカル WebUI（照合本線＋カタログ更新タブ）                                                             |




## セットアップ（おすすめ・非エンジニア向け）

```bash
git clone https://github.com/YuyaSanaki/Pre-peer-checker.git
cd Pre-peer-checker
./install.sh
# Mac: Pre-peer-checker.command をダブルクリック
# Linux: scripts/start_webui.sh
```

Apple Silicon Mac では `install.sh` だけで MLX（Legend LLM / VLM）・画像スタック・既定モデルの重み（計 ~10GB）まで導入し、最後に機能チェックを表示します。途中で失敗した場合も `./install.sh` を再実行すれば不足分だけ補います。

**Git を使わない場合**: GitHub のリポジトリページで「Code → Download ZIP」を選ぶか、開発者から受け取った ZIP を展開し、ターミナルで展開したフォルダ（例: `Pre-peer-checker-main`）に移動して `bash install.sh` を実行してください。以降の起動方法は同じです。
![Untitled.001](docs/fig/Untitled.001.png)

![Untitled.002](docs/fig/Untitled.002.png)

![Untitled.003](docs/fig/Untitled.003.png)

![Untitled.004](docs/fig/Untitled.004.png)

ブラウザで親フォルダを選びます。親の中に必須:

```text
MyCase/
  manuscript/   # 原稿・Figure
  data/         # 表・生データ・スクリプト
```

**原稿は Word（`.docx`）を推奨します。** PDF（proof 原稿など）でも照合できますが、精度が落ちる可能性があります。現状、Figure Legend の n・本文中の統計記載・参考文献の読み取りは Word 原稿が対象で、PDF だけの場合は主に図（パネル・埋め込み画像）の照合になります。Figure PDF は Word 原稿と併せて置いてください。

詳細は docs/PACKAGING.md。

![Untitled.005](docs/fig/Untitled.005.png)

データ量にもよりますが10分から1時間くらいかかります。





### Docker（開発・学習）

```bash
docker compose build
docker compose run --rm test
docker compose run --rm run verify /data/input -o /data/output/report.html
# GPU 学習スタブ:
docker compose --profile train build train
docker compose --profile train run --rm train
```

手順の詳細は [docs/DOCKER.md](docs/DOCKER.md)。

### ホスト venv（開発）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web]"
# 画像・顕微鏡フォーマットが必要なら:
# pip install -e ".[dev,imaging]"
# R tree-sitter 本線（language-pack）:
# pip install -e ".[dev,r-ast]"
```



## 使い方（CLI）

```bash
pre-peer-checker path/to/manuscript_folder -o outputs/report.html
# H3: 過去論文画像コーパスを指定
pre-peer-checker path/to/manuscript --corpus path/to/past_figures -o outputs/report.html --json outputs/warnings.json
# Legend LLM 補助（任意・MLX または CUDA transformers）:
# pip install -e ".[mlx]"          # Mac
# pip install -e ".[llm-cuda]"     # DGX Spark / Linux GPU
# pip install -e ".[llm-json]"     # Outlines（JSON schema 強制。未導入時は free+coerce）
# pip install -e ".[vlm-mlx]"      # Mac: パネル地図補助（mlx-vlm）
# pip install -e ".[vlm-cuda]"     # Linux GPU: Qwen2.5-VL
# pre-peer-checker ... --legend-llm --legend-llm-prefer auto
# pre-peer-checker ... --vlm-assist --vlm-prefer auto --vlm-profile qwen2.5-vl-7b-mlx
# Mac JSON mode 受入:
#   python scripts/dev_legend_json_mode_verify.py --prefer mlx --require-outlines
# Mac VLM パネル地図（ラベル無し PDF で補助経路）:
#   python scripts/dev_vlm_panel_map_verify.py --prefer mlx --synthetic --require-vlm
# auto: Mac→MLX、CUDA 利用可→transformers(CUDA)。MLX 既定プロファイルでも GPU ホストでは HF にフォールバック。
# Spark: TORCH_DISABLE_NATIVE_JIT=1 はバックエンドが自動設定（Triton 再ビルド不要）。
# 明示例: --legend-llm-prefer cuda --llm-profile qwen2.5-7b-hf
```

ローカル WebUI（本線）:

```bash
pip install -e ".[web]"
pre-peer-checker-web
# → http://127.0.0.1:8765
```

WebUI の **照合**タブでは、過去論文 PDF を D&D してローカルコーパス（`cache/past_papers/`）に登録できます。図を自動抽出し、照合時に選択した論文だけを H3 画像再利用検知に使います（マシン内再利用のみ・共有なし）。CLI の `--corpus`（画像フォルダ指定）と同等のエンジン経路です。

```bash
# CLI でも同じエンジン（画像フォルダを直接指定）
pre-peer-checker path/to/manuscript --corpus path/to/past_figures -o outputs/report.html
```

WebUI の **カタログ更新**タブでは次ができます（PubPeer サイトの自動取得はしません）:

1. ブラウザで保存した PubPeer PDF を D&D → `cache/` のみに取込・抽象ルール抽出
2. RW 等の公式ソース pull → ローカル LLM 仕分け → 人手キュレーション → Apply（照合用アクティブカタログ）
3. 抽象ルール案を **GitHub Issue 下書き URL**（Web Intent・トークン不要）で開くか、**PR 用 CLI** をコピー（自動 push しない。PDF・コメント全文は含めない）

CLI でも RW パルス等:

```bash
python -m pre_peer_checker.catalog --help
# 例: pulse / pull-rw / mine / propose / export-yaml
```

- **過去論文コーパス（H3）**: WebUI 照合タブ / `imaging.past_paper_ingest`（図抽出・ローカルライブラリ）
- **参考文献 PDF（引用整合）**: WebUI 照合タブ「2b」 / `parsers.cited_paper_ingest`（テキスト・チャンク抽出・ローカルライブラリ）
- **PubPeer カタログ（ネタ帳）**: WebUI カタログ更新タブ / `catalog.pubpeer_ingest` / `catalog.share`（抽象ルールのみ共有可）



## 参考文献メタ＋引用整合チェック

原稿の参考文献リストに誤りがないか、また「〜が示されている [12]」のような引用文が引用先論文の内容と合っているかを確認します。引用先論文の中身は**ユーザーが渡した PDF だけ**を使い、PubMed や Crossref などへのネットワーク問い合わせはしません。

チェックは 2 段階です。

1. **原稿だけで行うチェック（常時）** — Word 原稿の `References` 節と、本文中の引用（`[1]`、`[1,2]`、`[3–5]`、`(Smith et al., 2020)`）を突き合わせます。
2. **引用先 PDF を渡したときのチェック（任意）** — 参考文献の各エントリを PDF に紐付け（DOI 完全一致 → タイトル＋年 → ファイル名の順）、メタデータと本文を照合します。


| pattern_id                  | 内容                                      | 必要な入力        | 出力                           |
| --------------------------- | --------------------------------------- | ------------ | ---------------------------- |
| `P-REF-MISSING-ENTRY`       | 本文で引用しているキーが References に無い             | 原稿           | Warning                      |
| `P-REF-DUPLICATE-KEY`       | References に同じ番号・キーが重複している              | 原稿           | Warning                      |
| `P-REF-META-INCONSISTENT`   | 1 つのエントリ内で年が食い違う、DOI はあるのに著者もタイトルも読めない  | 原稿           | Warning                      |
| `P-REF-ORPHAN-ENTRY`        | References にあるが本文で一度も引用されていない           | 原稿           | カバレッジに情報表示（既定で Warning にしない） |
| `P-REF-PDF-META-MISMATCH`   | 参考文献の DOI・年・タイトルが、紐付いた PDF のものと食い違う     | 原稿 + 引用先 PDF | Warning                      |
| `P-REF-CLAIM-CONTRADICTION` | 引用文の主張（数値・増加／減少）が、引用先 PDF の該当箇所と明確に矛盾する | 原稿 + 引用先 PDF | Warning                      |


引用文と論文内容の整合は、次の流れで見ます。

- 本文の引用を含む文を「主張」として取り出し、その引用キーに紐付いた PDF の中だけから関連箇所を検索します（BM25）。別の論文の文章を根拠に取り違えないよう、原稿全体の PDF を横断検索はしません。
- 見つかった関連箇所は HTML レポートの **「引用根拠レビュー（情報）」カード**に、主張の文と並べて表示します。ここは人が読んで判断するための材料で、Warning ではありません。
- Warning になるのは、「本文は 50% 増加、PDF は 10% 減少」のように数値や増減の向きが機械的に矛盾すると判定できた場合だけです。LLM の判断だけで Warning を確定することはありません（**読む＝LLM、比べる＝機械**の原則どおり）。

引用先 PDF は `cache/cited_papers/` にだけ保存されます（git 外・共有なし）。H3 の過去論文画像コーパス（`cache/past_papers/`）とは別の置き場です。同じ内容の PDF を再度渡した場合は既存の登録を再利用します。

```bash
# 原稿だけで参考文献メタをチェック（引用先 PDF 不要・既定で実行）
pre-peer-checker path/to/case -o outputs/report.html

# 引用先 PDF（ファイルまたはフォルダ）を渡して、メタ突合と引用根拠レビューも実行
pre-peer-checker path/to/case --cited-papers path/to/reference_pdfs -o outputs/report.html
```

WebUI では照合タブの **「2b. 参考文献 PDF」**に PDF をドロップして登録し、照合時にチェックの入った PDF が使われます。

現時点の制限:

- 参考文献の読み取りは Word 原稿（`.docx`）の `References` 見出し以降が対象です。PDF 原稿の参考文献リストはまだ読みません。
- 番号付き・author–year 形式の一般的な書式に対応した規則ベースの読み取りです。雑誌固有の特殊な書式では取りこぼすことがあります。
- 引用先 PDF のタイトル・年・DOI は先頭ページからの推定です。スキャン PDF などテキストが取れないものは紐付けできません。
- 紐付けできなかった参考文献は Warning にせず、カバレッジに「未紐付け」として件数を出します。

PyQt GUI（任意・開発用）:

```bash
pip install -e ".[gui]"
pre-peer-checker-gui
```



## レイアウト検証 / 任意スモーク

```bash
./scripts/verify_packaging_layout.sh

# 任意: 凍結 CLI スモーク（配布本線ではない）
pip install -e '.[packaging]'
./scripts/build_linux_smoke.sh
```

詳細は [docs/PACKAGING.md](docs/PACKAGING.md)。

## Warning 細分類

出力はすべて Warning。タグ例: `[データ取り違え]` `[サンプルサイズ記載誤記]` `[画像重複・再利用（要出典確認）]` など（詳細は要件定義書）。参考文献メタ・引用整合の Warning は `[表記揺れ・参照不整合]` タグで出ます。

## ライセンス・第三者コンポーネント

本ソフトウェア（`pre-peer-checker`）自体のソースコードは **MIT**（[LICENSE](LICENSE)）です。依存パッケージとモデル重みはリポジトリに同梱せず、`install.sh` / `pip` / `torch.hub` を通じて利用者の環境へ各配布元から取得します。以下は無料配布・論文化を想定した 2026-09 時点の整理です（商用化時は別途再確認）。各パッケージ／モデルの正式条件は上流の LICENSE を正とし、本節は法的助言ではありません。

### PyMuPDF（AGPL-3.0）に関する条項

PDF のテキスト・図の抽出に使う **PyMuPDF（`fitz`）はコア依存**で、すべてのインストールに含まれます。PyMuPDF は **GNU AGPL-3.0 と Artifex 商用ライセンスのデュアルライセンス**です。

- 本リポジトリの MIT コードは AGPL-3.0 と両立します。**ソースを公開し、依存は利用者が各自取得する現行の配布方針**では、追加の対応は不要です。
- PyMuPDF を含めた**結合物を配布する場合**（PyInstaller の凍結バイナリ、`.app`、PyMuPDF をインストール済みの Docker イメージ、依存をまとめたアーカイブ等）は、結合物全体が AGPL-3.0 の条件を受けます。具体的には、対応するソースコードの提供、AGPL-3.0 全文の同梱、同一ライセンスでの再配布が必要です。
- PyMuPDF と結合した本ソフトを**改変し、WebUI 等でネットワーク越しに第三者へ提供する場合**は、AGPL-3.0 §13 により利用者へのソース提供義務が生じます（既定の `127.0.0.1` での個人ローカル利用は該当しません）。
- **クローズドソースまたは商用で配布・提供する場合**は、Artifex から商用ライセンスを取得するか、PDF 処理を permissive なライブラリ（例: pypdfium2 = Apache-2.0 / BSD-3-Clause、pypdf = BSD-3-Clause）へ置き換えてください。
- fork・改変して再配布する場合も上記の条件を引き継ぎます。リポジトリが MIT であることは、PyMuPDF を含む成果物をクローズドで配布できることを意味しません。

### 画像照合モデル（DINOv2 / LightGlue / SuperPoint）

- **DINOv2**: コード・標準重みとも Apache-2.0 で、MIT と両立します。実行時に `torch.hub.load("facebookresearch/dinov2", ...)` で取得し、同梱はしません。重みを再配布する場合は上流の LICENSE を添付してください。
- **LightGlue**: コードと学習済み重みは Apache-2.0 で、MIT と両立します。
- **SuperPoint / ALIKED（利用区分で切替）**: SuperPoint の推論コード（`lightglue/superpoint.py`）と重みは **Magic Leap の「大学・非営利組織による非商用研究」限定ライセンス**です。配布者が非商用でも、利用者が営利組織（企業の研究所・製薬企業・CRO など）であれば対象外になります。そこで `./install.sh` の最初に利用区分を選択し、LightGlue の特徴点抽出器を切り替えます。

  | 利用区分 | 特徴点抽出器 | ライセンス | 判定しきい値（一致点数） |
  | --- | --- | --- | --- |
  | 1) 大学・非営利組織による非商用研究 | SuperPoint | Magic Leap（非商用研究） | 35 |
  | 2) 上記以外（企業・商用研究・判断がつかない場合） | ALIKED | BSD-3-Clause（LightGlue 重みは Apache-2.0） | 50 |

  - 選択はリポジトリ直下の `usage_profile.json`（git 管理外）に保存され、`./install.sh` の再実行で変更できます。環境変数 `PRE_PEER_CHECKER_USAGE=academic|commercial` で上書き・非対話実行も可能です。未選択・判別不能のときは 2）ALIKED 側に倒します。
  - 区分は利用者の自己申告であり、条件の遵守は利用者の責任です。SuperPoint のファイル自体は LightGlue パッケージに同梱されるため 2) でもディスク上には存在しますが、読み込み・重みの取得は行いません。
  - しきい値は合成ペアで抽出器ごとに較正しています（`fixtures/gold/lightglue_calib/calib_summary.json`）。ALIKED は 90° 回転した切り抜きを一致点数だけでは無関係ペアと分離できないため、回転を伴う再利用の確定力は SuperPoint より弱くなります。
- **LightGlue のバージョン固定**: `install.sh` は LightGlue をコミット `eb42fee`（`LIGHTGLUE_COMMIT`）に固定して導入します。更新する場合は上流の LICENSE を確認し、`scripts/dev_lightglue_threshold_calib.py --features {superpoint,aliked}` で再較正してください。
- LightGlue は任意機能です。未導入の場合、精密照合は OpenCV ORB + RANSAC（Apache-2.0）→ 正規化相互相関（追加依存なし）へフォールバックします。

### ローカルモデル（任意・補助）


| 役割                    | モデル                                                                        | ライセンス                                                 | 備考                               |
| --------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------- | -------------------------------- |
| Legend→JSON 等（Mac 本線） | Qwen2.5-7B-Instruct（MLX 4-bit 例: `mlx-community/Qwen2.5-7B-Instruct-4bit`） | Apache 2.0                                            | Alibaba Cloud / Qwen             |
| Fig 接地（配布本命）          | Qwen2.5-VL-7B-Instruct（mlx-vlm）                                            | Apache 2.0                                            | パネル境界・軸ラベル・グラフ種別                 |
| Legend→JSON 等（開発・教師）    | Qwen2.5-32B-Instruct                                                       | Apache 2.0                                            | DGX ゴールド作成用                      |
| Fig 接地（開発・教師）         | Qwen2.5-VL-32B-Instruct                                                    | Apache 2.0                                            | DGX ゴールド・7B 蒸留／LoRA 用            |
| 画像類似スクリーニング           | DINOv2（例: `dinov2_vits14`）                                                 | Apache 2.0                                            | Meta。標準重み。派生チェックポイントは別ライセンスの場合あり |
| 画像ペア精密照合（任意）          | LightGlue + SuperPoint または ALIKED                                           | LightGlue: Apache 2.0 / SuperPoint: Magic Leap（非商用研究） / ALIKED: BSD-3 | install.sh の利用区分で切替（上記）          |

モデルプロファイル（`llm/model_registry.yaml`）には Apache-2.0 のモデルだけを載せています。Qwen2.5-3B（Qwen Research License）・Qwen2.5-72B（Qwen License）・InternVL3 は選択肢から外しました。


推論ランタイム: **MLX / mlx-lm**（MIT、Apple Silicon）、**PyTorch + transformers**（BSD / Apache、CUDA 経路）。

### コア依存（常時）


| パッケージ                             | 用途             | 代表的ライセンス                                       |
| --------------------------------- | -------------- | ---------------------------------------------- |
| numpy, pandas, scipy, statsmodels | 数値・統計再計算       | BSD 系                                          |
| openpyxl                          | Excel          | MIT                                            |
| ruamel.yaml                       | YAML           | MIT                                            |
| python-docx                       | Word           | MIT                                            |
| **PyMuPDF**                       | PDF テキスト・埋め込み図 | **AGPL-3.0**（または Artifex 商用）。上記「PyMuPDF に関する条項」参照 |
| Pillow                            | 画像 I/O         | MIT-CMU（HPND 系）                                 |
| Jinja2                            | HTML レポート      | BSD-3-Clause                                   |
| nbformat                          | Jupyter        | BSD-3-Clause                                   |




### オプショナル依存（`pyproject.toml` extras）


| Extra       | 主なパッケージ                                                      | 用途             | 代表的ライセンス            |
| ----------- | ------------------------------------------------------------ | -------------- | ------------------- |
| `web`       | FastAPI, uvicorn                                             | ローカル WebUI     | MIT / BSD           |
| `gui`       | PyQt6                                                        | 任意 GUI         | **GPL-3.0** / Riverbank 商用 |
| `imaging`   | torch, torchvision, OpenCV, aicsimageio                      | 重複スキャン・顕微鏡     | BSD / Apache 等（各上流） |
| `imaging`   | **readlif**                                                  | Leica LIF 読込    | **GPL-3.0**         |
| `imaging`   | **pylibCZIrw**                                               | Zeiss CZI 読込    | **LGPL-3.0**        |
| `r-ast`     | tree-sitter, tree-sitter-language-pack                       | R AST          | MIT                 |
| `mlx`       | mlx, mlx-lm                                                  | Mac LLM        | MIT                 |
| `llm-cuda`  | torch, transformers, accelerate                              | Linux/CUDA LLM | BSD / Apache        |
| `packaging` | PyInstaller                                                  | 凍結スモーク（配布本線外）  | GPL（リンク例外あり）        |
| `dev`       | pytest, ruff, httpx                                          | 開発             | MIT                 |


LightGlue は PyPI に無いため、`install.sh` が上流のコミット固定 zip から導入します（Apple Silicon）。

### 配布・論文での扱い（現状方針）

| 配布形態                                        | 主な制約                                                                                                     |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| ソース公開（clone → `./install.sh`、現行本線）            | 追加義務なし。依存とモデルは利用者が各自取得                                                                                  |
| 凍結バイナリ・Docker イメージ等、依存込みの結合物を配布                  | PyMuPDF により全体が AGPL-3.0。readlif・PyQt6 を含めば GPL-3.0、pylibCZIrw は LGPL-3.0（差し替え可能性の確保）。各 LICENSE を同梱 |
| クローズドソース・商用                                  | PyMuPDF の商用ライセンス取得または置換、SuperPoint 不使用（ALIKED 固定）、readlif・PyQt6 の除外または置換                   |

- **無料配布**: ソース公開（clone → `./install.sh`）を本線とし、利用者が依存とモデルを各自取得する形を推奨。
- **論文化**: Methods / Acknowledgments にモデル名・主要ライブラリとライセンス（および必要なら論文引用）を記載。
- **商用・クローズド配布**を将来検討する場合は、とくに PyMuPDF（AGPL）、SuperPoint（非商用）、readlif / PyQt6（GPL）を再点検してください。

