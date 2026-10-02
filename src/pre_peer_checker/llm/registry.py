"""LLM/VLM profile registry — selectable models for development and distribution."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml import YAML

Role = Literal["text", "vision"]


@dataclass(frozen=True)
class ModelProfile:
    id: str
    family: str
    role: Role
    label: str
    model_id: str
    prefer: str
    recommended_vram_gb: float | None = None
    size_hint: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedModel:
    """Concrete backend selection after profile + overrides."""

    profile_id: str | None
    model_id: str
    prefer: str
    role: Role
    label: str
    family: str = ""
    notes: str = ""
    source: str = "default"  # profile | override | env | default

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _registry_path() -> Path:
    return Path(__file__).resolve().parent / "model_registry.yaml"


def _load_raw(path: Path | None = None) -> dict[str, Any]:
    yaml = YAML(typ="safe")
    p = path or _registry_path()
    data = yaml.load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid model registry: {p}")
    return data


@lru_cache(maxsize=4)
def _cached_registry(path_str: str) -> tuple[dict[str, ModelProfile], str, str]:
    data = _load_raw(Path(path_str))
    profiles: dict[str, ModelProfile] = {}
    raw_profiles = data.get("profiles") or {}
    if not isinstance(raw_profiles, dict):
        raise ValueError("registry.profiles must be a mapping")
    for pid, meta in raw_profiles.items():
        if not isinstance(meta, dict):
            continue
        role = str(meta.get("role") or "text").lower()
        if role not in {"text", "vision"}:
            role = "text"
        prefer = str(meta.get("prefer") or "auto").lower()
        profiles[str(pid)] = ModelProfile(
            id=str(pid),
            family=str(meta.get("family") or ""),
            role=role,  # type: ignore[arg-type]
            label=str(meta.get("label") or pid),
            model_id=str(meta.get("model_id") or ""),
            prefer=prefer,
            recommended_vram_gb=(
                float(meta["recommended_vram_gb"])
                if meta.get("recommended_vram_gb") is not None
                else None
            ),
            size_hint=str(meta.get("size_hint") or ""),
            notes=str(meta.get("notes") or ""),
        )
    default_llm = str(data.get("default_llm_profile") or "qwen2.5-32b-mlx")
    default_vlm = str(data.get("default_vlm_profile") or "qwen2.5-vl-7b")
    return profiles, default_llm, default_vlm


def clear_registry_cache() -> None:
    _cached_registry.cache_clear()


def load_registry(path: Path | None = None) -> tuple[dict[str, ModelProfile], str, str]:
    """Return (profiles, default_llm_profile_id, default_vlm_profile_id)."""
    p = path or _registry_path()
    return _cached_registry(str(p.resolve()))


def list_profiles(
    role: Role | None = None,
    *,
    path: Path | None = None,
) -> list[ModelProfile]:
    profiles, _, _ = load_registry(path)
    items = list(profiles.values())
    if role is not None:
        items = [p for p in items if p.role == role]
    return sorted(items, key=lambda p: (p.family, p.recommended_vram_gb or 0, p.id))


def get_profile(profile_id: str, *, path: Path | None = None) -> ModelProfile | None:
    profiles, _, _ = load_registry(path)
    return profiles.get(profile_id)


def default_profile_id(role: Role = "text", *, path: Path | None = None) -> str:
    env_key = "PRE_PEER_CHECKER_LLM_PROFILE" if role == "text" else "PRE_PEER_CHECKER_VLM_PROFILE"
    env = (os.environ.get(env_key) or "").strip()
    if env:
        return env
    _, default_llm, default_vlm = load_registry(path)
    return default_llm if role == "text" else default_vlm


MLX_PREFERS = frozenset({"mlx"})
HF_PREFERS = frozenset({"transformers", "cuda", "hf"})


@dataclass(frozen=True)
class ProfileSelection:
    """Host-aware text profile choice and why it was made."""

    profile_id: str
    preferred_profile_id: str
    source: str  # env | memory | default
    memory_gb: float | None = None
    memory_kind: str = ""  # unified | gpu

    @property
    def downgraded(self) -> bool:
        return self.profile_id != self.preferred_profile_id

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "downgraded": self.downgraded}


def _mlx_available() -> bool:
    try:
        from pre_peer_checker.llm.backend import MLXBackend

        return MLXBackend.available()
    except Exception:
        return False


@lru_cache(maxsize=1)
def _unified_memory_gb() -> float | None:
    """Installed RAM (Apple Silicon: unified memory shared with the GPU)."""
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        return None


def _gpu_memory_gb() -> float | None:
    """VRAM of the torch GPU, capped by ``PRE_PEER_CHECKER_GPU_MAX_MEMORY_GB``."""
    try:
        from pre_peer_checker.accel import device_summary, gpu_device, gpu_memory_cap_gb

        if gpu_device() is None:
            return None
        mem = device_summary().get("memory_gb")
    except Exception:
        return None
    cap = gpu_memory_cap_gb()
    if mem is None:
        return cap
    return min(mem, cap) if cap is not None else mem


def _fit_to_memory(
    profiles: dict[str, ModelProfile],
    preferred: str,
    prefers: frozenset[str],
    memory_gb: float | None,
    memory_kind: str,
) -> ProfileSelection:
    """Downgrade ``preferred`` to the largest same-backend text profile that fits in memory.

    ``recommended_vram_gb`` is the threshold: installed unified memory for MLX profiles,
    GPU memory for HF profiles. Never upgrades beyond ``preferred``.
    """
    pref = profiles.get(preferred)
    if memory_gb is None or pref is None or pref.recommended_vram_gb is None:
        return ProfileSelection(preferred, preferred, "default", memory_gb, memory_kind)
    if pref.recommended_vram_gb <= memory_gb:
        return ProfileSelection(preferred, preferred, "memory", memory_gb, memory_kind)
    smaller = [
        p
        for p in profiles.values()
        if p.role == "text"
        and p.prefer in prefers
        and p.recommended_vram_gb is not None
        and p.recommended_vram_gb < pref.recommended_vram_gb
    ]
    if not smaller:
        return ProfileSelection(preferred, preferred, "memory", memory_gb, memory_kind)
    fitting = [p for p in smaller if p.recommended_vram_gb <= memory_gb]  # type: ignore[operator]
    chosen = (
        max(fitting, key=lambda p: p.recommended_vram_gb or 0)
        if fitting
        else min(smaller, key=lambda p: p.recommended_vram_gb or 0)
    )
    return ProfileSelection(chosen.id, preferred, "memory", memory_gb, memory_kind)


def select_llm_profile(*, path: Path | None = None) -> ProfileSelection:
    """Host-aware text profile: Mac/MLX → 32b-mlx, else CUDA 本線 32b-hf — sized to memory.

    メモリが推奨量に届かなければ同じバックエンドの軽量版（7B）へ落とす。
    ``PRE_PEER_CHECKER_LLM_PROFILE`` があればそれを最優先する。
    """
    env = (os.environ.get("PRE_PEER_CHECKER_LLM_PROFILE") or "").strip()
    if env:
        return ProfileSelection(env, env, "env")
    profiles, _, _ = load_registry(path)
    if _mlx_available():
        return _fit_to_memory(
            profiles,
            default_profile_id("text", path=path),
            MLX_PREFERS,
            _unified_memory_gb(),
            "unified",
        )
    # Non-Mac / MLX 無し: マシンパワーを活かす CUDA・HF 本線
    preferred = (
        "qwen2.5-32b-hf" if "qwen2.5-32b-hf" in profiles else default_profile_id("text", path=path)
    )
    return _fit_to_memory(profiles, preferred, HF_PREFERS, _gpu_memory_gb(), "gpu")


def effective_llm_profile_id(*, path: Path | None = None) -> str:
    """Profile id of :func:`select_llm_profile`."""
    return select_llm_profile(path=path).profile_id


def effective_vlm_profile_id(*, path: Path | None = None) -> str:
    """Host-aware vision profile: mlx-vlm あり → 既定の ``*-mlx`` 版; else registry default."""
    env = (os.environ.get("PRE_PEER_CHECKER_VLM_PROFILE") or "").strip()
    if env:
        return env
    default = default_profile_id("vision", path=path)
    try:
        from pre_peer_checker.llm.vlm_backend import MlxVlmBackend

        if MlxVlmBackend.available():
            profiles, _, _ = load_registry(path)
            mlx_variant = f"{default}-mlx"
            if mlx_variant in profiles:
                return mlx_variant
    except Exception:
        pass
    return default


def distribution_legend_llm_kwargs(*, path: Path | None = None) -> dict[str, Any]:
    """Legend LLM kwargs for shipping / gold gates (always prefer real inference when possible).

    - MLX あり → ``qwen2.5-32b-mlx`` + prefer auto
    - それ以外 → ``qwen2.5-32b-hf`` + prefer cuda（高精度。prefer=none にはしない）
    """
    pid = effective_llm_profile_id(path=path)
    profile = get_profile(pid, path=path)
    prefer = "auto"
    if profile is not None:
        prefer = profile.prefer or "auto"
    if pid.endswith("-hf") or (profile and profile.prefer in {"transformers", "cuda", "hf"}):
        prefer = "cuda"
    elif pid.endswith("-mlx") or (profile and profile.prefer == "mlx"):
        prefer = "auto"
    return {
        "legend_llm": True,
        "legend_llm_profile": pid,
        "legend_llm_prefer": prefer,
    }


def resolve_model(
    *,
    role: Role = "text",
    profile_id: str | None = None,
    model_id: str | None = None,
    prefer: str | None = None,
    path: Path | None = None,
) -> ResolvedModel:
    """Resolve profile + optional overrides into a concrete model selection.

    Precedence for model_id / prefer:
      explicit override > profile > role default profile > built-in fallback
    """
    profiles, default_llm, default_vlm = load_registry(path)
    built_in_model = (
        "mlx-community/Qwen2.5-7B-Instruct-4bit"
        if role == "text"
        else "Qwen/Qwen2.5-VL-7B-Instruct"
    )
    built_in_prefer = "auto"

    pid = (profile_id or "").strip() or None
    source = "override"
    if pid is None and not (model_id and prefer):
        pid = (
            effective_llm_profile_id(path=path)
            if role == "text"
            else effective_vlm_profile_id(path=path)
        )
        source = "env" if os.environ.get(
            "PRE_PEER_CHECKER_LLM_PROFILE" if role == "text" else "PRE_PEER_CHECKER_VLM_PROFILE"
        ) else "default"

    profile = profiles.get(pid) if pid else None
    if profile is None and pid:
        # Unknown profile id — keep id for diagnostics, fall back carefully
        return ResolvedModel(
            profile_id=pid,
            model_id=(model_id or "").strip() or built_in_model,
            prefer=(prefer or built_in_prefer).strip().lower() or built_in_prefer,
            role=role,
            label=pid,
            notes=f"unknown profile {pid!r}; using overrides/fallback",
            source="unknown_profile",
        )

    if profile is not None:
        resolved_model = (model_id or "").strip() or profile.model_id
        resolved_prefer = (prefer or "").strip().lower() or profile.prefer
        if model_id or prefer:
            source = "override"
        elif source not in {"env", "default"}:
            source = "profile"
        return ResolvedModel(
            profile_id=profile.id,
            model_id=resolved_model or built_in_model,
            prefer=resolved_prefer or built_in_prefer,
            role=profile.role,
            label=profile.label,
            family=profile.family,
            notes=profile.notes,
            source=source if source in {"env", "default", "override"} else "profile",
        )

    # No profile: pure overrides or built-in
    return ResolvedModel(
        profile_id=default_llm if role == "text" else default_vlm,
        model_id=(model_id or "").strip() or built_in_model,
        prefer=(prefer or built_in_prefer).strip().lower() or built_in_prefer,
        role=role,
        label="custom" if model_id else "built-in",
        notes="",
        source="override" if model_id or prefer else "default",
    )


def registry_public_dict(*, path: Path | None = None) -> dict[str, Any]:
    """JSON-serializable catalog for WebUI /api/llm-status."""
    profiles, default_llm, default_vlm = load_registry(path)
    return {
        "version": 1,
        "default_llm_profile": default_llm,
        "default_vlm_profile": default_vlm,
        "profiles": [p.to_dict() for p in list_profiles(path=path)],
        "text_profiles": [p.to_dict() for p in list_profiles("text", path=path)],
        "vision_profiles": [p.to_dict() for p in list_profiles("vision", path=path)],
    }
