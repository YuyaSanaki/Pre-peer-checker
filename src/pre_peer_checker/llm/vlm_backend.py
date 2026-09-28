"""Local VLM backends for panel-map assist (optional; vector geometry remains primary).

Preference when ``prefer=\"auto\"``:
  1. mlx-vlm on Apple Silicon
  2. transformers Qwen2.5-VL on CUDA
  3. None → caller keeps vector-only regions
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class VlmBackendInfo:
    name: str
    device: str
    model_id: str
    available: bool
    detail: str = ""


class LocalVlmBackend(ABC):
    @abstractmethod
    def info(self) -> VlmBackendInfo: ...

    @abstractmethod
    def generate(self, prompt: str, image_path: Path | str, *, max_tokens: int = 768) -> str: ...


class CallableVlmBackend(LocalVlmBackend):
    def __init__(self, fn: Callable[[str, Path], str], name: str = "callable-vlm"):
        self._fn = fn
        self._name = name

    def info(self) -> VlmBackendInfo:
        return VlmBackendInfo(
            name=self._name, device="stub", model_id="callable", available=True
        )

    def generate(self, prompt: str, image_path: Path | str, *, max_tokens: int = 768) -> str:
        return self._fn(prompt, Path(image_path))


def _unwrap_vlm_text(out: Any) -> str:
    """mlx-vlm may return str, (str,), or GenerationResult(text=...)."""
    if out is None:
        return ""
    if isinstance(out, tuple) and out:
        return _unwrap_vlm_text(out[0])
    text_attr = getattr(out, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    if isinstance(out, str):
        # Defensive: stringify of GenerationResult embeds text='...'
        if out.startswith("GenerationResult(") and "text=" in out:
            m = re.search(r"text=(['\"])(?P<body>.*?)(?<!\\)\1", out, re.S)
            if m:
                body = m.group("body")
                try:
                    return bytes(body, "utf-8").decode("unicode_escape")
                except Exception:
                    return body.replace("\\n", "\n").replace('\\"', '"')
        return out
    return str(out)


class MlxVlmBackend(LocalVlmBackend):
    def __init__(
        self,
        model_id: str = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
    ):
        self.model_id = model_id
        self._model = None
        self._processor = None
        self._config = None

    @staticmethod
    def available() -> bool:
        try:
            import mlx_vlm  # noqa: F401

            return True
        except Exception:
            return False

    def info(self) -> VlmBackendInfo:
        return VlmBackendInfo(
            name="mlx-vlm",
            device="mps/unified",
            model_id=self.model_id,
            available=self.available(),
        )

    def load(self) -> None:
        if self._model is not None:
            return
        from mlx_vlm import load
        from mlx_vlm.utils import load_config

        self._model, self._processor = load(self.model_id)
        try:
            self._config = load_config(self.model_id)
        except Exception:
            self._config = None

    def generate(self, prompt: str, image_path: Path | str, *, max_tokens: int = 768) -> str:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        self.load()
        image_path = Path(image_path)
        formatted = apply_chat_template(
            self._processor,
            self._config,
            prompt,
            num_images=1,
        )
        out = generate(
            self._model,
            self._processor,
            formatted,
            image=[str(image_path)],
            max_tokens=max_tokens,
            verbose=False,
        )
        return _unwrap_vlm_text(out)


class TransformersVlmBackend(LocalVlmBackend):
    """Qwen2.5-VL via transformers (CUDA / CPU)."""

    def __init__(self, model_id: str | None = None, *, device: str | None = None):
        self.model_id = model_id or "Qwen/Qwen2.5-VL-7B-Instruct"
        self.device_override = device
        self._model = None
        self._processor = None
        self._device = "cpu"

    @staticmethod
    def available() -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401

            return True
        except Exception:
            return False

    def _resolve_device(self) -> str:
        if self.device_override:
            return self.device_override
        import torch

        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def info(self) -> VlmBackendInfo:
        ok = self.available()
        device = "unknown"
        detail = ""
        if ok:
            try:
                device = self._resolve_device()
            except Exception:
                device = "error"
            if device == "cuda":
                try:
                    import torch

                    detail = torch.cuda.get_device_name(0)
                except Exception:
                    detail = ""
        return VlmBackendInfo(
            name="transformers-vlm",
            device=device,
            model_id=self.model_id,
            available=ok,
            detail=detail,
        )

    def load(self) -> None:
        if self._model is not None:
            return
        os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
        os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
        os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")
        import torch
        from transformers import AutoProcessor

        device = self._resolve_device()
        # Prefer AutoModelForImageTextToText when present (transformers≥4.49)
        try:
            from transformers import AutoModelForImageTextToText as ModelCls
        except Exception:
            from transformers import Qwen2_5_VLForConditionalGeneration as ModelCls  # type: ignore

        self._processor = AutoProcessor.from_pretrained(self.model_id, trust_remote_code=True)
        dtype = torch.float16 if device == "cuda" else torch.float32
        self._model = ModelCls.from_pretrained(
            self.model_id,
            torch_dtype=dtype,
            trust_remote_code=True,
            low_cpu_mem_usage=True,
        )
        self._model = self._model.to(device)
        self._device = device

    def generate(self, prompt: str, image_path: Path | str, *, max_tokens: int = 768) -> str:
        from PIL import Image

        self.load()
        assert self._model is not None and self._processor is not None
        image = Image.open(image_path).convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._processor(text=[text], images=[image], return_tensors="pt")
        inputs = {k: v.to(self._device) if hasattr(v, "to") else v for k, v in inputs.items()}
        import torch

        with torch.inference_mode():
            out_ids = self._model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        trim = out_ids[:, inputs["input_ids"].shape[-1] :]
        return str(self._processor.batch_decode(trim, skip_special_tokens=True)[0])


def probe_vlm_backends() -> list[VlmBackendInfo]:
    return [MlxVlmBackend().info(), TransformersVlmBackend().info()]


def _cuda_usable() -> bool:
    try:
        import torch

        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            return False
        torch.zeros(1, device="cuda")
        return True
    except Exception:
        return False


def _hf_vl_from_mlx_id(model_id: str) -> str:
    mid = (model_id or "").lower()
    if "32b" in mid:
        return "Qwen/Qwen2.5-VL-32B-Instruct"
    return "Qwen/Qwen2.5-VL-7B-Instruct"


def select_vlm_backend(
    prefer: str = "auto",
    *,
    model_id: str | None = None,
    profile_id: str | None = None,
) -> LocalVlmBackend | None:
    """Return a VLM backend or None."""
    from pre_peer_checker.llm.registry import resolve_model

    prefer_in = (prefer or "auto").lower()
    if prefer_in in {"none", "off", "0", "false"}:
        return None

    resolved = resolve_model(
        role="vision",
        profile_id=profile_id,
        model_id=model_id,
        prefer=None if prefer_in == "auto" else prefer_in,
    )
    mid = (model_id or resolved.model_id or "").strip() or "Qwen/Qwen2.5-VL-7B-Instruct"
    if prefer_in != "auto":
        prefer_l = prefer_in
    elif resolved.prefer in {"mlx", "cuda", "transformers", "hf"}:
        prefer_l = resolved.prefer
    else:
        prefer_l = "auto"

    if prefer_l == "mlx":
        mlx_id = mid
        if not mid.startswith("mlx-community/"):
            mlx_id = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
        if MlxVlmBackend.available():
            return MlxVlmBackend(mlx_id)
        if TransformersVlmBackend.available():
            return TransformersVlmBackend(
                _hf_vl_from_mlx_id(mid),
                device="cuda" if _cuda_usable() else None,
            )
        return None

    if prefer_l in {"cuda", "transformers", "hf"}:
        if not TransformersVlmBackend.available():
            return None
        hf = mid if not mid.startswith("mlx-community/") else _hf_vl_from_mlx_id(mid)
        return TransformersVlmBackend(
            hf, device="cuda" if (prefer_l == "cuda" or _cuda_usable()) and _cuda_usable() else None
        )

    # auto
    if MlxVlmBackend.available():
        mlx_id = (
            mid
            if mid.startswith("mlx-community/")
            else "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
        )
        return MlxVlmBackend(mlx_id)
    if TransformersVlmBackend.available() and _cuda_usable():
        hf = mid if not mid.startswith("mlx-community/") else _hf_vl_from_mlx_id(mid)
        return TransformersVlmBackend(hf, device="cuda")
    return None


def vlm_probe() -> dict[str, Any]:
    return {
        "backends": [b.__dict__ for b in probe_vlm_backends()],
        "mac_verify_hint": (
            "pip install -e '.[vlm-mlx]' && "
            "python scripts/dev_vlm_panel_map_verify.py --prefer mlx "
            "--synthetic --require-vlm"
        ),
        "cuda_verify_hint": (
            "pip install -e '.[vlm-cuda]' && "
            "python scripts/dev_vlm_panel_map_verify.py --prefer cuda "
            "--profile qwen2.5-vl-7b --synthetic --require-vlm"
        ),
    }
