#!/usr/bin/env bash
# Optional Linux/DGX smoke freeze (CLI). Not the end-user distribution path
# (prefer install.sh + WebUI). See docs/PACKAGING.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "Create .venv first: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -q 'pyinstaller>=6.3'
pip install -q -e .

OUT_DIR="${ROOT}/dist/pre-peer-checker"
rm -rf build/pre-peer-checker-cli "${OUT_DIR}"

pyinstaller packaging/pyinstaller/pre-peer-checker-cli.spec \
  --distpath dist \
  --workpath build/pre-peer-checker-cli \
  --noconfirm

BIN="${OUT_DIR}/pre-peer-checker"
test -x "${BIN}"

SMOKE_OUT="${ROOT}/outputs/packaging_smoke"
mkdir -p "${SMOKE_OUT}"
"${BIN}" fixtures/synthetic/demo \
  -o "${SMOKE_OUT}/report.html" \
  --json "${SMOKE_OUT}/warnings.json"

test -f "${SMOKE_OUT}/report.html"
test -f "${SMOKE_OUT}/warnings.json"

python - <<'PY'
import json
from pathlib import Path
w = json.loads(Path("outputs/packaging_smoke/warnings.json").read_text())
n = w.get("n_warnings", len(w.get("warnings", [])))
print(f"OK: frozen CLI smoke — n_warnings={n}")
print(f"Bundle: dist/pre-peer-checker/")
PY
