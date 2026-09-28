"""Input collection filters (Office temps / invalid docx)."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from pre_peer_checker.io_bundle import (
    FileKind,
    collect_inputs,
    is_office_temp_name,
    is_valid_docx_package,
)
from pre_peer_checker.pipeline.orchestrator import _select_docx_for_legend


def test_skips_office_lock_docx(tmp_path: Path) -> None:
    good = tmp_path / "paper.final.docx"
    with ZipFile(good, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    lock = tmp_path / "~$paper.final.docx"
    lock.write_bytes(b"not a zip")
    bundle = collect_inputs([tmp_path])
    names = [p.name for p in bundle.get(FileKind.DOCX)]
    assert names == ["paper.final.docx"]


def test_skips_invalid_docx_stub(tmp_path: Path) -> None:
    stub = tmp_path / "broken.docx"
    stub.write_text("not a package", encoding="utf-8")
    assert is_valid_docx_package(stub) is False
    bundle = collect_inputs([tmp_path])
    assert bundle.get(FileKind.DOCX) == []


def test_office_temp_names() -> None:
    assert is_office_temp_name("~$uthor et al.docx")
    assert is_office_temp_name(".hidden")
    assert not is_office_temp_name(".Rhistory")
    assert not is_office_temp_name("Author et al.text.final.docx")


def test_select_docx_prefers_text_final_over_copy() -> None:
    paths = [
        Path("Author et al.Supplimental Info2ndver4Final copy.docx"),
        Path("Author et al.text.final.docx"),
        Path("Author et al-Response.ver.13.docx"),
    ]
    chosen = _select_docx_for_legend(paths)
    assert paths[1] in chosen
    assert paths[2] not in chosen
    assert paths[0] not in chosen  # " copy" demoted below text.final


def test_collect_inputs_expands_zip_under_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    payload = data / "inner.csv"
    payload.write_text("a,b\n1,2\n", encoding="utf-8")
    zpath = data / "tables.zip"
    with ZipFile(zpath, "w") as zf:
        zf.write(payload, arcname="tables/inner.csv")
    payload.unlink()

    manuscript = tmp_path / "manuscript"
    manuscript.mkdir()
    (manuscript / "note.txt").write_text("x", encoding="utf-8")

    bundle = collect_inputs([manuscript, data])
    try:
        csvs = [p.name for p in bundle.get(FileKind.CSV)]
        assert "inner.csv" in csvs
        assert any("tables.zip" in s for s in bundle.extracted_from)
    finally:
        bundle.cleanup_extracts()


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    from pre_peer_checker.io_bundle import safe_extract_zip

    zpath = tmp_path / "evil.zip"
    with ZipFile(zpath, "w") as zf:
        zf.writestr("../outside.csv", "x,y\n")
        zf.writestr("safe.csv", "a,b\n")
    dest = tmp_path / "out"
    files = safe_extract_zip(zpath, dest)
    names = {p.name for p in files}
    assert "safe.csv" in names
    assert "outside.csv" not in names
    assert not (tmp_path / "outside.csv").exists()


def test_docx_not_treated_as_expandable_zip(tmp_path: Path) -> None:
    docx = tmp_path / "paper.docx"
    with ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    bundle = collect_inputs([tmp_path])
    try:
        assert bundle.extracted_from == []
        assert [p.name for p in bundle.get(FileKind.DOCX)] == ["paper.docx"]
    finally:
        bundle.cleanup_extracts()
