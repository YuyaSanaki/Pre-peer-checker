"""Torch accelerator detection shared by LLM / VLM / imaging backends.

Order: CUDA (NVIDIA, and AMD via ROCm builds which expose ``torch.cuda``) →
Intel XPU → Apple MPS → CPU. ``PRE_PEER_CHECKER_DEVICE`` forces a device.
``PRE_PEER_CHECKER_GPU_MAX_MEMORY_GB`` caps the VRAM used for LLM / VLM weights
(the rest goes to CPU RAM) — also used to reproduce small-GPU hosts on large GPUs.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

GPU_DEVICES = frozenset({"cuda", "xpu"})


def _allocates(torch: Any, device: str) -> bool:
    # is_available() can be True while the driver is unusable (e.g. sandbox, stale driver).
    try:
        torch.zeros(1, device=device)
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def torch_device() -> str:
    forced = (os.environ.get("PRE_PEER_CHECKER_DEVICE") or "").strip().lower()
    if forced:
        return forced
    try:
        import torch
    except Exception:
        return "cpu"
    try:
        if (
            torch.cuda.is_available()
            and torch.cuda.device_count() > 0
            and _allocates(torch, "cuda")
        ):
            return "cuda"
    except Exception:
        pass
    xpu = getattr(torch, "xpu", None)
    try:
        if xpu is not None and xpu.is_available() and _allocates(torch, "xpu"):
            return "xpu"
    except Exception:
        pass
    mps = getattr(torch.backends, "mps", None)
    try:
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def gpu_device() -> str | None:
    """``cuda`` / ``xpu`` when a discrete-style torch GPU is usable, else None."""
    d = torch_device()
    return d if d in GPU_DEVICES else None


def clear_cache() -> None:
    torch_device.cache_clear()


def device_summary() -> dict[str, Any]:
    """Human-oriented description of the active accelerator (never raises)."""
    device = torch_device()
    out: dict[str, Any] = {"device": device, "vendor": "cpu", "name": "", "memory_gb": None}
    if device == "cpu":
        return out
    try:
        import torch

        if device == "cuda":
            props = torch.cuda.get_device_properties(0)
            hip = getattr(torch.version, "hip", None)
            out["vendor"] = "amd" if hip else "nvidia"
            out["runtime"] = f"ROCm {hip}" if hip else f"CUDA {torch.version.cuda}"
            out["name"] = props.name
            out["memory_gb"] = round(props.total_memory / 1024**3, 1)
        elif device == "xpu":
            props = torch.xpu.get_device_properties(0)
            out["vendor"] = "intel"
            out["runtime"] = "XPU"
            out["name"] = props.name
            out["memory_gb"] = round(props.total_memory / 1024**3, 1)
        elif device == "mps":
            out["vendor"] = "apple"
            out["runtime"] = "MPS"
            out["name"] = "Apple GPU"
    except Exception:
        pass
    return out


def device_label() -> str:
    s = device_summary()
    if s["device"] == "cpu":
        return "CPU"
    parts = [s.get("name") or s["device"]]
    if s.get("runtime"):
        parts.append(f"（{s['runtime']}")
        parts[-1] += f" / {s['memory_gb']}GB）" if s.get("memory_gb") else "）"
    return "".join(parts)


def gpu_memory_cap_gb() -> float | None:
    raw = (os.environ.get("PRE_PEER_CHECKER_GPU_MAX_MEMORY_GB") or "").strip()
    try:
        cap = float(raw)
    except ValueError:
        return None
    return cap if cap > 0 else None


def _capped_max_memory(cap_gb: float) -> dict[Any, str]:
    try:
        import psutil

        cpu_gb = int(psutil.virtual_memory().available / 1024**3)
    except Exception:
        cpu_gb = 64
    # Without a "cpu" entry accelerate would offload the overflow to disk instead.
    return {0: f"{cap_gb}GiB", "cpu": f"{max(cpu_gb, 1)}GiB"}


def _is_oom(exc: BaseException) -> bool:
    try:
        import torch

        if isinstance(exc, torch.OutOfMemoryError):
            return True
    except Exception:
        pass
    return "out of memory" in str(exc).lower()


def load_pretrained(model_cls: Any, model_id: str, *, device: str, **kwargs: Any) -> Any:
    """``from_pretrained`` onto ``device``; spill layers to CPU RAM when VRAM is short.

    GPU loads go through accelerate ``device_map`` — callers must not pass ``device=``
    to ``transformers.pipeline`` for such models.
    """
    if device not in GPU_DEVICES:
        return model_cls.from_pretrained(model_id, **kwargs).to(device)
    cap = gpu_memory_cap_gb()
    if cap is not None:
        return model_cls.from_pretrained(
            model_id, device_map="auto", max_memory=_capped_max_memory(cap), **kwargs
        )
    try:
        return model_cls.from_pretrained(model_id, device_map={"": device}, **kwargs)
    except Exception as exc:
        if not _is_oom(exc):
            raise
    import gc

    import torch

    gc.collect()
    backend = getattr(torch, device, None)
    if backend is not None and hasattr(backend, "empty_cache"):
        backend.empty_cache()
    return model_cls.from_pretrained(model_id, device_map="auto", **kwargs)
