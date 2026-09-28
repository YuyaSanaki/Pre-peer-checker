#!/usr/bin/env bash
# Offline check — WebUI / install layout present (no PyPI).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

need=(
  install.sh
  scripts/start_webui.sh
  docs/PACKAGING.md
  src/pre_peer_checker/resources/pubpeer_patterns.json
  src/pre_peer_checker/cli.py
  src/pre_peer_checker/web/app.py
  src/pre_peer_checker/web/case_layout.py
  src/pre_peer_checker/web/templates/index.html
)
for f in "${need[@]}"; do
  test -f "$f" || { echo "MISSING: $f"; exit 1; }
done

test -x install.sh || { echo "NOT EXECUTABLE: install.sh"; exit 1; }
test -x scripts/start_webui.sh || { echo "NOT EXECUTABLE: scripts/start_webui.sh"; exit 1; }

# shellcheck disable=SC1091
source .venv/bin/activate
python - <<'PY'
from pre_peer_checker.resources import patterns_json_path
from pre_peer_checker import __version__
from pre_peer_checker.web.case_layout import validate_case_root
from pre_peer_checker.web.app import create_app
from pathlib import Path

p = patterns_json_path()
assert p.is_file(), p
text = p.read_text(encoding="utf-8")
assert "P-DATA-SWAP-CROSS-CONDITION" in text
fixture = Path("fixtures/patterns/pubpeer_patterns.json").read_text(encoding="utf-8")
assert fixture == text, "resources/pubpeer_patterns.json out of sync with fixtures/patterns/"
assert create_app() is not None
assert validate_case_root(".").ok is False  # repo root lacks manuscript/+data/
print(f"OK packaging layout — version={__version__} patterns={p} webui=ok")
PY
