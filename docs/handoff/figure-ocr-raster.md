# ラスタ Figure OCR — 現状と Qwen3-VL 将来対応

**目的**: 文字が画像に焼き込まれた出版 Figure（Cell / Nature 系）から、パネル文字（A/B/C…）や図中の `n=` などを拾い、Legend・本文照合の **読む** 側ヒントにする。判定（Warning 確定）は従来どおり決定論。

**更新**: 2026-09-29（quick ベンチ 4 枚・private `input/panel_extract/`）

---

## 現状で OK とした方針

| 環境 | 図中 OCR（パネル crop 後） | パネル layout | Legend（テキスト） |
| --- | --- | --- | --- |
| **macOS** | **Apple Vision** | Florence-2（CPU/MPS） | Qwen2.5-7B（MLX 本線） |
| **Linux + GPU（Spark 等）** | **Florence-2** OCR | 同スクリプト内 Florence | Qwen2.5-7B（transformers / CUDA） |
| **製品 VLM 補助** | 対象外（別タスク） | — | Qwen2.5-VL（`--vlm-assist`・ベクター分割が空のときのみ） |

**quick ベンチ figPanel recall（ラスタ正解）**: Vision（Mac, panels）≈ Florence（Spark, full）≈ **82.1%**。内訳は `tmp/figure_ocr_bench/` の `preds_*` + `score`（gitignore）。

**Qwen2.5-VL / Qwen3-VL（ベンチ脚本）**: Spark aarch64 では推論未達（OOM / `ConstTensorWrapper` 等）。**本番必須にしない**。Legend の Qwen（**テキスト 7B**）が Spark で動くことと、ベンチの **VL** は別モデル・別コード経路である点に注意（[会話整理](#legend-と-vl-の混同を避ける)）。

**取り込み未実装**: ベンチ結果は `scripts/dev_figure_ocr_bench.py` のみ。製品の `figure_panel_labels` / チャンク `[figure_panel_labels]` への配線は **別 PR**。

---

## 開発用ベンチ（リポジトリにあり）

```text
scripts/dev_figure_ocr_bench.py   prepare | subset | run | score
scripts/run_figure_ocr_bench_quick.sh      # Spark: Florence + Qwen 試行
scripts/run_figure_ocr_panels_quick.sh     # Spark: panels + Qwen
scripts/run_figure_ocr_mac_quick.sh        # Mac: Vision + panels layout
```

- データ: `input/panel_extract/paper_01|02/*.pdf` → `tmp/figure_ocr_bench/`（images, gt, preds）
- プロファイル: `quick`（4）/ `raster`（10）/ `full`（42）
- Mac panels 実行時: **`BENCH_DEVICE=cpu` または `mps`**（layout 用 Florence が CUDA 固定だと Mac で落ちる）

Spark → Mac へ正解・pred 共有:

```bash
rsync -avz spark-host:~/20260922Pre-peer-checker/tmp/figure_ocr_bench/ tmp/figure_ocr_bench/
```

---

## 将来: OCR 精度向上のため Qwen3-VL を使えるようにする

**動機**: Vision / Florence で ~82% の figPanel でも、難関 fig（小文字・密集・低コントラスト）や図中 `n=` の取りこぼしが残る。Apache-2.0 の **Qwen3-VL** が panels crop 上で **有意に上回る** ことがベンチで確認できたら、オプションエンジンまたは Mac/Linux フォールバックの候補にする。

**やること（優先順）**

1. **製品経路に寄せる**  
   ベンチ専用 `QwenVL` 直読みではなく、`pre_peer_checker.llm.vlm_backend.TransformersVlmBackend`（および Mac なら `MlxVlmBackend`）＋ `accel.load_pretrained` / `TORCHDYNAMO_DISABLE` 等、Legend LLM と同系の hardening を **図 OCR 用アダプタ** から呼ぶ。プロンプトはベンチの JSON spot 形式と製品の `[figure_panel_labels]` 用途を分離して定義。

2. **Qwen3-VL の評価環境**  
   - **第一候補: Pegasus `gen_S` + H100（x86_64）** — Spark aarch64 で出た VL テンソル不具合を避け、メモリ余裕で 8B を試す。Pegasus の作業ディレクトリ（`<work_dir>/ocr_bench/`）へ `tmp/figure_ocr_bench` + 脚本を rsync する流れは過去実績あり。  
   - **Spark**: torch / transformers の組み合わせ更新後、`--layout panels` + `qwen3vl` で quick 再 score。low_mem 時は registry 方針どおり **4B**（`BENCH_VLM_LOW_MEM=1`）。

3. **ベンチ合格基準（prod 昇格前）**  
   - `profile quick` で **figPanel recall > 現行 Vision/Florence**、かつ **extra（余分パネル文字）が許容範囲**（現 Vision extra=7 vs Florence full extra=3 を参考）。  
   - `profile raster`（10 枚）で regress なし。  
   - 出力は **needs review** 扱い（VLM 幻覚リスク）。Warning 確定は決定論のまま。

4. **製品への配線**  
   - ラスタ Figure PDF: ベクター `_panel_labels` が空 → 現状は VLM **パネル地図** のみ。将来は **panels layout cache**（Florence）→ **Qwen3-VL OCR on crop**（または Vision on Mac）を `collect_panel_labels_by_figure` / チャンク添付に統合。  
   - `llm/model_registry.yaml` に **Apache-2.0** の Qwen3-VL プロファイルを追加（配布既定は 7B テキスト/VLM 本線をいきなり差し替えない）。

5. **やらないこと**  
   - Spark 上のベンチ脚本だけ直して「本番 VL が動く」とみなさない。  
   - Qwen3-VL を Legend テキスト 7B の代替にしない（役割が違う）。

**参照コード**

- ベンチ: `scripts/dev_figure_ocr_bench.py`（`QwenVL`, `--layout panels`）
- 製品 VLM 補助: `src/pre_peer_checker/llm/vlm_backend.py`, `panel_map_assist.py`
- パネル文字（ベクター）: `src/pre_peer_checker/parsers/figure_panel_labels.py`

---

## Legend と VL の混同を避ける

| | Legend Qwen | 製品 VLM 補助 | OCR ベンチ Qwen-VL |
| --- | --- | --- | --- |
| モデル | Qwen2.5-**7B Instruct**（テキスト） | Qwen2.5-**VL**-7B | Qwen2.5/3-**VL** |
| Spark | ✅ `TransformersBackend` | ベクター Fig では **often 未起動** | ❌ 2026-09 時点 |
| 入力 | 原稿テキスト | 出版 Fig PDF（1 ページ地図） | 300 dpi ラスタ / crop |
| 出力 | Legend JSON | パネル bbox JSON | bbox + 文字 OCR JSON |

---

## 関連

- [DETECTION_AND_MODELS.md §2.5 `panel_map`](../DETECTION_AND_MODELS.md) — VLM はベクター失敗時の補助
- [ARCHITECTURE.md §0](../ARCHITECTURE.md) — 読む＝LLM/VLM、比べる＝機械
- Git: OCR ベンチ脚本 `d3fa6c4` 以降 `main`
