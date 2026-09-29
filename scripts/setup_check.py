#!/usr/bin/env python3
"""Print which optional capabilities are usable in this environment.

Called at the end of ``install.sh``. ``--require-mlx`` exits non-zero when the Legend LLM
cannot run on MLX (Apple Silicon setup must not silently fall back to rules only).
``--require-gpu`` does the same for Linux hosts where install.sh detected a GPU: torch,
the Legend LLM and the VLM must all run on it rather than on the CPU.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import unicodedata


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _hf_cached(repo_id: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(repo_id, "config.json"), str)
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--require-mlx", action="store_true")
    ap.add_argument("--require-gpu", action="store_true")
    args = ap.parse_args()

    from pre_peer_checker.accel import (
        GPU_DEVICES,
        device_label,
        device_summary,
        gpu_memory_cap_gb,
    )
    from pre_peer_checker.imaging.duplicate_scan import DinoDuplicateScanner
    from pre_peer_checker.imaging.lightglue_match import (
        active_lightglue_features,
        lightglue_available,
        opencv_available,
    )
    from pre_peer_checker.llm.backend import select_backend
    from pre_peer_checker.llm.json_mode import outlines_available
    from pre_peer_checker.llm.registry import (
        effective_llm_profile_id,
        effective_vlm_profile_id,
        get_profile,
    )
    from pre_peer_checker.llm.vlm_backend import select_vlm_backend
    from pre_peer_checker.parsers.manuscript_text import ocr_available
    from pre_peer_checker.parsers.raster_figure_panel_ocr import (
        apple_vision_available,
        raster_panel_ocr_enabled,
    )
    from pre_peer_checker.parsers.r_treesitter import treesitter_available
    from pre_peer_checker.usage_profile import current_usage

    llm_pid = effective_llm_profile_id()
    llm = select_backend("auto", profile_id=llm_pid)
    vlm_pid = effective_vlm_profile_id()
    vlm = select_vlm_backend("auto", profile_id=vlm_pid)

    def model_note(pid: str) -> str:
        prof = get_profile(pid)
        if prof is None or not prof.model_id:
            return pid
        cached = "取得済み" if _hf_cached(prof.model_id) else "未取得（初回実行時にダウンロード）"
        return f"{prof.model_id} / 重み {cached}"

    accel = device_summary() if _has("torch") else {"device": "cpu", "memory_gb": None}
    accel_label = device_label() if _has("torch") else "CPU"

    def backend_note(info, pid: str) -> str:
        where = "" if info.device in {"stub", "mps/unified"} else f"（{info.device}）"
        return f"{info.name}{where} — {model_note(pid)}"

    llm_info = llm.info() if llm is not None else None
    vlm_info = vlm.info() if vlm is not None else None
    rows = [
        ("WebUI", _has("fastapi") and _has("uvicorn"), ""),
        ("演算デバイス（torch）", accel["device"] != "cpu", accel_label),
        (
            "Legend LLM",
            llm is not None,
            backend_note(llm_info, llm_pid) if llm_info is not None else "規則のみ",
        ),
        (
            "VLM（パネル地図補助）",
            vlm is not None,
            backend_note(vlm_info, vlm_pid) if vlm_info is not None else "未導入",
        ),
        ("JSON スキーマ強制（Outlines）", outlines_available(), ""),
        ("R 構文解析（tree-sitter）", treesitter_available(), ""),
        (
            "画像重複検出（DINOv2 / torch）",
            DinoDuplicateScanner.available(),
            f"{accel['device']} で実行" if accel["device"] != "cpu" else "",
        ),
        (
            "画像精密照合（LightGlue）",
            lightglue_available(),
            f"特徴点: {active_lightglue_features()}（利用区分: {current_usage()}）",
        ),
        ("OpenCV", opencv_available(), ""),
        ("顕微鏡 LIF（readlif）", _has("readlif"), ""),
        ("顕微鏡 CZI（pylibCZIrw）", _has("pylibCZIrw"), ""),
        ("OCR（Tesseract）", ocr_available(), "スキャン画像だけの PDF 原稿ページを読む"),
        (
            "Figure ラスタ OCR（Apple Vision）",
            apple_vision_available(),
            "出版 Fig PDF のパネル文字（ベクターが空のとき）"
            if apple_vision_available()
            else "未導入（Mac: install.sh / pip install -e '.[vision-mac]'）",
        ),
        (
            "Figure ラスタ OCR（有効）",
            raster_panel_ocr_enabled(),
            "off: PRE_PEER_CHECKER_RASTER_PANEL_OCR=0",
        ),
    ]
    def display_width(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)

    width = max(display_width(r[0]) for r in rows)
    for name, ok, note in rows:
        mark = "OK" if ok else "--"
        pad = " " * (width - display_width(name))
        print(f"  [{mark}] {name}{pad}  {note}".rstrip())

    mem = accel.get("memory_gb")
    cap = gpu_memory_cap_gb()
    if cap is not None and mem is not None:
        mem = min(mem, cap)
    if accel["device"] in GPU_DEVICES and mem is not None and mem < 20:
        print(
            f"  注意: GPU メモリ {mem}GB では 7B モデルの一部を CPU メモリに置いて実行します"
            "（照合は完了しますが遅くなります）。"
        )

    if args.require_mlx and (llm_info is None or llm_info.name != "mlx"):
        print("ERROR: Legend LLM が MLX で動作しません。", file=sys.stderr)
        return 1
    if args.require_gpu:
        problems = []
        if accel["device"] not in GPU_DEVICES:
            problems.append("PyTorch から GPU を利用できません")
        if llm_info is None or llm_info.device not in GPU_DEVICES:
            problems.append("Legend LLM が GPU で動作しません")
        if vlm_info is None or vlm_info.device not in GPU_DEVICES:
            problems.append("VLM が GPU で動作しません")
        if problems:
            print(f"ERROR: {'、'.join(problems)}。", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
