# Panel-extract thin cases（別論文・抽出教師）

Legend → `(figure, panel, group, n)` の抽出品質用。**Warning gold / 実験データは不要。**  
原稿 Word でも出版論文 PDF でも可（caption / legend テキストが取れればよい）。

| パス | git | 内容 |
|------|-----|------|
| `_template/` | 追跡 | 新規 case のコピー元 |
| `paper_01/` / `paper_02/` | 追跡（雛形のみ） | 予約スロット。filled は ignore |
| `*/panel_extract_gold.example.json` | 追跡 | 人手確定 gold のスキーマ |
| `*/panel_extract_draft.example.json` | 追跡 | 32B 生出力→人が直す前の下書きスキーマ |
| `*/panel_extract_gold.json` / `*_draft.json` | **ignore** | 実 n・識別可能なパネル対応 |
| `input/panel_extract/<case_id>/` | **ignore**（README のみ追跡可） | PDF / docx / 切り出し legend テキスト |

正本スキーマ: [`panel_extract_gold.schema.json`](panel_extract_gold.schema.json)  
人手手順の詳細: [`HUMAN_REVIEW.md`](HUMAN_REVIEW.md)

各 case の `case_manifest` に `split: dev | holdout` がある。paper_01 / paper_02 は dev（ルール作りに使用済み）。**未見論文は holdout** として `scripts/dev_holdout_prepare.py --case paper_NN --new` で作り、ブラインドで gold を確定してから `scripts/dev_generalization_eval.py` で測る（[`HUMAN_REVIEW.md`「holdout（汎化評価）」](HUMAN_REVIEW.md#holdout汎化評価)）。

## 新規論文を足す

```bash
# 1) スロットを増やす（paper_03 など）
cp -r fixtures/gold/panel_extract/_template fixtures/gold/panel_extract/paper_03

# 2) 素材を置く（git 外）
mkdir -p input/panel_extract/paper_03
# 出版 PDF または Word、必要なら legend_excerpt.txt を配置

# 3) case_id を編集
#    fixtures/gold/panel_extract/paper_03/*.example.json の case_id / source

# 4) 32B で抽出 → draft に保存（コミットしない）
cp fixtures/gold/panel_extract/paper_03/panel_extract_draft.example.json \
   fixtures/gold/panel_extract/paper_03/panel_extract_draft.json
# …モデル出力を items[] に流し込み、review.status は "draft"

# 5) 人が直して gold へ（目安 hard-span 10〜20）
cp fixtures/gold/panel_extract/paper_03/panel_extract_gold.example.json \
   fixtures/gold/panel_extract/paper_03/panel_extract_gold.json
# draft を見ながら確定。各 item の review_status を confirmed に

# 6) 評価
python scripts/dev_panel_extract_eval.py \
  --gold fixtures/gold/panel_extract/paper_03/panel_extract_gold.json \
  --warnings outputs/metrics/<run>_warnings.json \
  -o outputs/metrics/panel_extract_paper_03_eval.json
```

## やらないこと

- PDF・長い legend 原文・DOI／論文名を **git 追跡の example に書かない**
- 生の 32B 出力をそのまま gold / 蒸留教師にしない（`HUMAN_REVIEW.md`）
- この薄い case に実験データフォルダを無理に揃えない（フル case 化は後から）
