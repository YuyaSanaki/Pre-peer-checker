# Slot template

holdout（未見論文）は準備スクリプトで作る:

```bash
mkdir -p input/panel_extract/paper_XX   # PDF を 1 本置く
python scripts/dev_holdout_prepare.py --case paper_XX --new
```

手作業でコピーする場合（dev 用など）:

```bash
cp -r fixtures/gold/panel_extract/_template fixtures/gold/panel_extract/paper_XX
# then replace REPLACE_WITH_SLOT_ID → paper_XX in the example JSON files, set "split"
mkdir -p input/panel_extract/paper_XX
```

See [../README.md](../README.md) and [../HUMAN_REVIEW.md](../HUMAN_REVIEW.md).
