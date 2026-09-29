"""Case root layout (manuscript/ + data/) and WebUI API."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pre_peer_checker.web.case_layout import validate_case_root


@pytest.fixture(autouse=True)
def _isolated_runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runs = tmp_path / "runs"
    monkeypatch.setattr("pre_peer_checker.web.app.RUNS_DIR", runs, raising=False)
    return runs


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


def test_web_api_validate_and_run(tmp_path: Path, _isolated_runs_dir: Path) -> None:
    pytest.importorskip("fastapi")
    import json
    import subprocess

    from fastapi.testclient import TestClient

    from pre_peer_checker.web.app import create_app

    root = _make_demo_case(tmp_path)

    client = TestClient(create_app())
    bad = client.post("/api/validate-root", json={"root": str(tmp_path / "missing")})
    assert bad.status_code == 200
    assert bad.json()["ok"] is False

    ok = client.post("/api/validate-root", json={"root": str(root)})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["root"] == str(root.resolve())

    run = client.post("/api/run", json={"root": str(root), "run_title": "Demo paper"})
    assert run.status_code == 200
    payload = run.json()
    assert payload["ok"] is True
    assert payload["n_warnings"] >= 1

    run_dir = Path(payload["run_dir"])
    assert run_dir.parent == _isolated_runs_dir.resolve()
    assert run_dir.name.endswith("_Demo paper")
    assert (run_dir / "input" / "manuscript" / "legend_n.json").is_file()
    assert (run_dir / "input" / "data" / "quant_a.csv").is_file()
    assert (run_dir / "condition" / "run_config.json").is_file()
    assert (run_dir / "report" / "report.html").is_file()
    assert (run_dir / "report" / "warnings.json").is_file()
    manifest = json.loads((run_dir / "audit" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"
    assert manifest["hashes"]["input"] == payload["input_hash"]
    check = subprocess.run(
        ["shasum", "-a", "256", "-c", "audit/SHA256SUMS"],
        cwd=run_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr

    report = client.get("/api/report")
    assert report.status_code == 200
    assert "html" in report.headers.get("content-type", "").lower()

    again = client.post("/api/run", json={"root": str(root), "run_title": "Demo paper"}).json()
    assert again["ok"] is True
    assert again["run_dir"] != payload["run_dir"]
    assert again["input_hash"] == payload["input_hash"]
    assert (run_dir / "report" / "report.html").is_file()

    runs = client.get("/api/runs").json()["runs"]
    assert {r["run_name"] for r in runs} == {payload["run_name"], again["run_name"]}
    assert client.get(payload["report_url"]).status_code == 200
    assert client.get("/api/runs/..%2F..%2Fetc/report").status_code == 404


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


def test_web_api_run_start_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("fastapi")
    import threading
    import time

    from fastapi.testclient import TestClient

    from pre_peer_checker.gui.worker import GuiRunConfig, GuiRunResult
    from pre_peer_checker.pipeline.progress import Stage
    from pre_peer_checker.web.app import create_app

    root = _make_demo_case(tmp_path)
    release = threading.Event()

    def fake_job(config: GuiRunConfig) -> GuiRunResult:
        tr = config.progress
        assert tr is not None
        tr.set_stages([Stage("legend_llm", "Figure Legend の読み取り", 10.0)])
        tr.start("legend_llm")
        tr.update(done=1, total=3, detail="Figure 2 を読み取り中（2/3）")
        release.wait(timeout=5)
        tr.finish()
        return GuiRunResult(
            ok=True,
            n_warnings=2,
            report_path=tmp_path / "r.html",
            json_path=None,
            warning_rows=[],
            coverage_lines=["stub"],
        )

    monkeypatch.setattr("pre_peer_checker.web.app.run_verification_job", fake_job)
    monkeypatch.setattr("pre_peer_checker.web.app.load_history", dict)
    client = TestClient(create_app())

    start = client.post("/api/run/start", json={"root": str(root), "legend_llm": True})
    assert start.status_code == 200
    job_id = start.json()["job_id"]

    snap = None
    for _ in range(50):
        st = client.get(f"/api/run/status/{job_id}").json()
        assert st["ok"] is True
        snap = st["progress"]
        if snap.get("sub_done") == 1:
            break
        time.sleep(0.05)
    assert snap is not None
    assert st["done"] is False
    assert snap["stage_label"] == "Figure Legend の読み取り"
    assert snap["detail"] == "Figure 2 を読み取り中（2/3）"
    # 照合フォルダへの入力保存は照合本体の前に走るので、完了済みステージとして先頭に残る
    assert snap["stages"][0]["id"] == "archive"
    assert snap["stages"][0]["status"] == "done"
    assert snap["stages"][0]["seconds"] is not None
    assert client.get("/api/run/current").json()["job_id"] == job_id

    busy = client.post("/api/run/start", json={"root": str(root)})
    assert busy.json()["ok"] is False
    assert busy.json()["job_id"] == job_id

    release.set()
    for _ in range(50):
        st = client.get(f"/api/run/status/{job_id}").json()
        if st["done"]:
            break
        time.sleep(0.05)
    assert st["done"] is True
    assert st["progress"]["fraction"] == 1.0
    assert st["result"]["ok"] is True
    assert st["result"]["n_warnings"] == 2
    assert client.get("/api/run/current").json()["job_id"] is None
    assert client.get("/api/run/status/unknown").json()["ok"] is False


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
