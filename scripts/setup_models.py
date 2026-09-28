#!/usr/bin/env python3
"""Pre-download default model weights so the first WebUI run works without waiting.

Called by ``install.sh``. Each step is best-effort: failures are reported, never fatal.
Weights go to the standard caches (``~/.cache/huggingface``, ``~/.cache/torch``).
"""

from __future__ import annotations

import sys


def _hf_snapshot(label: str, repo_id: str) -> bool:
    from huggingface_hub import snapshot_download

    print(f"  - {label}: {repo_id}（初回は数 GB。回線により数分〜数十分）", flush=True)
    snapshot_download(repo_id)
    return True


def _mlx_profiles() -> list[tuple[str, str]]:
    from pre_peer_checker.llm.registry import (
        effective_llm_profile_id,
        effective_vlm_profile_id,
        get_profile,
    )

    out: list[tuple[str, str]] = []
    for label, pid in (
        ("Legend LLM", effective_llm_profile_id()),
        ("VLM（パネル地図補助）", effective_vlm_profile_id()),
    ):
        prof = get_profile(pid)
        if prof is not None and prof.prefer == "mlx" and prof.model_id:
            out.append((label, prof.model_id))
    return out


def _dinov2() -> bool:
    from pre_peer_checker.imaging.duplicate_scan import DinoDuplicateScanner

    if not DinoDuplicateScanner.available():
        return False
    import torch

    print("  - 画像重複検出: DINOv2 (facebookresearch/dinov2)", flush=True)
    torch.hub.load("facebookresearch/dinov2", "dinov2_vits14", trust_repo=True)
    return True


def _lightglue() -> bool:
    from pre_peer_checker.imaging.lightglue_match import lightglue_available

    if not lightglue_available():
        return False
    from lightglue import LightGlue, SuperPoint

    print("  - 画像精密照合: LightGlue + SuperPoint", flush=True)
    SuperPoint(max_num_keypoints=2048)
    LightGlue(features="superpoint")
    return True


def main() -> int:
    failures: list[str] = []
    for label, repo in _mlx_profiles():
        try:
            _hf_snapshot(label, repo)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label} ({repo}): {exc}")
    for label, fn in (("DINOv2", _dinov2), ("LightGlue", _lightglue)):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: {exc}")
    if failures:
        print("  モデル取得で失敗した項目（WebUI 初回実行時に再取得されます）:")
        for f in failures:
            print(f"    ! {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
