# private_benchmark（dev・Word 原稿）

汎化評価（`scripts/dev_generalization_eval.py`）に private benchmark を dev として載せるためのスロット。

- Legend gold は `fixtures/gold/private_benchmark/panel_extract_gold.json`（gitignore）をそのまま使う（`gold_paths.legend`）。ここに複製しない。
- 入力: `input/panel_extract/private_benchmark/` に最終原稿の `.docx` を 1 本（symlink 可）。

```bash
mkdir -p input/panel_extract/private_benchmark
ln -s "../../private_benchmark/Manuscript/word/<final manuscript>.docx" input/panel_extract/private_benchmark/
```
