#!/usr/bin/env bash
# Start local WebUI and open the browser. Created/kept by install.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

if [[ ! -f "${ROOT}/.venv/bin/activate" ]]; then
  echo "ERROR: .venv がありません。先に ./install.sh を実行してください。" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${ROOT}/.venv/bin/activate"

PORT="${PRE_PEER_CHECKER_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"
export PRE_PEER_CHECKER_PORT="${PORT}"

(
  sleep 1.2
  if command -v open >/dev/null 2>&1; then
    open "${URL}" || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${URL}" || true
  fi
) &

exec pre-peer-checker-web
