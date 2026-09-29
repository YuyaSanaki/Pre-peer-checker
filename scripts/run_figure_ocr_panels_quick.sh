#!/usr/bin/env bash
# quick 4 枚 · パネル crop 経由（Florence でレイアウト → 各エンジンが crop OCR）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/tmp/figure_ocr_bench"
PY="${PY:-${ROOT}/.venv/bin/python}"
export BENCH_DEVICE="${BENCH_DEVICE:-cuda}"
export BENCH_VLM_LOW_MEM="${BENCH_VLM_LOW_MEM:-1}"
export BENCH_VLM_MAX_SIDE="${BENCH_VLM_MAX_SIDE:-1280}"
export BENCH_VLM_MAX_NEW="${BENCH_VLM_MAX_NEW:-2048}"

cd "$ROOT"
"$PY" scripts/dev_figure_ocr_bench.py subset --profile quick

LOG="${OUT}/spark_panels_quick.log"
exec > >(tee -a "$LOG") 2>&1
echo "START $(date -Iseconds) layout=panels profile=quick"

for e in qwen25vl qwen3vl; do
  echo "=== $e ==="
  "$PY" scripts/dev_figure_ocr_bench.py run --profile quick --layout panels --engine "$e"
done
"$PY" scripts/dev_figure_ocr_bench.py score --profile quick --layout panels
echo "DONE $(date -Iseconds)"
