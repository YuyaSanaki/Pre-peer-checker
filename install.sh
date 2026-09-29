#!/usr/bin/env bash
# One-shot setup for non-programmers: clone → ./install.sh → double-click launcher.
#
# Apple Silicon Mac では Legend LLM / VLM（MLX）・画像スタック・既定モデルの重みまで
# 導入し、この 1 回で全機能が使える状態にする。Linux では GPU（NVIDIA CUDA / AMD ROCm /
# Intel XPU）を検出し、それに合う PyTorch で同じ機能一式を導入する（DGX Spark を含む）。
# 再実行しても安全（不足分だけ補う）。
#
# 環境変数（任意）:
#   PRE_PEER_CHECKER_PYTHON=/path/to/python3   使う Python を明示（3.11 以上）
#   PRE_PEER_CHECKER_SKIP_MODELS=1             モデル重みの事前ダウンロードを省略
#   PRE_PEER_CHECKER_SKIP_OCR=1                OCR（Tesseract）の導入を省略
#   PRE_PEER_CHECKER_ACCEL=cuda|rocm|xpu|cpu   Linux の GPU 種別を明示（既定: 自動検出）
#   PRE_PEER_CHECKER_TORCH_INDEX=URL           PyTorch の取得元 index を明示（新しい CUDA / ROCm 版など）
#   PRE_PEER_CHECKER_EXTRAS=dev,gui            追加で入れる extras（カンマ区切り）
#   PRE_PEER_CHECKER_USAGE=academic|commercial 利用区分の質問を省略（非対話実行用）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

OS="$(uname -s)"
APPLE_SILICON=0
if [[ "${OS}" == "Darwin" && "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]]; then
  APPLE_SILICON=1
  # Rosetta（x86_64）ターミナルから起動された場合は arm64 で再実行する。MLX は arm64 専用。
  if [[ "$(uname -m)" != "arm64" ]]; then
    echo "==> Rosetta 環境を検出。arm64 で再実行します…"
    exec arch -arm64 /bin/bash "$0" ${1+"$@"}
  fi
fi

# Linux の GPU 種別。NVIDIA はドライバ（nvidia-smi）、AMD は ROCm カーネルドライバ（/dev/kfd）、
# Intel は DRM デバイスのベンダ ID で判定する。
detect_linux_accel() {
  local want="${PRE_PEER_CHECKER_ACCEL:-auto}"
  if [[ "${want}" != "auto" ]]; then
    echo "${want}"
    return
  fi
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    echo cuda
  elif [[ -e /dev/kfd ]] && grep -qsx 0x1002 /sys/class/drm/card*/device/vendor; then
    echo rocm
  elif grep -qsx 0x8086 /sys/class/drm/card*/device/vendor; then
    echo xpu
  else
    echo cpu
  fi
}

ACCEL="none"
if [[ "${OS}" == "Linux" ]]; then
  ACCEL="$(detect_linux_accel)"
  case "${ACCEL}" in
    cuda|rocm|xpu|cpu) ;;
    *)
      echo "ERROR: PRE_PEER_CHECKER_ACCEL は cuda / rocm / xpu / cpu のいずれかを指定してください（指定値: ${ACCEL}）。" >&2
      exit 1
      ;;
  esac
  if [[ "${ACCEL}" == "rocm" || "${ACCEL}" == "xpu" ]] && [[ "$(uname -m)" != "x86_64" ]]; then
    echo "    注意: ${ACCEL} 版 PyTorch は x86_64 のみ提供されています。CPU で導入します。"
    ACCEL="cpu"
  fi
fi

PORT="${PRE_PEER_CHECKER_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

echo "==> Pre-peer-checker セットアップ"
echo "    リポジトリ: ${ROOT}"
if [[ "${APPLE_SILICON}" == "1" ]]; then
  echo "    環境: Apple Silicon Mac（macOS $(sw_vers -productVersion)）— MLX 構成で導入します"
  MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
  if (( MACOS_MAJOR < 14 )); then
    echo "    注意: MLX は macOS 14 以降を推奨します。導入に失敗する場合は macOS を更新してください。"
  fi
