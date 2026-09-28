"""Bundled resources for frozen / wheel installs."""

from pathlib import Path

from pre_peer_checker.resources import patterns_json_path


def test_patterns_json_shipped():
    path = patterns_json_path()
    assert path.is_file()
    data = path.read_text(encoding="utf-8")
    assert "P-DATA-SWAP-CROSS-CONDITION" in data
    assert "P-IMAGE-REUSE-UNCITED" in data


def test_resources_package_importable():
    import pre_peer_checker.resources as res

    assert Path(res.__file__).parent.joinpath("pubpeer_patterns.json").is_file()
