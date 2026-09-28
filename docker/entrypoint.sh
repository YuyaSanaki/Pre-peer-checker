#!/usr/bin/env bash
# Container entrypoint — 実行 / 学習 / テスト / shell
set -euo pipefail

cmd="${1:-verify}"
shift || true

case "$cmd" in
  verify|run|check)
    # 用法: docker compose run --rm run verify /data/input -o /data/output/report.html
    if [[ $# -eq 0 ]]; then
      set -- /data/input -o /data/output/report.html
    fi
    exec pre-peer-checker "$@"
    ;;
  train|learn)
    exec python /app/docker/train_stub.py "$@"
    ;;
  test|pytest)
    exec pytest -q "$@"
    ;;
  shell|bash)
    exec bash "$@"
    ;;
  python)
    exec python "$@"
    ;;
  *)
    # 任意コマンド透過（例: pre-peer-checker ...）
    exec "$cmd" "$@"
    ;;
esac
