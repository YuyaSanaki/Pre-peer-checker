"""Past-paper PDF ingest → local H3 corpus library."""

from __future__ import annotations

from pathlib import Path

import pytest


def _make_pdf_with_embedded_png(path: Path, *, color: tuple[int, int, int] = (40, 120, 200)) -> Path:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    # Create a small PNG and embed it
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 120), 1)
    pix.set_rect(pix.irect, (*color, 255))
    img_bytes = pix.tobytes("png")
    page.insert_image(fitz.Rect(40, 40, 280, 280), stream=img_bytes)
    page.insert_text((40, 320), "Past paper figure")
    doc.save(path)
    doc.close()
    return path


def _make_text_only_pdf(path: Path) -> Path:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((72, 72), "Vector-ish page with no embedded images.")
    doc.save(path)
    doc.close()
    return path


def test_ingest_embedded_figures(tmp_path: Path) -> None:
    pytest.importorskip("fitz")
    from pre_peer_checker.imaging.past_paper_ingest import (
        delete_entry,
        ingest_past_paper_pdf,
        list_entries,
        resolve_corpus_roots,
    )

    lib = tmp_path / "past_papers"
    pdf = _make_pdf_with_embedded_png(tmp_path / "prior.pdf")
    result = ingest_past_paper_pdf(pdf, library_root=lib)
    assert result["ok"] is True
    assert result["n_figures"] >= 1
    assert result["entry"]["extract_method"] == "embedded"
    eid = result["entry"]["id"]
    fig_dir = Path(result["figures_dir"])
    assert fig_dir.is_dir()
    assert any(fig_dir.glob("*.png"))

    entries = list_entries(lib)
    assert len(entries) == 1
    assert entries[0]["id"] == eid

    roots = resolve_corpus_roots([eid], library_root=lib)
    assert roots == [fig_dir.resolve()]

    deleted = delete_entry(eid, library_root=lib)
    assert deleted["ok"] is True
    assert list_entries(lib) == []
    assert not fig_dir.exists()


def test_ingest_page_raster_fallback(tmp_path: Path) -> None:
    pytest.importorskip("fitz")
    from pre_peer_checker.imaging.past_paper_ingest import ingest_past_paper_pdf

    lib = tmp_path / "past_papers"
    pdf = _make_text_only_pdf(tmp_path / "text_only.pdf")
    result = ingest_past_paper_pdf(pdf, library_root=lib)
    assert result["ok"] is True
    assert result["n_figures"] >= 1
    assert result["entry"]["extract_method"] == "page_raster"


def test_ingest_bytes_rejects_non_pdf(tmp_path: Path) -> None:
    from pre_peer_checker.imaging.past_paper_ingest import ingest_past_paper_bytes

    out = ingest_past_paper_bytes("note.txt", b"hello", library_root=tmp_path / "lib")
    assert out["ok"] is False
    assert "PDF" in (out.get("error") or "")
