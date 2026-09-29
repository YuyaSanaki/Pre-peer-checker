#!/usr/bin/env bash
# macOS: Apple Vision（crop OCR）+ 任意で Florence-2。input/ の 2 論文 PDF が要る。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/tmp/figure_ocr_bench"
PY="${PY:-${ROOT}/.venv/bin/python}"

cd "$ROOT"
test -f "$OUT/gt.json" || "$PY" scripts/dev_figure_ocr_bench.py prepare --profile full
"$PY" scripts/dev_figure_ocr_bench.py subset --profile quick

# Vision は macOS のみ。レイアウトは Florence（CPU/MPS）でパネル crop。
export BENCH_VLM_MAX_SIDE="${BENCH_VLM_MAX_SIDE:-1280}"
ENGINES="${ENGINES:-vision}"
LAYOUT="${LAYOUT:-panels}"

LOG="${OUT}/mac_quick.log"
exec > >(tee -a "$LOG") 2>&1
echo "START $(date -Iseconds) layout=$LAYOUT engines=$ENGINES"
for e in $ENGINES; do
  echo "=== $e ==="
  "$PY" scripts/dev_figure_ocr_bench.py run --profile quick --layout "$LAYOUT" --engine "$e"
done
"$PY" scripts/dev_figure_ocr_bench.py score --profile quick --layout "$LAYOUT"
echo "DONE $(date -Iseconds)"
