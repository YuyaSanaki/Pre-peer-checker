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
def case_profile():
    """Public tests run on the built-in neutral profile, ignoring local overrides."""
    with use_case_profile(None) as prof:
        yield prof


@pytest.fixture
def sided_profile():
    with use_case_profile(SYNTHETIC_SIDES_PROFILE) as prof:
        yield prof
