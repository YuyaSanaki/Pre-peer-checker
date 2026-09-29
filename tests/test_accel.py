"""Shared torch accelerator selection (CUDA / ROCm / XPU / MPS / CPU)."""

from __future__ import annotations

from typing import ClassVar

import pytest

from pre_peer_checker import accel


@pytest.fixture(autouse=True)
def _fresh_cache():
    accel.clear_cache()
    yield
    accel.clear_cache()


def test_forced_device_env(monkeypatch):
    monkeypatch.setenv("PRE_PEER_CHECKER_DEVICE", "xpu")
    assert accel.torch_device() == "xpu"
    assert accel.gpu_device() == "xpu"


def test_mps_is_not_a_gpu_device(monkeypatch):
    monkeypatch.setenv("PRE_PEER_CHECKER_DEVICE", "mps")
    assert accel.gpu_device() is None


class _FakeModel:
    def __init__(self, device_map):
        self.device_map = device_map
        self.moved_to = None

    def to(self, device):
        self.moved_to = device
        return self


class _FakeCls:
    calls: ClassVar[list] = []

    @classmethod
    def from_pretrained(cls, model_id, device_map=None, **kwargs):
        cls.calls.append(device_map)
        if device_map == {"": "cuda"}:
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        return _FakeModel(device_map)


def test_load_pretrained_spills_to_cpu_on_oom():
    _FakeCls.calls = []
    model = accel.load_pretrained(_FakeCls, "m", device="cuda")
    assert _FakeCls.calls == [{"": "cuda"}, "auto"]
    assert model.device_map == "auto"


def test_load_pretrained_respects_gpu_memory_cap(monkeypatch):
    seen = {}

    class Capped:
        @classmethod
        def from_pretrained(cls, model_id, device_map=None, max_memory=None, **kwargs):
            seen.update(device_map=device_map, max_memory=max_memory)
            return _FakeModel(device_map)

    monkeypatch.setenv("PRE_PEER_CHECKER_GPU_MAX_MEMORY_GB", "8")
    accel.load_pretrained(Capped, "m", device="cuda")
    assert seen["device_map"] == "auto"
    assert seen["max_memory"][0] == "8.0GiB"
    assert "cpu" in seen["max_memory"]


def test_load_pretrained_reraises_non_oom():
    class Broken:
        @classmethod
        def from_pretrained(cls, *_a, **_k):
            raise ValueError("bad checkpoint")

    with pytest.raises(ValueError):
        accel.load_pretrained(Broken, "m", device="cuda")


def test_load_pretrained_cpu_uses_to():
    _FakeCls.calls = []
    model = accel.load_pretrained(_FakeCls, "m", device="cpu")
    assert _FakeCls.calls == [None]
    assert model.moved_to == "cpu"


def test_select_backend_uses_xpu(monkeypatch):
    from pre_peer_checker.llm import backend as be

    monkeypatch.setattr(be.MLXBackend, "available", staticmethod(lambda: False))
    monkeypatch.setattr(be.TransformersBackend, "available", staticmethod(lambda: True))
    monkeypatch.setattr(be, "_gpu_device", lambda: "xpu")
    selected = be.select_backend("auto", profile_id="qwen2.5-7b-hf")
    assert isinstance(selected, be.TransformersBackend)
    assert selected.device_override == "xpu"
    assert be.TransformersBackend._tensor_device("xpu") == "xpu"


def test_select_vlm_backend_auto_on_any_gpu(monkeypatch):
    from pre_peer_checker.llm import vlm_backend as vb

    monkeypatch.setattr(vb.MlxVlmBackend, "available", staticmethod(lambda: False))
    monkeypatch.setattr(vb.TransformersVlmBackend, "available", staticmethod(lambda: True))
    monkeypatch.setattr(vb, "_gpu_device", lambda: "xpu")
    selected = vb.select_vlm_backend("auto", profile_id="qwen2.5-vl-7b")
    assert isinstance(selected, vb.TransformersVlmBackend)
    assert selected.device_override == "xpu"

    monkeypatch.setattr(vb, "_gpu_device", lambda: None)
    cpu = vb.select_vlm_backend("auto", profile_id="qwen2.5-vl-7b")
    assert isinstance(cpu, vb.TransformersVlmBackend)
    assert cpu.device_override is None
