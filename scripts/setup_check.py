#!/usr/bin/env python3
"""Print which optional capabilities are usable in this environment.

Called at the end of ``install.sh``. ``--require-mlx`` exits non-zero when the Legend LLM
cannot run on MLX (Apple Silicon setup must not silently fall back to rules only).
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
    args = ap.parse_args()

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

    mps = False
    if _has("torch"):
        try:
            import torch

            mps = bool(torch.backends.mps.is_available())
        except Exception:
            mps = False

    rows = [
        ("WebUI", _has("fastapi") and _has("uvicorn"), ""),
        (
            "Legend LLM",
            llm is not None,
            f"{llm.info().name} — {model_note(llm_pid)}" if llm is not None else "規則のみ",
        ),
        (
            "VLM（パネル地図補助）",
            vlm is not None,
            f"{vlm.info().name} — {model_note(vlm_pid)}" if vlm is not None else "未導入",
        ),
        ("JSON スキーマ強制（Outlines）", outlines_available(), ""),
        ("R 構文解析（tree-sitter）", treesitter_available(), ""),
        (
            "画像重複検出（DINOv2 / torch）",
            DinoDuplicateScanner.available(),
            "Apple GPU (MPS) 利用可" if mps else "",
        ),
        (
            "画像精密照合（LightGlue）",
            lightglue_available(),
            f"特徴点: {active_lightglue_features()}（利用区分: {current_usage()}）",
        ),
        ("OpenCV", opencv_available(), ""),
        ("顕微鏡 LIF（readlif）", _has("readlif"), ""),
        ("顕微鏡 CZI（pylibCZIrw）", _has("pylibCZIrw"), ""),
    ]
    def display_width(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)

    width = max(display_width(r[0]) for r in rows)
    for name, ok, note in rows:
        mark = "OK" if ok else "--"
        pad = " " * (width - display_width(name))
        print(f"  [{mark}] {name}{pad}  {note}".rstrip())

    if args.require_mlx and (llm is None or llm.info().name != "mlx"):
        print("ERROR: Legend LLM が MLX で動作しません。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
