# Handoff (example / public stub)

ローカル開発の詳細引き継ぎは **`handoff.md`（gitignore・push しない）** に置く。
本ファイルはリポジトリに残す **公開用の骨子**だけを示す。

## 絶対条件（公開）

1. 完全ローカル・API 課金ゼロ
2. 読む＝LLM/VLM、比べる＝機械
3. カタログ倫理: 表現を取らない / 検査仕様に抽象化 / コメント者を名指し・攻撃しない

詳細は `docs/REQUIREMENTS.md` / `docs/DETECTION_AND_MODELS.md` / `docs/ROADMAP.md`。

## ローカルで用意するもの（gitignore）

| パス | 役割 |
|------|------|
| `handoff.md` | 個別ケース名・パス・評価コマンドの非公開メモ |
| `input/private_benchmark/` | プライベート benchmark（実データ） |
| `check_reference/` | 手元参照 PDF 等 |
| `fixtures/gold/private_benchmark/*.json`（filled） | 実データ由来ゴールド（`case_manifest.json` の `focus_inputs` で入力スライス指定） |
| `rules/local_case_profile.json` | ケース語彙プロファイル（実験系トークン・パネルヒント。`docs/ARCHITECTURE.md` 参照） |
| `tests/private/` | 実データ前提の回帰テスト |

```bash
cp handoff.example.md handoff.md   # 初回のみ。以降 handoff.md を編集（コミットしない）
# 必要なら: ln -s <local_case_dir> input/private_benchmark
```

## CI で使う公開回帰

```bash
python -m pre_peer_checker.eval.gold_eval --case demo --run --input fixtures/synthetic/demo
python -m pre_peer_checker.eval.gold_eval --case cross_fig_reuse --run --input fixtures/synthetic/cross_fig_reuse
pytest -q
```
