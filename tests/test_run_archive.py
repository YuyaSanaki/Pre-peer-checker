"""照合ごとの保存フォルダ（run_archive）."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pre_peer_checker.pipeline.run_archive import (
    RunArchive,
    guess_paper_title,
    list_runs,
    resolve_run_dir,
    run_folder_name,
    safe_title,
)


def test_run_folder_name_format() -> None:
    when = datetime(2026, 9, 28, 22, 59, tzinfo=UTC)
    assert run_folder_name("Paper title", when) == "20260928_2259_Paper title"


def test_safe_title_strips_path_separators() -> None:
    assert safe_title("A/B: C?") == "A B C"
    assert safe_title("  ") == "untitled"
    assert len(safe_title("あ" * 200).encode("utf-8")) <= 150


def test_guess_title_from_markdown(tmp_path: Path) -> None:
    ms = tmp_path / "manuscript"
    ms.mkdir()
    (ms / "paper.md").write_text("# Neurons regrow after injury\n\nBody", encoding="utf-8")
    assert guess_paper_title(ms, "case") == ("Neurons regrow after injury", "md:paper.md")
    assert guess_paper_title(tmp_path / "missing", "case") == ("case", "case_root_name")


def test_archive_layout_and_same_minute_runs(tmp_path: Path) -> None:
    case = tmp_path / "case"
    (case / "manuscript").mkdir(parents=True)
    (case / "data").mkdir()
    (case / "manuscript" / "legend.json").write_text("{}", encoding="utf-8")
    (case / "data" / "a.csv").write_text("x\n1\n", encoding="utf-8")
    (case / "data" / ".DS_Store").write_bytes(b"junk")
    runs = tmp_path / "runs"
    when = datetime(2026, 9, 28, 22, 59).astimezone()

    first = RunArchive.create(runs, case_root=case, title="Paper title", when=when)
    first.snapshot_inputs([case / "manuscript", case / "data"])
    first.snapshot_conditions({"k": 1}, patterns_path=None)
    (first.report_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    manifest = first.finalize(ok=True, n_warnings=0)

    assert first.name == "20260928_2259_Paper title"
    assert sorted(p.name for p in first.run_dir.iterdir()) == [
        "audit", "condition", "input", "report"
    ]
    assert not (first.input_dir / "data" / ".DS_Store").exists()
    assert manifest["n_input_files"] == 2
    assert set(manifest["hashes"]) == {"input", "condition", "report"}
    sums = (first.audit_dir / "SHA256SUMS").read_text(encoding="utf-8")
    assert "input/data/a.csv" in sums and "report/report.html" in sums

    second = RunArchive.create(runs, case_root=case, title="Paper title", when=when)
    second.snapshot_inputs([case / "manuscript", case / "data"])
    assert second.name == "20260928_2259_Paper title_2"
    assert second.finalize(ok=True)["hashes"]["input"] == manifest["hashes"]["input"]

    assert [r["run_name"] for r in list_runs(runs)] == [second.name, first.name]
    assert resolve_run_dir(runs, first.name) == first.run_dir.resolve()
    assert resolve_run_dir(runs, "..") is None
    assert resolve_run_dir(runs, "../case") is None


def test_snapshot_inputs_reports_byte_progress(tmp_path: Path) -> None:
    from pre_peer_checker.pipeline.progress import ProgressTracker, Stage

    case = tmp_path / "case"
    (case / "data").mkdir(parents=True)
    (case / "data" / "big.bin").write_bytes(b"x" * 3000)
    (case / "data" / "small.csv").write_bytes(b"y" * 1000)
    tr = ProgressTracker()
    tr.set_stages([Stage("archive", "Archive", 10.0)])
    tr.start("archive")

    archive = RunArchive.create(tmp_path / "runs", case_root=case, title="T")
    archive.snapshot_inputs([case / "data"], progress=tr)

    snap = tr.snapshot()
    assert snap["sub_total"] == 4000
    assert snap["sub_done"] == 4000
    assert "2/2" in snap["detail"]
