"""Case root layout (manuscript/ + data/) and WebUI API."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pre_peer_checker.web.case_layout import validate_case_root


def test_validate_case_root_missing_subdirs(tmp_path: Path) -> None:
    root = tmp_path / "case"
    root.mkdir()
    v = validate_case_root(root)
    assert v.ok is False
    assert v.error is not None
    assert "manuscript/" in v.error
    assert "data/" in v.error


def test_validate_case_root_missing_data_only(tmp_path: Path) -> None:
    root = tmp_path / "case"
    (root / "manuscript").mkdir(parents=True)
    v = validate_case_root(root)
    assert v.ok is False
    assert "data/" in (v.error or "")


def test_validate_case_root_ok(tmp_path: Path) -> None:
    root = tmp_path / "case"
    manuscript = root / "manuscript"
    data = root / "data"
    manuscript.mkdir(parents=True)
    data.mkdir(parents=True)
    v = validate_case_root(root)
    assert v.ok is True
    assert v.inputs == [manuscript.resolve(), data.resolve()]


def _make_demo_case(tmp_path: Path) -> Path:
    """Copy synthetic demo files into manuscript/ + data/ layout."""
    demo = Path("fixtures/synthetic/demo")
    root = tmp_path / "demo_case"
    manuscript = root / "manuscript"
    data = root / "data"
    manuscript.mkdir(parents=True)
    data.mkdir(parents=True)
    shutil.copy(demo / "legend_n.json", manuscript / "legend_n.json")
    shutil.copy(demo / "quant_a.csv", data / "quant_a.csv")
    shutil.copy(demo / "quant_b.csv", data / "quant_b.csv")
    return root


def test_web_api_validate_and_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pre_peer_checker.web.app import create_app

    root = _make_demo_case(tmp_path)
    out_html = tmp_path / "report.html"
    out_json = tmp_path / "warnings.json"
    monkeypatch.setattr("pre_peer_checker.web.app.DEFAULT_REPORT", out_html)
    monkeypatch.setattr("pre_peer_checker.web.app.DEFAULT_JSON", out_json)

    client = TestClient(create_app())
    bad = client.post("/api/validate-root", json={"root": str(tmp_path / "missing")})
    assert bad.status_code == 200
    assert bad.json()["ok"] is False

    ok = client.post("/api/validate-root", json={"root": str(root)})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["root"] == str(root.resolve())

    run = client.post("/api/run", json={"root": str(root)})
    assert run.status_code == 200
    payload = run.json()
    assert payload["ok"] is True
    assert payload["n_warnings"] >= 1
    assert out_html.is_file()

    report = client.get("/api/report")
    assert report.status_code == 200
    assert "html" in report.headers.get("content-type", "").lower()


def test_web_api_llm_status() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pre_peer_checker.web.app import create_app

    client = TestClient(create_app())
    res = client.get("/api/llm-status")
    assert res.status_code == 200
    body = res.json()
    assert "available" in body
    assert "backends" in body
    assert "text_profiles" in body
    assert "vision_profiles" in body
    assert body["default_llm_profile"] == "qwen2.5-7b-mlx"
    assert "effective_llm_profile" in body
    assert body["effective_llm_profile"] in {"qwen2.5-7b-mlx", "qwen2.5-7b-hf"}
    assert any(p["id"] == "qwen2.5-7b-hf" for p in body["text_profiles"])
    assert not any(str(p.get("id", "")).startswith("gemma") for p in body["text_profiles"])


def test_web_api_run_passes_legend_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pre_peer_checker.gui.worker import GuiRunConfig, GuiRunResult
    from pre_peer_checker.web.app import create_app

    root = _make_demo_case(tmp_path)
    captured: dict[str, GuiRunConfig] = {}

    def fake_job(config: GuiRunConfig) -> GuiRunResult:
        captured["cfg"] = config
        return GuiRunResult(
            ok=True,
            n_warnings=0,
            report_path=tmp_path / "r.html",
            json_path=None,
            warning_rows=[],
            coverage_lines=["stub"],
        )

    monkeypatch.setattr("pre_peer_checker.web.app.run_verification_job", fake_job)
    client = TestClient(create_app())
    res = client.post(
        "/api/run",
        json={
            "root": str(root),
            "legend_llm": True,
            "legend_llm_prefer": "none",
            "legend_llm_profile": "qwen2.5-7b-hf",
            "vlm_profile": "qwen2.5-vl-7b",
        },
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert captured["cfg"].legend_llm is True
    assert captured["cfg"].legend_llm_prefer == "none"
    assert captured["cfg"].legend_llm_profile == "qwen2.5-7b-hf"
    assert captured["cfg"].vlm_profile == "qwen2.5-vl-7b"


def test_web_api_corpus_ingest_and_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("fitz")
    import fitz
    from fastapi.testclient import TestClient

    from pre_peer_checker.gui.worker import GuiRunConfig, GuiRunResult
    from pre_peer_checker.web.app import create_app

    lib = tmp_path / "past_papers"
    monkeypatch.setattr(
        "pre_peer_checker.imaging.past_paper_ingest.DEFAULT_LIBRARY_DIR", lib
    )

    # Build a PDF with an embedded image
    pdf_path = tmp_path / "prior.pdf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), 1)
    pix.set_rect(pix.irect, (10, 20, 30, 255))
    page.insert_image(fitz.Rect(20, 20, 200, 200), stream=pix.tobytes("png"))
    doc.save(pdf_path)
    doc.close()

    root = _make_demo_case(tmp_path)
    captured: dict[str, GuiRunConfig] = {}

    def fake_job(config: GuiRunConfig) -> GuiRunResult:
        captured["cfg"] = config
        return GuiRunResult(
            ok=True,
            n_warnings=0,
            report_path=tmp_path / "r.html",
            json_path=None,
            warning_rows=[],
            coverage_lines=["stub"],
        )

    monkeypatch.setattr("pre_peer_checker.web.app.run_verification_job", fake_job)
    client = TestClient(create_app())

    empty = client.get("/api/corpus/list")
    assert empty.status_code == 200
    assert empty.json()["n_entries"] == 0

    with pdf_path.open("rb") as fh:
        ingest = client.post(
            "/api/corpus/ingest-pdf",
            files={"file": ("prior.pdf", fh, "application/pdf")},
        )
    assert ingest.status_code == 200
    body = ingest.json()
    assert body["ok"] is True
    assert body["n_figures"] >= 1
    entry_id = body["entry"]["id"]

    listed = client.get("/api/corpus/list")
    assert listed.json()["n_entries"] == 1

    run = client.post(
        "/api/run",
        json={"root": str(root), "legend_llm": False, "corpus_ids": [entry_id]},
    )
    assert run.status_code == 200
    assert run.json()["ok"] is True
    assert len(captured["cfg"].corpus) == 1
    assert captured["cfg"].corpus[0].name == "figures"
    assert any(captured["cfg"].corpus[0].glob("*.png"))

    deleted = client.delete(f"/api/corpus/{entry_id}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert client.get("/api/corpus/list").json()["n_entries"] == 0


def test_web_api_pick_folder_uses_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pre_peer_checker.web.app import create_app
    from pre_peer_checker.web.folder_picker import PickResult

    monkeypatch.setattr(
        "pre_peer_checker.web.app.pick_folder",
        lambda: PickResult(path="/tmp/demo_case"),
    )
    client = TestClient(create_app())
    res = client.post("/api/pick-folder")
    assert res.status_code == 200
    assert res.json() == {"path": "/tmp/demo_case"}


def test_pick_macos_osascript_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from pre_peer_checker.web import folder_picker as fp

    class FakeProc:
        returncode = 0
        stdout = "/Users/me/MyCase/\n"
        stderr = ""

    monkeypatch.setattr(fp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(fp.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(fp.subprocess, "run", lambda *a, **k: FakeProc())
    result = fp.pick_folder()
    assert result.path == "/Users/me/MyCase"
    assert result.cancelled is False


def test_pick_macos_osascript_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    from pre_peer_checker.web import folder_picker as fp

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "execution error: User canceled. (-128)"

    monkeypatch.setattr(fp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(fp.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(fp.subprocess, "run", lambda *a, **k: FakeProc())
    result = fp.pick_folder()
    assert result.cancelled is True
    assert result.path is None
