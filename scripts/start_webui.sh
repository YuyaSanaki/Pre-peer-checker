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

open_url() {
  if command -v open >/dev/null 2>&1; then
    open "$1" || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$1" || true
  fi
}

port_in_use() {
  python - "$1" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(0)
finally:
    s.close()
sys.exit(1)
PY
}

is_our_webui() {
  python - "$1" <<'PY'
import sys, urllib.request
try:
    html = urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/", timeout=2).read(4096)
except Exception:
    sys.exit(1)
sys.exit(0 if b"<title>Pre-peer-checker</title>" in html else 1)
PY
}

PORT="${PRE_PEER_CHECKER_PORT:-8765}"
if port_in_use "${PORT}"; then
  if is_our_webui "${PORT}"; then
    echo "Pre-peer-checker WebUI は既に起動しています → http://127.0.0.1:${PORT}"
    open_url "http://127.0.0.1:${PORT}"
    exit 0
  fi
  echo "ポート ${PORT} は別のプロセスが使用中です（旧バージョンの WebUI 等）。空きポートを探します…"
  for candidate in $(seq $((PORT + 1)) $((PORT + 20))); do
    if ! port_in_use "${candidate}"; then
      PORT="${candidate}"
      break
    fi
  done
  if port_in_use "${PORT}"; then
    echo "ERROR: 空きポートが見つかりません。PRE_PEER_CHECKER_PORT で指定してください。" >&2
    exit 1
  fi
fi

URL="http://127.0.0.1:${PORT}"
export PRE_PEER_CHECKER_PORT="${PORT}"

(
  sleep 1.2
  open_url "${URL}"
) &

exec pre-peer-checker-web
