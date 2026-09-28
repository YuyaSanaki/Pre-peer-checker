"""Bundled static assets (pattern registry, etc.) for frozen / wheel installs."""

from __future__ import annotations

from importlib import resources
from pathlib import Path


def patterns_json_path() -> Path:
    """Return path to shipped pubpeer_patterns.json (works in frozen apps)."""
    ref = resources.files("pre_peer_checker.resources").joinpath("pubpeer_patterns.json")
    with resources.as_file(ref) as p:
        return Path(p)