elif [[ "${OS}" == "Linux" ]]; then
  case "${ACCEL}" in
    cuda) ACCEL_LABEL="NVIDIA GPU（CUDA）" ;;
    rocm) ACCEL_LABEL="AMD GPU（ROCm）" ;;
    xpu) ACCEL_LABEL="Intel GPU（XPU）" ;;
    *) ACCEL_LABEL="GPU なし（CPU）" ;;
  esac
  echo "    環境: Linux $(uname -m) — ${ACCEL_LABEL} 構成で導入します"
  if [[ "${ACCEL}" == "cpu" ]]; then
    echo "    注意: GPU が見つかりません。Legend LLM / VLM は CPU では実用速度にならないため、"
    echo "          GPU ドライバ導入後に ./install.sh を再実行してください。"
  fi
else
  echo "    環境: ${OS} $(uname -m)"
fi

# ---------------------------------------------------------------------------
# 利用区分（ライセンス上の利用条件が付くコンポーネントを切り替える）
# ---------------------------------------------------------------------------
USAGE_FILE="${ROOT}/usage_profile.json"
usage_label() {
  case "$1" in
    academic) echo "大学・非営利組織による非商用研究（SuperPoint を使用）" ;;
    *) echo "企業・商用研究・その他（ALIKED を使用）" ;;
  esac
}
CURRENT_USAGE=""
if [[ -f "${USAGE_FILE}" ]]; then
  CURRENT_USAGE="$(sed -n 's/.*"usage"[[:space:]]*:[[:space:]]*"\([a-z]*\)".*/\1/p' "${USAGE_FILE}" | head -n 1)"
fi
USAGE="${PRE_PEER_CHECKER_USAGE:-}"
if [[ -n "${USAGE}" && "${USAGE}" != "academic" && "${USAGE}" != "commercial" ]]; then
  echo "ERROR: PRE_PEER_CHECKER_USAGE は academic か commercial を指定してください（指定値: ${USAGE}）。" >&2
  exit 1
fi
if [[ -z "${USAGE}" ]] && (: </dev/tty) 2>/dev/null; then
  DEFAULT_CHOICE=2
  [[ "${CURRENT_USAGE}" == "academic" ]] && DEFAULT_CHOICE=1
  echo
  echo "==> 利用区分を選んでください"
  echo "    画像精密照合の特徴点抽出 SuperPoint は、開発元 Magic Leap のライセンスにより"
  echo "    「大学・非営利組織による非商用研究」でのみ利用できます。"
  echo "    企業の研究所・製薬企業・CRO など営利組織での利用（自社論文のチェックを含む）は対象外です。"
  echo "      1) 大学・非営利組織による非商用研究 — SuperPoint を使用"
  echo "      2) 上記以外（企業・商用研究・判断がつかない場合）— ALIKED（BSD-3）を使用"
  while [[ -z "${USAGE}" ]]; do
    ANSWER=""
    read -r -p "    番号を入力 [既定: ${DEFAULT_CHOICE}]: " ANSWER </dev/tty || ANSWER=""
    case "${ANSWER:-${DEFAULT_CHOICE}}" in
      1) USAGE="academic" ;;
      2) USAGE="commercial" ;;
      *) echo "    1 か 2 を入力してください。" ;;
    esac
  done
