"""Model registry / profile selection."""

from __future__ import annotations

from pre_peer_checker.eval.model_bakeoff import build_bakeoff_plan
from pre_peer_checker.llm.backend import select_backend
from pre_peer_checker.llm.registry import (
    get_profile,
    list_profiles,
    load_registry,
    resolve_model,
)


def test_registry_loads_defaults():
    profiles, default_llm, default_vlm = load_registry()
    assert default_llm == "qwen2.5-32b-mlx"
    assert default_vlm == "qwen2.5-vl-7b"
    assert "qwen2.5-32b-mlx" in profiles
    assert "qwen2.5-7b-mlx" in profiles
    assert "qwen2.5-7b-hf" in profiles
    assert "qwen2.5-vl-7b" in profiles
    assert "gemma3-12b" not in profiles
    # Only Apache-2.0 models are offered (no Qwen Research / Qwen License / InternVL).
    for pid in ("qwen2.5-3b-mlx", "qwen2.5-72b-hf", "internvl3-8b", "internvl3-38b"):
        assert pid not in profiles


def test_list_profiles_by_role():
    text = list_profiles("text")
    vision = list_profiles("vision")
    assert all(p.role == "text" for p in text)
    assert all(p.role == "vision" for p in vision)
    assert any(p.id == "qwen2.5-32b-hf" for p in text)
    assert any(p.id.startswith("qwen2.5-vl") for p in vision)
    assert not any(p.family == "gemma" for p in text + vision)


def test_resolve_qwen_hf_profile():
    r = resolve_model(role="text", profile_id="qwen2.5-7b-hf")
    assert r.model_id == "Qwen/Qwen2.5-7B-Instruct"
    assert r.prefer == "transformers"
    assert r.family == "qwen"


def test_resolve_model_id_override():
    r = resolve_model(
        role="text",
        profile_id="qwen2.5-7b-mlx",
        model_id="custom/override",
    )
    assert r.model_id == "custom/override"
    assert r.source == "override"


def test_select_backend_respects_none_with_profile():
    assert select_backend("none", profile_id="qwen2.5-7b-hf") is None


def test_select_backend_mlx_falls_back_to_cuda_transformers(monkeypatch):
    """DGX Spark: default Mac MLX profile must not hard-fail when MLX missing."""
    from pre_peer_checker.llm import backend as be

    monkeypatch.setattr(be.MLXBackend, "available", staticmethod(lambda: False))
    monkeypatch.setattr(be.TransformersBackend, "available", staticmethod(lambda: True))
    monkeypatch.setattr(be, "_gpu_device", lambda: "cuda")
    selected = select_backend("auto", profile_id="qwen2.5-7b-mlx")
    assert selected is not None
    assert isinstance(selected, be.TransformersBackend)
    assert selected.model_id == "Qwen/Qwen2.5-7B-Instruct"
    assert selected.device_override == "cuda"


def test_bakeoff_plan_smoke(monkeypatch):
    # Avoid loading mlx/torch in CI/sandbox (can abort the process).
    monkeypatch.setattr(
        "pre_peer_checker.eval.model_bakeoff.select_backend",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "pre_peer_checker.eval.model_bakeoff.probe_backends",
        lambda: [],
    )
    monkeypatch.setattr(
        "pre_peer_checker.llm.vlm_backend.select_vlm_backend",
        lambda *a, **k: None,
    )
    plan = build_bakeoff_plan(["qwen2.5-7b-mlx", "qwen2.5-7b-hf", "qwen2.5-vl-7b"])
    assert plan["n_profiles"] == 3
    assert len(plan["rows"]) == 3
    ids = {
        (row.get("profile") or {}).get("id")
        for row in plan["rows"]
    }
    assert "qwen2.5-7b-hf" in ids
    vlm_row = next(
        r for r in plan["rows"] if (r.get("profile") or {}).get("id") == "qwen2.5-vl-7b"
    )
    assert vlm_row.get("backend_available") is False


def test_get_profile_unknown():
    assert get_profile("does-not-exist") is None


def test_effective_llm_profile_non_mlx_uses_hf(monkeypatch):
    from pre_peer_checker.llm import backend as be
    from pre_peer_checker.llm.registry import (
        distribution_legend_llm_kwargs,
        effective_llm_profile_id,
    )

    monkeypatch.setattr(be.MLXBackend, "available", staticmethod(lambda: False))
    assert effective_llm_profile_id() == "qwen2.5-32b-hf"
    kw = distribution_legend_llm_kwargs()
    assert kw["legend_llm_profile"] == "qwen2.5-32b-hf"
    assert kw["legend_llm_prefer"] == "cuda"
    assert kw["legend_llm_prefer"] != "none"


def test_probe_backends_reports_profile_model(monkeypatch):
    from pre_peer_checker.llm import backend as be

    monkeypatch.delenv("PRE_PEER_CHECKER_LLM_PROFILE", raising=False)
    monkeypatch.setattr(be.MLXBackend, "available", staticmethod(lambda: True))
    by_name = {b.name: b.model_id for b in be.probe_backends()}
    assert by_name["mlx"] == "mlx-community/Qwen2.5-32B-Instruct-4bit"
    assert by_name["transformers"] == "Qwen/Qwen2.5-32B-Instruct"

    by_name = {b.name: b.model_id for b in be.probe_backends(profile_id="qwen2.5-7b-mlx")}
    assert by_name["mlx"] == "mlx-community/Qwen2.5-7B-Instruct-4bit"
    assert by_name["transformers"] == "Qwen/Qwen2.5-7B-Instruct"

    by_name = {b.name: b.model_id for b in be.probe_backends(profile_id="qwen2.5-32b-hf")}
    assert by_name["mlx"] == "mlx-community/Qwen2.5-32B-Instruct-4bit"
    assert by_name["transformers"] == "Qwen/Qwen2.5-32B-Instruct"
