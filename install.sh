#!/usr/bin/env bash
# One-shot setup for non-programmers: clone → ./install.sh → double-click launcher.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PORT="${PRE_PEER_CHECKER_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

echo "==> Pre-peer-checker セットアップ"
echo "    リポジトリ: ${ROOT}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 が見つかりません。Python 3.11 以上をインストールしてください。" >&2
  exit 1
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "ERROR: Python 3.11 以上が必要です（現在: ${PY_VER}）。" >&2
  exit 1
}

if [[ ! -d .venv ]]; then
  echo "==> .venv を作成中…"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
echo "==> パッケージをインストール中（web extras）…"
pip install -e '.[web]'

chmod +x "${ROOT}/scripts/start_webui.sh"

# macOS double-clickable launcher (repo-relative via start_webui.sh)
COMMAND_LAUNCHER="${ROOT}/Pre-peer-checker.command"
cat > "${COMMAND_LAUNCHER}" <<EOF
#!/usr/bin/env bash
cd "${ROOT}"
exec "${ROOT}/scripts/start_webui.sh"
EOF
chmod +x "${COMMAND_LAUNCHER}"

# Linux .desktop (may need to be marked trusted once)
DESKTOP_LAUNCHER="${ROOT}/Pre-peer-checker.desktop"
cat > "${DESKTOP_LAUNCHER}" <<EOF
[Desktop Entry]
Type=Application
Name=Pre-peer-checker
Comment=Local manuscript / data verification
Exec=${ROOT}/scripts/start_webui.sh
Path=${ROOT}
Terminal=true
Categories=Science;Education;
EOF
chmod +x "${DESKTOP_LAUNCHER}"

echo
echo "==> セットアップ完了"
echo "    次回からは次をダブルクリックしてください:"
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "      ${COMMAND_LAUNCHER}"
else
  echo "      ${ROOT}/scripts/start_webui.sh"
  echo "      または ${DESKTOP_LAUNCHER}"
fi
echo "    ブラウザで ${URL} が開きます。"
echo
echo "利用者向け: 親フォルダに manuscript/ と data/ を用意し、WebUI でその親を選んで「照合開始」。"