fi
USAGE="${USAGE:-${CURRENT_USAGE:-commercial}}"
cat > "${USAGE_FILE}" <<EOF
{"usage": "${USAGE}", "selected_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
echo "    利用区分: $(usage_label "${USAGE}")"
echo "    （変更するには ./install.sh を再実行してください）"

# ---------------------------------------------------------------------------
# Python（3.11 以上。Apple Silicon ではネイティブ arm64 が必須）
# ---------------------------------------------------------------------------
python_ok() {
  "$1" - "${APPLE_SILICON}" >/dev/null 2>&1 <<'PY'
import platform, sys
ok = sys.version_info >= (3, 11)
if sys.argv[1] == "1":
    ok = ok and platform.machine() == "arm64"
raise SystemExit(0 if ok else 1)
PY
}

find_python() {
  local cand
  local candidates=()
  [[ -n "${PRE_PEER_CHECKER_PYTHON:-}" ]] && candidates+=("${PRE_PEER_CHECKER_PYTHON}")
  candidates+=(python3.12 python3.13 python3.11)
  if [[ "${OS}" == "Darwin" ]]; then
    candidates+=(
      /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.11
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
      /Library/Frameworks/Python.framework/Versions/3.13/bin/python3
      /Library/Frameworks/Python.framework/Versions/3.11/bin/python3
    )
  fi
  candidates+=(python3)
  for cand in "${candidates[@]}"; do
    if command -v "${cand}" >/dev/null 2>&1 && python_ok "$(command -v "${cand}")"; then
      command -v "${cand}"
      return 0
    fi
  done
  return 1
}

# 適切な Python が無ければ uv（単一バイナリ）で Python 3.12 を取得する。システムは汚さない。
python_via_uv() {
  local uv_bin
  uv_bin="$(command -v uv 2>/dev/null || true)"
  if [[ -z "${uv_bin}" && -x "${HOME}/.local/bin/uv" ]]; then
    uv_bin="${HOME}/.local/bin/uv"
  fi
  if [[ -z "${uv_bin}" ]]; then
    echo "==> Python 3.11 以上が見つからないため、uv 経由で Python 3.12 を取得します…" >&2
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >&2
    uv_bin="${HOME}/.local/bin/uv"
  fi
  "${uv_bin}" python install 3.12 >&2
  "${uv_bin}" python find 3.12
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(python_via_uv || true)"
fi
if [[ -z "${PYTHON_BIN}" ]] || ! python_ok "${PYTHON_BIN}"; then
  echo "ERROR: Python 3.11 以上（Apple Silicon ではネイティブ arm64 版）を用意できませんでした。" >&2
  echo "       https://www.python.org/downloads/ から macOS 版をインストールして再実行してください。" >&2
  exit 1
fi
echo "    Python: ${PYTHON_BIN} ($("${PYTHON_BIN}" -c 'import platform; print(platform.python_version(), platform.machine())'))"

# 既存 .venv が古い Python / x86_64 で作られていたら作り直す
if [[ -d .venv ]] && ! { [[ -x .venv/bin/python ]] && python_ok .venv/bin/python; }; then
  echo "==> 既存の .venv が要件を満たさないため作り直します…"
  rm -rf .venv
fi
create_venv() {
  "$1" -m venv .venv >/dev/null 2>&1 && .venv/bin/python -c 'import encodings, ensurepip' >/dev/null 2>&1
}
if [[ ! -d .venv ]]; then
  echo "==> .venv を作成中…"
  # uv 管理 Python をシンボリックリンク経由で使うと stdlib を見失うため、失敗時は実体パスで作り直す
  if ! create_venv "${PYTHON_BIN}"; then
    rm -rf .venv
    if ! create_venv "$("${PYTHON_BIN}" -c 'import os, sys; print(os.path.realpath(sys.executable))')"; then
      echo "ERROR: .venv を作成できませんでした（${PYTHON_BIN}）。" >&2
      exit 1
    fi
  fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

# ---------------------------------------------------------------------------
# パッケージ
# ---------------------------------------------------------------------------
OPTIONAL_NG=()

try_install() {
  local label="$1"
  shift
  echo "==> ${label} …"
  if ! python -m pip install --quiet "$@"; then
    echo "    警告: ${label} の導入に失敗しました（この機能なしで続行します）。"
    OPTIONAL_NG+=("${label}")
  fi
}

REQUIRED_EXTRAS="web"
[[ "${APPLE_SILICON}" == "1" ]] && REQUIRED_EXTRAS="web,mlx"
if [[ -n "${PRE_PEER_CHECKER_EXTRAS:-}" ]]; then
  REQUIRED_EXTRAS="${REQUIRED_EXTRAS},${PRE_PEER_CHECKER_EXTRAS}"
fi
echo "==> 本体をインストール中（extras: ${REQUIRED_EXTRAS}）…"
python -m pip install --quiet -e ".[${REQUIRED_EXTRAS}]"

# NVIDIA ドライバが対応する CUDA 版（nvidia-smi 表示）に合わせて PyTorch の index を選ぶ。
# cu130: ドライバ 580 以上（DGX Spark / Blackwell 世代を含む）、cu128: 570 以上、cu126: 560 以上。
cuda_torch_index() {
  local major=0 minor=0 v
  v="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9]*\)\.\([0-9]*\).*/\1 \2/p' | head -n 1)"
  [[ -n "${v}" ]] && read -r major minor <<<"${v}"
  if (( major >= 13 )); then
    echo cu130
  elif (( major == 12 && minor >= 8 )); then
    echo cu128
  else
    if (( major < 12 || (major == 12 && minor < 6) )); then
      echo "    警告: NVIDIA ドライバが古い可能性があります（CUDA ${major}.${minor}）。" \
        "GPU を使うにはドライバを 560 以上に更新してください。" >&2
    fi
    echo cu126
  fi
}

# 入っている torch が目的の GPU を使える状態なら 0。
torch_matches_accel() {
  python - "$1" >/dev/null 2>&1 <<'PY'
import sys

import torch
import torchvision  # noqa: F401

want = sys.argv[1]
if want == "cuda":
    ok = torch.version.cuda is not None and torch.cuda.is_available()
elif want == "rocm":
    ok = getattr(torch.version, "hip", None) is not None and torch.cuda.is_available()
elif want == "xpu":
    ok = hasattr(torch, "xpu") and torch.xpu.is_available()
else:
    ok = True
raise SystemExit(0 if ok else 1)
PY
}

install_linux_torch() {
  local index="${PRE_PEER_CHECKER_TORCH_INDEX:-}"
  if [[ -z "${index}" ]]; then
    case "${ACCEL}" in
      cuda) index="https://download.pytorch.org/whl/$(cuda_torch_index)" ;;
      rocm) index="https://download.pytorch.org/whl/rocm7.2" ;;
      xpu) index="https://download.pytorch.org/whl/xpu" ;;
      *) index="https://download.pytorch.org/whl/cpu" ;;
    esac
  fi
  if torch_matches_accel "${ACCEL}"; then
    echo "==> PyTorch（${ACCEL}）は導入済みです"
    return
  fi
  # CPU 版など別構成の torch が入っていると pip は入れ替えないため、先に外す
  if python -c 'import torch' >/dev/null 2>&1; then
    echo "==> 既存の PyTorch が ${ACCEL} に対応していないため入れ替えます…"
    python -m pip uninstall --quiet -y torch torchvision
  fi
  try_install "PyTorch（${ACCEL}: ${index}）" torch torchvision --index-url "${index}"
}

