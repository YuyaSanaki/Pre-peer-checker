# 配布・起動（ローカル WebUI）

エンドユーザー向けの本線は **`.dmg` ではなく**、リポジトリを clone したうえで **`./install.sh` → ショートカットでローカル WebUI** です。  
Apple Developer Program / コード署名 / 公証は **不要**です。

開発機（DGX Spark）では CLI・pytest・（任意）Linux 凍結スモークも使えます。

## 成果物の役割

| 成果物 | 用途 |
|--------|------|
| `./install.sh` | 初回セットアップ（`.venv` + 依存 + 既定モデル + 起動ランチャ + 機能チェック）。再実行で不足分を補う |
| `Pre-peer-checker.command`（Mac） / `scripts/start_webui.sh` | ダブルクリック起動 |
| ブラウザ `http://127.0.0.1:8765` | 親フォルダ選択 → 照合 → HTML レポート |
| `dist/pre-peer-checker/`（CLI onedir） | **任意**・開発用の凍結 CLI スモーク（PyInstaller） |

## 1. 初回セットアップ（非エンジニア向け手順）

前提: Git が入っていること（ZIP で導入する場合は不要）。Python 3.11+ が無ければ `install.sh` が [uv](https://docs.astral.sh/uv/) 経由で Python 3.12 を取得します（システムの Python は変更しません）。

```bash
git clone https://github.com/YuyaSanaki/Pre-peer-checker.git
cd Pre-peer-checker
./install.sh
```

Git を使わない場合は、GitHub のリポジトリページの「Code → Download ZIP」または開発者から受け取った ZIP を展開し、展開したフォルダ（例: `Pre-peer-checker-main`）で `bash install.sh` を実行します。`install.sh` と起動ランチャは Git に依存しません。

`install.sh` が OS を判定して導入する内容:

| 環境 | 導入内容 |
|------|----------|
| **Apple Silicon Mac** | WebUI・**MLX Legend LLM**（必須。失敗時はエラー終了）・mlx-vlm（VLM パネル地図補助）・Outlines（JSON スキーマ強制）・tree-sitter（R 解析）・torch / torchvision / OpenCV / LightGlue（画像重複・精密照合）・readlif / pylibCZIrw（顕微鏡）。さらに既定モデル（`Qwen2.5-7B-Instruct-4bit` ~4.3GB、`Qwen2.5-VL-7B-Instruct-4bit` ~5.6GB、DINOv2、LightGlue）を事前取得 |
| Linux | WebUI・tree-sitter。GPU 推論は `PRE_PEER_CHECKER_EXTRAS=llm-cuda,vlm-cuda ./install.sh` |

最後に機能チェック（`scripts/setup_check.py`）が各機能の `[OK]` / `[--]` とモデル重みの取得状況を表示します。任意機能の導入に失敗しても続行し、再実行で補えます。

| 環境変数 | 効果 |
|----------|------|
| `PRE_PEER_CHECKER_SKIP_MODELS=1` | モデル重みの事前取得を省略（WebUI 初回実行時に取得） |
| `PRE_PEER_CHECKER_EXTRAS=dev,gui` | 追加 extras を導入 |
| `PRE_PEER_CHECKER_PYTHON=/path/to/python3` | 使用する Python を明示 |

Rosetta（x86_64）のターミナルから実行しても arm64 で自動的に再実行します。既存 `.venv` が古い Python や x86_64 で作られていた場合は作り直します。

完了後:

- **Mac**: `Pre-peer-checker.command` をダブルクリック
- **Linux**: `scripts/start_webui.sh` または `Pre-peer-checker.desktop`

ブラウザが開き、照合 UI が表示されます。

## 2. 利用者が用意するフォルダ

親フォルダを 1 つ選びます。必須サブフォルダ:

```text
MyCase/
  manuscript/   # Word・Figure PDF など（.zip 可・自動展開）
  data/         # xlsx・生データ・スクリプトなど（.zip 可・自動展開）
```

WebUI で親（`MyCase`）を選択 →「照合開始」。欠けていると日本語でエラーになります。

## 3. 開発者向けレイアウト検証

```bash
./scripts/verify_packaging_layout.sh
```

`install.sh`・WebUI エントリ・パターン JSON の存在と import を確認します（PyPI オフライン可、ただし `.venv` は事前に必要）。

## 4. （任意）Linux CLI 凍結スモーク

配布本線ではありません。凍結 import の回帰用:

```bash
source .venv/bin/activate
pip install -e '.[packaging]'
./scripts/build_linux_smoke.sh
```

凍結成果物（`dist/`）には PyMuPDF（AGPL-3.0）が同梱されます。第三者へ配布する場合は、AGPL-3.0 の条件（対応ソースの提供・ライセンス全文の同梱）に従ってください。詳細は README の「ライセンス・第三者コンポーネント」節を参照。

## 5. 廃止したもの

- Mac `.app` / `.dmg` 配布本線
- Developer ID 署名・notarytool 公証

レガシー骨格（`scripts/build_macos.sh`、`packaging/pyinstaller/pre-peer-checker-gui.spec` 等）はリポジトリに残っていますが **非推奨**です。日常起動は WebUI を使ってください。

## 6. PyQt GUI について

`pre-peer-checker-gui`（PyQt6）は開発・任意利用向けに残しています。非エンジニア向け本線は **WebUI** です。

```bash
pip install -e '.[gui]'
pre-peer-checker-gui
```

## FAQ: Docker に PyPI を入れる必要はあるか？

**いいえ。** Docker 経路は開発・学習用です。エンドユーザーの WebUI 経路はホストの `.venv` のみで完結します。

| 場面 | PyPI / ネット | 説明 |
|------|---------------|------|
| `./install.sh` | **初回のみ**必要 | 依存パッケージと既定モデル重みを取得 |
| ショートカット起動・照合 | **不要** | 完全ローカル |
| `docker compose run … verify` | **不要**（イメージ構築時のみ必要だった） | 照合はローカル |
