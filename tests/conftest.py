from __future__ import annotations

import pytest

from pre_peer_checker.engine.case_profile import use_case_profile

# Neutral two-arm vocabulary for synthetic side-aware tests (plot↔table, residue, H2b).
SYNTHETIC_SIDES_PROFILE = {
    "sides": [
        {
            "name": "alphaexp",
            "tokens": ["alphaexp"],
            "figure": "1",
            "figure_tokens": ["fig1c"],
            "panels": ["B", "C", "D"],
        },
        {
            "name": "betaexp",
            "tokens": ["betaexp"],
            "figure": "1",
            "figure_tokens": ["fig1h"],
            "panels": ["F", "G", "H"],
        },
    ],
    "panel_group_keys": {
        "B": ["0", "wt"],
        "C": ["1", "cont"],
        "D": ["2", "mutx"],
        "G": ["1", "cont"],
        "H": ["2", "mutxkd"],
    },
    "residue_folder_tokens": ["plotdump"],
    "figure_alias_tokens": {"1": ["plotdump"]},
    "filename_group_rules": [["mutxkd", "mutxkd"], ["mutx", "mutx"]],
}


@pytest.fixture(autouse=True)
def _disable_heavy_raster_ocr(monkeypatch):
    """Florence-2 raster OCR is production-only; tests must not download/run it.

    Tests that exercise the OCR path pass ``raster_fallback=True`` and stub the
    engine. Opt in to the real auto path with ``raster_ocr_auto``.
    """
    monkeypatch.setenv("PRE_PEER_CHECKER_RASTER_PANEL_OCR", "0")


@pytest.fixture(autouse=True)
def _large_host_memory(monkeypatch):
    """Default-profile tests assume a host big enough for the 32B models."""
    from pre_peer_checker.llm import registry

    monkeypatch.setattr(registry, "_unified_memory_gb", lambda: 128.0)
    monkeypatch.setattr(registry, "_gpu_memory_gb", lambda: 128.0)


@pytest.fixture
def raster_ocr_auto(monkeypatch):
    monkeypatch.delenv("PRE_PEER_CHECKER_RASTER_PANEL_OCR", raising=False)


@pytest.fixture(autouse=True)
def case_profile():
    """Public tests run on the built-in neutral profile, ignoring local overrides."""
    with use_case_profile(None) as prof:
        yield prof


@pytest.fixture
def sided_profile():
    with use_case_profile(SYNTHETIC_SIDES_PROFILE) as prof:
        yield prof