try_install "R 構文解析（tree-sitter）" -e ".[r-ast]"
if [[ "${APPLE_SILICON}" == "1" ]]; then
  try_install "VLM パネル地図補助（mlx-vlm）" -e ".[vlm-mlx]"
  try_install "JSON スキーマ強制（Outlines）" -e ".[llm-json]"
  try_install "画像スタック（torch / torchvision / OpenCV）" \
    "torch>=2.2" "torchvision>=0.17" "opencv-python-headless>=4.8"
elif [[ "${OS}" == "Linux" ]]; then
  # torch は GPU 別の index から先に入れる（後続の依存解決で PyPI 版に置き換わらないように）
  install_linux_torch
  try_install "Legend LLM / VLM パネル地図補助（transformers）" -e ".[llm-cuda,vlm-cuda]"
  try_install "JSON スキーマ強制（Outlines）" -e ".[llm-json]"
  # opencv-python（GUI 版）が既にあれば cv2 が衝突するため headless は入れない
  if ! python -c 'import cv2' >/dev/null 2>&1; then
    try_install "OpenCV" "opencv-python-headless>=4.8"
  fi
fi
if [[ "${APPLE_SILICON}" == "1" || "${OS}" == "Linux" ]]; then
  # 上流の変更（ライセンス・重み・API）を検知できるようコミット固定。更新時は再較正:
  #   scripts/dev_lightglue_threshold_calib.py --features {superpoint,aliked}
  LIGHTGLUE_COMMIT="eb42fee2d71449efb0aa5c10549752b5d75384d8"
  try_install "画像精密照合（LightGlue）" \
    "lightglue @ https://github.com/cvg/LightGlue/archive/${LIGHTGLUE_COMMIT}.zip"
  try_install "顕微鏡 LIF（readlif）" "readlif>=0.6"
  try_install "顕微鏡 CZI（pylibCZIrw）" "pylibCZIrw>=4.0"
