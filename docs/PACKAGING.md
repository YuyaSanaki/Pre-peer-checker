# 配布・起動（ローカル WebUI）

エンドユーザー向けの本線は **`.dmg` ではなく**、リポジトリを clone したうえで **`./install.sh` → ショートカットでローカル WebUI** です。  
Apple Developer Program / コード署名 / 公証は **不要**です。

開発機（DGX Spark）では CLI・pytest・（任意）Linux 凍結スモークも使えます。

## 成果物の役割

| 成果物 | 用途 |
|--------|------|
| `./install.sh` | 初回セットアップ（`.venv` + `[web]` + 起動ランチャ） |
| `Pre-peer-checker.command`（Mac） / `scripts/start_webui.sh` | ダブルクリック起動 |
| ブラウザ `http://127.0.0.1:8765` | 親フォルダ選択 → 照合 → HTML レポート |
| `dist/pre-peer-checker/`（CLI onedir） | **任意**・開発用の凍結 CLI スモーク（PyInstaller） |

## 1. 初回セットアップ（非エンジニア向け手順）

前提: Git と Python 3.11+ が入っていること（詳しい人が一度用意してもよい）。

```bash
git clone https://github.com/YuyaSanaki/Pre-peer-checker.git
cd Pre-peer-checker
./install.sh
```

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
| `./install.sh` | **初回のみ**必要 | `pip install -e '.[web]'` |
| ショートカット起動・照合 | **不要** | 完全ローカル |
| `docker compose run … verify` | **不要**（イメージ構築時のみ必要だった） | 照合はローカル |
