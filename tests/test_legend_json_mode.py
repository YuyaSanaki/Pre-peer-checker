"""6A Legend JSON mode (Outlines / free+coerce fallback)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from pre_peer_checker.llm.backend import CallableBackend
from pre_peer_checker.llm.json_mode import (
    json_mode_probe,
    outlines_available,
    structured_legend_generate,
)
from pre_peer_checker.llm.legend_extract import extract_legends_with_backend
from pre_peer_checker.llm.legend_schema import parse_legend_llm_response


def test_json_mode_probe_shape():
    probe = json_mode_probe()
    assert "outlines_available" in probe
    assert "backends" in probe
    assert "mac_verify_hint" in probe
    assert isinstance(outlines_available(), bool)


def test_structured_generate_callable_falls_back_to_free_coerce():
    payload = {
        "figure": "Figure 1",
        "panels": [{"panel": "F", "n": 12, "groups": ["WT"], "notes": ""}],
        "tests": ["Welch"],
        "p_values": [0.01],
        "citation": {"mentioned": False, "reproduced_from": None, "spans": []},
        "raw_excerpt": "n=12 (F)",
        "extractor": "llm",
    }

    def fake(prompt: str) -> str:
        assert "Figure" in prompt or "extract" in prompt.lower() or "n=" in prompt
        return json.dumps(payload)

    backend = CallableBackend(fake)
    text, meta = structured_legend_generate(backend, "extract legend n=12 (F)")
    assert meta["json_mode"] == "free+coerce"
    parsed = parse_legend_llm_response(text)
    assert parsed is not None
    assert any(p.panel.upper() == "F" and p.n == 12 for p in parsed.panels)


def test_structured_generate_records_outlines_mlx_when_helper_returns(monkeypatch):
    from pre_peer_checker.llm import json_mode as jm

    class FakeMLX:
        def info(self):
            return SimpleNamespace(name="mlx")

        def load(self):
            self._model = object()
            self._tokenizer = object()

        def generate(self, prompt: str, *, max_tokens: int = 768) -> str:
            raise AssertionError("should use outlines path")

    monkeypatch.setattr(
        jm,
        "_try_outlines_mlxlm",
        lambda *a, **k: json.dumps(
            {
                "figure": "Figure 1",
                "panels": [{"panel": "N", "n": 17, "groups": [], "notes": ""}],
                "tests": [],
                "p_values": [],
                "citation": {"mentioned": False, "reproduced_from": None, "spans": []},
            }
        ),
    )
    backend = FakeMLX()
    text, meta = structured_legend_generate(backend, "prompt")
    assert meta["json_mode"] == "outlines-mlx"
    parsed = parse_legend_llm_response(text)
    assert parsed is not None
    assert any(p.n == 17 for p in parsed.panels)


def test_extract_legends_with_backend_records_json_mode(tmp_path, monkeypatch):
    """Wiring: extract_legends_with_backend sets meta.json_mode via structured path."""
    from docx import Document

    import pre_peer_checker.llm.backend as be
    from pre_peer_checker.llm.legend_extract import extract_legends_with_backend

    docx = tmp_path / "legend.docx"
    d = Document()
    d.add_paragraph("Figure 1. n=5 (A). Welch's t-test.")
    d.save(docx)

    class FakeBackend:
        def info(self):
            return SimpleNamespace(
                name="callable", device="stub", model_id="x", available=True, detail=""
            )

        def generate(self, prompt: str, *, max_tokens: int = 768) -> str:
            return json.dumps(
                {
                    "figure": "Figure 1",
                    "panels": [{"panel": "A", "n": 5, "groups": [], "notes": ""}],
                    "tests": ["Welch"],
                    "p_values": [],
                    "citation": {"mentioned": False, "reproduced_from": None, "spans": []},
                }
            )

    monkeypatch.setattr(be, "select_backend", lambda *a, **k: FakeBackend())
    monkeypatch.setattr(be, "probe_backends", lambda: [])

    _items, meta = extract_legends_with_backend(docx, enabled=True, prefer="auto")
    assert meta.get("json_mode") == "free+coerce"
    assert meta.get("json_modes")
    assert meta.get("json_mode_outlines") is False