fi

# ---------------------------------------------------------------------------
# OCR（Tesseract）: Word 原稿が無いとき、スキャン画像だけの PDF ページを読むため（任意）。
# pip では入らないので OS のパッケージ管理を使う（Linux は管理者権限が要る）。
# ---------------------------------------------------------------------------
install_tesseract() {
  if command -v tesseract >/dev/null 2>&1; then
    echo "==> OCR（Tesseract）は導入済みです"
    return
  fi
  local sudo="" ok=1
  if [[ "${OS}" == "Linux" && "$(id -u)" != "0" ]]; then
    if ! command -v sudo >/dev/null 2>&1 \
      || ! { sudo -n true 2>/dev/null || (: </dev/tty) 2>/dev/null; }; then
      echo "==> OCR（Tesseract）: 管理者権限が無いため省略します"
      OPTIONAL_NG+=("OCR（Tesseract）")
      return
    fi
    sudo="sudo"
  fi
  echo "==> OCR（Tesseract）…"
  if [[ "${OS}" == "Darwin" ]]; then
    if command -v brew >/dev/null 2>&1; then
      brew install tesseract && ok=0
    else
      echo "    Homebrew（https://brew.sh）が無いため省略します。"
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    [[ -n "${sudo}" ]] && echo "    管理者パスワードを求められたら入力してください。"
    { ${sudo} apt-get install -y -qq tesseract-ocr \
      || { ${sudo} apt-get update -qq && ${sudo} apt-get install -y -qq tesseract-ocr; }; } && ok=0
  elif command -v dnf >/dev/null 2>&1; then
    [[ -n "${sudo}" ]] && echo "    管理者パスワードを求められたら入力してください。"
    ${sudo} dnf install -y -q tesseract && ok=0
  fi
  if (( ok != 0 )); then
    echo "    警告: OCR（Tesseract）を導入できませんでした（スキャン PDF のページは読まずに続行します）。"
    OPTIONAL_NG+=("OCR（Tesseract）")
  fi
}
if [[ "${PRE_PEER_CHECKER_SKIP_OCR:-0}" != "1" ]]; then
  install_tesseract
fi

