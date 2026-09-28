"""Local LLM backends for Legend JSON (optional; rules remain the product main path).

Preference order when ``prefer=\"auto\"``:
  1. Apple MLX (``mlx_lm``) — Mac distribution path
  2. HuggingFace transformers on CUDA / CPU — DGX / Linux path
  3. None → caller falls back to rules-only extraction
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


@dataclass
class BackendInfo:
    name: str
    device: str
    model_id: str
    available: bool
    detail: str = ""


class LocalLLMBackend(ABC):
    """Minimal text-generation interface used by legend_extract."""

    @abstractmethod
    def info(self) -> BackendInfo: ...

    @abstractmethod
    def generate(self, prompt: str, *, max_tokens: int = 768) -> str: ...


class MLXBackend(LocalLLMBackend):
    def __init__(self, model_id: str = "mlx-community/Qwen2.5-7B-Instruct-4bit"):
        self.model_id = model_id
        self._model = None
        self._tokenizer = None

    @staticmethod
    def available() -> bool:
        try:
            import mlx.core  # noqa: F401
            import mlx_lm  # noqa: F401

            return True
        except Exception:
            return False

    def info(self) -> BackendInfo:
        return BackendInfo(
            name="mlx",
            device="mps/unified",
            model_id=self.model_id,
            available=self.available(),
        )

    def load(self) -> None:
        from mlx_lm import load

        if self._model is None:
            self._model, self._tokenizer = load(self.model_id)

    def generate(self, prompt: str, *, max_tokens: int = 768) -> str:
        from mlx_lm import generate

        self.load()
        return generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            verbose=False,
        )


class TransformersBackend(LocalLLMBackend):
    """CUDA (preferred) or CPU via transformers — DGX Spark path."""

    def __init__(
        self,
        model_id: str | None = None,
        *,
        device: str | None = None,
    ):
        self.model_id = model_id or os.environ.get(
            "PRE_PEER_CHECKER_LLM_MODEL",
            "Qwen/Qwen2.5-7B-Instruct",
        )
        self.device_override = device
        self._pipe = None
        self._tokenizer = None
        self._model = None
        self._device = "unknown"

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
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def info(self) -> BackendInfo:
        ok = self.available()
        device = "unknown"
        detail = ""
        if ok:
            try:
                device = self._resolve_device()
            except Exception:
                device = "error"
            # Device name is informational only; never overwrite a resolved device.
            if device == "cuda":
                try:
                    import torch

                    if torch.cuda.is_available() and torch.cuda.device_count() > 0:
                        detail = torch.cuda.get_device_name(0)
                except Exception:
                    detail = ""
        return BackendInfo(
            name="transformers",
            device=device,
            model_id=self.model_id,
            available=ok,
            detail=detail,
        )

    def load(self) -> None:
        if self._pipe is not None:
            return
        # Avoid torch.compile / Triton native-JIT side paths on generate.
        # TORCH_DISABLE_NATIVE_JIT keeps CUDA eager kernels (DGX Spark works
        # without python3-dev / Triton driver rebuild).
        os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
        os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
        os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

        try:
            import torch._dynamo

            torch._dynamo.config.disable = True
        except Exception:
            pass

        device = self._resolve_device()
        # Prefer explicit CPU when CUDA is advertised but unusable (no driver).
        if device == "cuda":
            try:
                torch.zeros(1, device="cuda")
            except Exception:
                device = "cpu"
        dtype = torch.float16 if device in {"cuda", "mps"} else torch.float32
        if device == "cuda":
            pipe_device: int | str = 0
        elif device == "mps":
            pipe_device = "mps"
        else:
            pipe_device = -1
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
        )
        # Keep model and tokenizer inputs on the same device for direct generate().
        self._model = self._model.to(device)
        self._pipe = pipeline(
            "text-generation",
            model=self._model,
            tokenizer=self._tokenizer,
            device=pipe_device,
        )
        self._device = device

    @staticmethod
    def _tensor_device(device: str) -> str:
        """Map backend device label to a torch .to() target."""
        if device in {"cuda", "mps", "cpu"}:
            return device
        return "cpu"

    def generate(self, prompt: str, *, max_tokens: int = 768) -> str:
        self.load()
        assert self._pipe is not None
        import torch
        from transformers import GenerationConfig

        tok = self._tokenizer or getattr(self._pipe, "tokenizer", None)
        model_input: str = prompt
        if tok is not None and hasattr(tok, "apply_chat_template"):
            try:
                # Do not split the prompt: system blocks embed JSON examples with
                # blank lines, so a naive "\\n\\n" cut corrupts the instruction.
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You extract structured figure legend metadata for "
                            "manuscript integrity checks. Follow the user "
                            "instructions exactly. Return ONLY valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
                model_input = tok.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            except Exception:
                model_input = prompt

        # Prefer direct generate() to avoid pipeline/Triton compile side paths.
        if tok is not None and getattr(self, "_model", None) is not None:
            inputs = tok(model_input, return_tensors="pt")
            target = self._tensor_device(self._device)
            inputs = {k: v.to(target) for k, v in inputs.items()}
            gen_cfg = GenerationConfig(
                max_new_tokens=max_tokens,
                do_sample=False,
                pad_token_id=getattr(tok, "pad_token_id", None)
                or getattr(tok, "eos_token_id", None),
            )
            with torch.inference_mode():
                out_ids = self._model.generate(**inputs, generation_config=gen_cfg)
            prompt_len = inputs["input_ids"].shape[-1]
            new_tokens = out_ids[0, prompt_len:]
            return str(tok.decode(new_tokens, skip_special_tokens=True))

        out = self._pipe(
            model_input,
            max_new_tokens=max_tokens,
            do_sample=False,
            return_full_text=False,
        )
        if isinstance(out, list) and out:
            text = out[0].get("generated_text") or out[0].get("text") or ""
            return str(text)
        return str(out)


class CallableBackend(LocalLLMBackend):
    """Test / stub backend wrapping a plain callable."""

    def __init__(self, fn: Callable[[str], str], name: str = "callable"):
        self._fn = fn
        self._name = name

    def info(self) -> BackendInfo:
        return BackendInfo(name=self._name, device="stub", model_id="callable", available=True)

    def generate(self, prompt: str, *, max_tokens: int = 768) -> str:
        return self._fn(prompt)


def probe_backends() -> list[BackendInfo]:
    return [
        MLXBackend().info(),
        TransformersBackend().info(),
    ]


def _cuda_usable() -> bool:
    """True when torch can allocate on a real CUDA device (DGX Spark / Linux GPU)."""
    try:
        import torch

        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            return False
        torch.zeros(1, device="cuda")
        return True
    except Exception:
        return False


def _hf_text_model_from_mlx_id(model_id: str) -> str:
    """Map MLX community ids to HuggingFace Instruct checkpoints for CUDA hosts."""
    mid = (model_id or "").strip()
    if mid.startswith("Qwen/") or mid.startswith("google/"):
        return mid
    low = mid.lower()
    if "32b" in low:
        return "Qwen/Qwen2.5-32B-Instruct"
    return os.environ.get("PRE_PEER_CHECKER_LLM_MODEL") or "Qwen/Qwen2.5-7B-Instruct"


def select_backend(
    prefer: str = "auto",
    *,
    model_id: str | None = None,
    profile_id: str | None = None,
) -> LocalLLMBackend | None:
    """Return a ready backend or None (rules-only).

    prefer: auto | mlx | cuda | transformers | none
    profile_id: registry profile (``llm/model_registry.yaml``). When set,
    supplies default model_id / prefer unless overridden.

    Host policy:
      - Mac + MLX → MLX
      - CUDA usable (DGX Spark / Linux GPU) → transformers on CUDA
      - else → transformers CPU / None
    """
    from pre_peer_checker.llm.registry import resolve_model

    prefer_in = (prefer or "auto").lower()
    if prefer_in in {"none", "off", "rules", "0", "false"}:
        return None

    resolved = resolve_model(
        role="text",
        profile_id=profile_id,
        model_id=model_id,
        prefer=None if prefer_in == "auto" else prefer_in,
    )
    mid = (model_id or resolved.model_id or "").strip() or (
        "mlx-community/Qwen2.5-7B-Instruct-4bit"
    )
    # Device preference: explicit CLI/API > profile.prefer > auto probe
    if prefer_in != "auto":
        prefer_l = prefer_in
    elif resolved.prefer in {"mlx", "cuda", "transformers", "hf"}:
        prefer_l = resolved.prefer
    else:
        prefer_l = "auto"

    if prefer_l == "mlx":
        if MLXBackend.available():
            return MLXBackend(mid)
        # Default Mac profile on a CUDA host (Spark): fall through to HF+CUDA.
        if TransformersBackend.available():
            return TransformersBackend(
                model_id=_hf_text_model_from_mlx_id(mid),
                device="cuda" if _cuda_usable() else None,
            )
        return None
    if prefer_l in {"cuda", "transformers", "hf"}:
        if not TransformersBackend.available():
            return None
        hf_mid = mid
        if mid.startswith("mlx-community/"):
            hf_mid = _hf_text_model_from_mlx_id(mid)
        want_cuda = prefer_l == "cuda" or _cuda_usable()
        return TransformersBackend(
            model_id=hf_mid,
            device="cuda" if want_cuda and _cuda_usable() else None,
        )
    # auto: MLX on Apple Silicon; else CUDA transformers when GPU works
    if MLXBackend.available():
        if not mid.startswith("mlx-community/"):
            # HF ids are not loadable via mlx_lm; use registry MLX default
            mid = "mlx-community/Qwen2.5-7B-Instruct-4bit"
        return MLXBackend(mid)
    if TransformersBackend.available():
        hf_mid = mid
        if mid.startswith("mlx-community/"):
            hf_mid = _hf_text_model_from_mlx_id(mid)
        return TransformersBackend(
            model_id=hf_mid,
            device="cuda" if _cuda_usable() else None,
        )
    return None
