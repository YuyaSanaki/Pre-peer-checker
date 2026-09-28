"""Usage profile (install.sh choice) → license-restricted component selection."""

from __future__ import annotations

import json

import pytest

from pre_peer_checker import usage_profile as up


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    path = tmp_path / "usage_profile.json"
    monkeypatch.setattr(up, "USAGE_FILE", path)
    monkeypatch.delenv(up.ENV_VAR, raising=False)
    return path


def test_missing_file_defaults_to_commercial(usage_file):
    assert up.current_usage() == up.COMMERCIAL
    assert up.lightglue_features() == "aliked"


def test_academic_file_selects_superpoint(usage_file):
    usage_file.write_text(json.dumps({"usage": "academic"}), encoding="utf-8")
    assert up.current_usage() == up.ACADEMIC
    assert up.lightglue_features() == "superpoint"


def test_invalid_file_falls_back_to_commercial(usage_file):
    usage_file.write_text("{not json", encoding="utf-8")
    assert up.current_usage() == up.COMMERCIAL
    usage_file.write_text(json.dumps({"usage": "maybe"}), encoding="utf-8")
    assert up.current_usage() == up.COMMERCIAL


def test_env_overrides_file(usage_file, monkeypatch):
    usage_file.write_text(json.dumps({"usage": "academic"}), encoding="utf-8")
    monkeypatch.setenv(up.ENV_VAR, "commercial")
    assert up.lightglue_features() == "aliked"


def test_explicit_usage_argument():
    assert up.lightglue_features("academic") == "superpoint"
    assert up.lightglue_features("commercial") == "aliked"
