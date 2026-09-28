"""PDF から画像・テキスト（パネルラベル候補）を抽出."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class PdfPageExtract:
    page_index: int
    text: str
    image_count: int
    image_xrefs: list[int] = field(default_factory=list)


@dataclass
class PdfExtract:
    path: Path
    pages: list[PdfPageExtract]
    full_text: str


def extract_pdf(path: Path | str, *, extract_images: bool = True) -> PdfExtract:
    path = Path(path)
    doc = fitz.open(path)
    pages: list[PdfPageExtract] = []
    texts: list[str] = []
    try:
        for i, page in enumerate(doc):
            text = page.get_text("text")
            texts.append(text)
            xrefs: list[int] = []
            if extract_images:
                for img in page.get_images(full=True):
                    xrefs.append(int(img[0]))
            pages.append(
                PdfPageExtract(
                    page_index=i,
                    text=text,
                    image_count=len(xrefs),
                    image_xrefs=xrefs,
                )
            )
    finally:
        doc.close()
    return PdfExtract(path=path, pages=pages, full_text="\n".join(texts))


def export_pdf_images(path: Path | str, out_dir: Path | str) -> list[Path]:
    """埋め込み画像をファイルに書き出し、パス一覧を返す."""
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(path)
    written: list[Path] = []
    try:
        seen: set[int] = set()
        for page_i, page in enumerate(doc):
            for img in page.get_images(full=True):
                xref = int(img[0])
                if xref in seen:
                    continue
                seen.add(xref)
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha > 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                dest = out_dir / f"{path.stem}_p{page_i}_x{xref}.png"
                pix.save(dest)
                written.append(dest)
    finally:
        doc.close()
    return written