# ---------------------------------------------------------------------------
# モデル重みの事前取得（Apple Silicon / Linux）
# Linux は HF 版（fp16）の LLM + VLM で計 ~32GB。GPU が無い場合は画像モデルのみ。
# ---------------------------------------------------------------------------
if [[ "${APPLE_SILICON}" == "1" || "${OS}" == "Linux" ]] \
  && [[ "${PRE_PEER_CHECKER_SKIP_MODELS:-0}" != "1" ]]; then
  if [[ "${APPLE_SILICON}" == "1" ]]; then
    MEM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
    NEED_GB=15
  else
    MEM_GB=$(( $(awk '/^MemTotal:/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
    NEED_GB=15
    [[ "${ACCEL}" != "cpu" ]] && NEED_GB=40
  fi
  # 重みの保存先（HF_HOME を /work 等へ向けている場合はそちら）の空きを見る
  MODEL_DIR="${HF_HOME:-${XDG_CACHE_HOME:-${HOME}/.cache}/huggingface}"
  while [[ ! -d "${MODEL_DIR}" ]]; do MODEL_DIR="$(dirname "${MODEL_DIR}")"; done
  FREE_GB=$(( $(df -Pk "${MODEL_DIR}" | awk 'NR==2 {print $4}') / 1024 / 1024 ))
  echo "==> 既定モデルの重みを取得中（メモリ ${MEM_GB}GB / 空きディスク ${FREE_GB}GB）…"
  if (( MEM_GB < 16 )); then
    echo "    注意: メモリ 16GB 未満では 7B モデルの推論が重くなります。"
    echo "          照合中は他のアプリを閉じてください。"
  fi
  if (( FREE_GB < NEED_GB )); then
    echo "    警告: 空きディスクが ${NEED_GB}GB 未満です。モデル取得を省略します"
    echo "          （WebUI 初回実行時にダウンロードされます）。"
  else
    python "${ROOT}/scripts/setup_models.py" || true
  fi
fi

# ---------------------------------------------------------------------------
# 起動ランチャ
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# 動作確認
# ---------------------------------------------------------------------------
echo
echo "==> 機能チェック"
CHECK_ARGS=()
[[ "${APPLE_SILICON}" == "1" ]] && CHECK_ARGS+=(--require-mlx)
# NVIDIA はドライバが動いていれば GPU を使えるはず。ROCm / XPU は対応 GPU が限られるため警告に留める。
[[ "${ACCEL}" == "cuda" ]] && CHECK_ARGS+=(--require-gpu)
if ! python "${ROOT}/scripts/setup_check.py" "${CHECK_ARGS[@]+"${CHECK_ARGS[@]}"}"; then
  if [[ "${APPLE_SILICON}" == "1" ]]; then
    echo "ERROR: MLX の導入を確認できませんでした。上のログを確認して ./install.sh を再実行してください。" >&2
  else
    echo "ERROR: GPU での動作を確認できませんでした。上のログを確認して ./install.sh を再実行してください。" >&2
    echo "       GPU を使わずに導入する場合: PRE_PEER_CHECKER_ACCEL=cpu ./install.sh" >&2
  fi
  exit 1
fi
if [[ "${ACCEL}" == "rocm" || "${ACCEL}" == "xpu" ]] \
  && ! python -c 'from pre_peer_checker.accel import gpu_device; raise SystemExit(0 if gpu_device() else 1)' 2>/dev/null; then
  echo "    注意: ${ACCEL_LABEL} を PyTorch から利用できませんでした。CPU で動作します"
  echo "          （対応 GPU・ドライバを確認してください）。"
fi
if (( ${#OPTIONAL_NG[@]} > 0 )); then
  echo
  echo "    導入に失敗した任意機能: ${OPTIONAL_NG[*]}"
  echo "    （ネットワーク状況を確認して ./install.sh を再実行すると補えます）"
fi

echo
echo "==> セットアップ完了"
echo "    次回からは次をダブルクリックしてください:"
if [[ "${OS}" == "Darwin" ]]; then
  echo "      ${COMMAND_LAUNCHER}"
else
  echo "      ${ROOT}/scripts/start_webui.sh"
  echo "      または ${DESKTOP_LAUNCHER}"
fi
echo "    ブラウザで ${URL} が開きます。"
echo
echo "利用者向け: 親フォルダに manuscript/ と data/ を用意し、WebUI でその親を選んで「照合開始」。"
