#!/usr/bin/env bash
# Spark 等ローカル GPU: 4 枚 quick プロファイルで OCR 比較（目安 30–45 分）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/tmp/figure_ocr_bench"
PY="${PY:-${ROOT}/.venv/bin/python}"
export BENCH_DEVICE="${BENCH_DEVICE:-cuda}"

cd "$ROOT"
test -f "$OUT/gt.json" || "$PY" scripts/dev_figure_ocr_bench.py prepare --profile full
"$PY" scripts/dev_figure_ocr_bench.py subset --profile quick

# Spark (aarch64): Paddle 省略。Qwen3 8B+300dpi は OOM しやすい → 25vl + 3vl-4B（low mem）のみ。
if [[ "$(uname -m)" == "aarch64" ]]; then
  export BENCH_VLM_LOW_MEM=1 BENCH_VLM_MAX_SIDE="${BENCH_VLM_MAX_SIDE:-1280}"
  ENGINES="${ENGINES:-florence2 qwen25vl qwen3vl}"
else
  ENGINES="${ENGINES:-qwen25vl qwen3vl florence2}"
fi
LOG="${OUT}/spark_quick.log"
exec > >(tee -a "$LOG") 2>&1
echo "START $(date -Iseconds) ENGINES=$ENGINES"
for e in $ENGINES; do
  echo "=== $e ==="
  "$PY" scripts/dev_figure_ocr_bench.py run --profile quick --engine "$e"
done
"$PY" scripts/dev_figure_ocr_bench.py score --profile quick
