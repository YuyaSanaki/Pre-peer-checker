# 人手修正スキーム（panel extract）

目的: 抽出案を人が直し、**確認済みラベルだけ**を `panel_extract_gold.json` にする。

**paper_01 / paper_02**: 32B 先行ではなく **Cursor 対話でマニュアル・キュレーション**する（PDF + `legend_excerpt.txt` + draft を見ながら、エージェントが提案 → 人が OK/修正 → gold へ追記）。

## ファイル役割

| ファイル | 誰が書く | 意味 |
|----------|----------|------|
| `panel_extract_draft.json` | シード（pdftotext／将来は 32B） | 未確定。`review.status=draft` / `in_review` |
| `panel_extract_gold.json` | 人（Cursor 対話で確定） | `review.status=confirmed` のみ評価に使う |

どちらも **gitignore**。追跡するのは `*.example.json` と本ドキュメント。

## 1 項目（item）の必須キー

採点キーは **`(figure, panel, group, n)`**（`dev_panel_extract_eval.py` と同じ）。

| キー | 型 | 説明 |
|------|-----|------|
| `id` | string | 一意 ID。例: `PE-F2-C-early-control` |
| `figure` | string | `"Figure 2"` 形式（スペースあり） |
| `panel` | string | 大文字 1 文字（意図パネル。typo 面は別欄） |
| `group` | string | 群ラベル。パネル単位 n なら `""` |
| `n` | int \| null | legend 記載の n。無い／曖昧なら `null` にして `drop` 候補 |
| `difficulty` | string | 下記タグから 1 つ |
| `review_status` | string | `pending` → `confirmed` / `dropped` |

## 任意キー（あると直しやすい）

| キー | 説明 |
|------|------|
| `aliases_group` | 表記揺れ許容（`"mutx"` ↔ `"mutx+/-"`） |
| `legend_typo` | 表面テキストの誤りメモ（例: `(k) written for panel L`） |
| `forbid_panels` | 誤って出てはいけないパネル文字（例: dose を A にしない → `["A"]`） |
| `evidence_span` | legend からの短い根拠引用（**長い原文は避ける**） |
| `n_scope` | `per_group` \| `total` \| `per_genotype` \| `unknown` |
| `model_raw` | draft のみ。32B が出した生の group/panel（差分確認用） |
| `human_note` | 修正理由の一行メモ |

## difficulty タグ（推奨語彙）

| 値 | 典型 |
|----|------|
| `context_typo` | `(k)/(l)` → L/M など文脈補正 |
| `multi_group` | 1 パネルに複数 genotype |
| `multi_group_timepoint` | 時系列×条件 |
| `four_way_group` | 2×2 以上の交差群 |
| `complex_genotype` | `mutx -/-; UAS-…` など長い genotype |
| `panel_letter` | セクション文字とパネル文字の取り違えやすい箇所 |
| `shared_n` | `n=10 (D, E, G and H)` 型 / `f,g … N = 57` |
| `other` | 上記以外（`human_note` 必須） |

## 出版 PDF の書き方 4 型（paper gold）

採点未達がここに集中する。規則側で先に切り、7B はその行を上書きしない。

| 型 | 本文の形 | gold の取り方 |
|----|----------|---------------|
| パネル単位の N | `N = 3 dishes` / `N = 88 sarcomere fibers` | 節パネル、**group 空**（単位語は群にしない） |
| 時点リスト | `N = 15 (day 3), …` / `14 images … (day 10)` | 同じパネルに day ごとの行 |
| 小文字の共有 N | `f,g … N = 57` | F と G の両方、group 空 |
| 後置の `(n = )` | `(A) … (n = 5)` / `mice (n = 5) and patients (n = 6)` / `(RNA-seq, n = 3)` | 節パネル。括弧内が条件のときだけ group |

### 判断ルール（キュレーションで確定したもの）

- **代表画像でも legend が n を明示していれば付ける**（`(B) Representative images … (n = 5)` や `b,c … N = …` の共有表記は両パネルに展開）。模式図に付いた n は誤記として扱い、意図したパネルに移す。
- ブロット単独のパネルは、legend がそのパネル自身の n を書いていてレーン数と合うなら付ける。n が隣の定量パネルを指しているなら付けない。
- 画像と定量が同じパネル内にある（例: ゲル＋右側の統計）場合は、**統計の n** を採る。
- legend の n が明らかに別パネルのもの（例: 模式図やブロットに付いているが、本来は隣の定量パネル）なら、意図したパネルに移し `legend_typo` に記録する。
- 図の点の数と legend の n が食い違い、図側が正しいと判断したら、図側の値を採り `legend_typo` に記録する。

キャプションは **`pdftotext -raw`（段の順）** で切る。layout 抽出の左右混線は使わない。

**読み取り本線（2026-09-25 固定）**: 上表 4 型は規則が先 → 7B は空きだけ → 規則行は上書きしない。詳細は [docs/ROADMAP.md](../../../docs/ROADMAP.md)「Legend 読み取り方式」。

## レビュー手順（1 論文）

1. PDF / Word から **Figure legend / caption** を読む（切り出し `legend_excerpt.txt` 推奨・git 外）
2. draft の各 item を開き、**(figure, panel, group, n)** が legend と一致するか確認
3. 合っていれば `review_status: confirmed`。群表記だけ揺れるなら `aliases_group` を足す
4. モデル／シードがパネルを取り違えていれば直す。必要なら `forbid_panels` / `legend_typo`
5. 曖昧・caption に n が無い → `review_status: dropped`（gold の `items` から外すか残して dropped）
6. ケース先頭の `review.status` を `confirmed` にし、`reviewed_by` / `reviewed_at` を記入
7. **confirmed だけ**を評価・将来の蒸留教師に使う

### Cursor 対話キュレーション（paper_01 / paper_02）

1. Fig 単位でエージェントが **提案表**（confirm / fix / drop）を出す
2. 人が「OK」「n は 6」「D の patients は drop」など返す
3. 合意分だけ `panel_extract_gold.json` に追記（draft の `review_status` も更新）
4. 全 hard-span が終わったらケース `review.status=confirmed`

## ケース先頭の `review` オブジェクト

```json
"review": {
  "status": "confirmed",
  "teacher_model": "qwen2.5-32b-hf",
  "reviewed_by": "initials-or-handle",
  "reviewed_at": "2026-09-24",
  "target_hard_spans": "10-20"
}
```

`status`: `draft` | `in_review` | `confirmed`

## 倫理（短い）

- example / git 追跡ファイルに **論文名、著者、DOI、PDF、長い引用を書かない**
- 手元 PDF は `input/panel_extract/<case_id>/` のみ
