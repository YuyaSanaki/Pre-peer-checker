# Docker 実行・学習環境

DGX Spark 上での **開発・照合実行・モデル学習の標準手順** は Docker とする（要件定義 1.2 / Handoff §2）。

エンドユーザー向け起動は Docker ではない（`./install.sh` → ローカル WebUI。Phase 3–4 / [PACKAGING.md](PACKAGING.md)）。

## 前提

- Docker Engine + **NVIDIA Container Toolkit**（`nvidia` runtime）
- ホスト GPU がコンテナから見えること（`docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`）
- アーキテクチャ: **aarch64**（DGX Spark）を第一対象。x86_64 も同一 Dockerfile でビルド可能なら許容

## イメージ

| ターゲット | 用途 | Compose サービス |
|------------|------|------------------|
| `runtime` | 照合 CLI・pytest | `run`, `test`, `shell` |
| `train` | PyTorch + imaging、学習・DINOv2 | `train`（profile: `train`） |

## ボリューム規約（コンテナ内）

| ホスト | コンテナ | 用途 |
|--------|----------|------|
| `./input` | `/data/input` (ro) | **検証対象**: 実験データ・原稿・スクリプト・Figure |
| `./outputs` | `/data/output` | HTML レポート等 |
| `./models` | `/data/models` | 重み・学習成果 |
| `./check_reference` | `/data/reference` (ro) | **照合ポイント参照資料**: PubPeer・説明資料・質問票など（検知観点の定義・評価用）。検証対象本体ではない |

未発表データはイメージに焼かず、**実行時マウントのみ**（機密要件）。

## コマンド例

```bash
# ビルド
docker compose build
docker compose --profile train build train

# 照合
docker compose run --rm run verify /data/input -o /data/output/report.html

# テスト
docker compose run --rm test

# 学習スタブ（GPU 疎通確認 → /data/models/train_stub_meta.json）
docker compose --profile train run --rm train

# 対話シェル
docker compose --profile dev run --rm shell
```

エントリポイントサブコマンド: `verify` | `train` | `test` | `shell` | `python` | 任意コマンド透過。

## ローカル実行・課金

- ビルド時のみレジストリ／PyPI へのネットワークが必要（または事前キャッシュ済みベースイメージ）。
- **推論・照合実行時に外部 API は呼ばない**（要件どおり）。
- 学習もローカル GPU のみ。クラウド学習エンドポイントは使わない。
- 未発表データはイメージに焼かず、**実行時マウントのみ**。

## トラブルシュート

- GPU が見えない → Container Toolkit / CDI（`nvidia.com/gpu=all`）を確認。
- `tree-sitter-r` ビルド失敗 → runtime は正規表現フォールバックで照合継続可。train/本番開発では Git インストール成功を確認。
- aarch64 で PyTorch CUDA wheel が無い → `train` ビルドが CPU にフォールバックする場合あり。NGC PyTorch イメージへ `CUDA_DEVEL_IMAGE` / ベース差し替えを検討。
