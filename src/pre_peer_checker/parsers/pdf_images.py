"""PDF 埋め込み画像の書き出し（Figure 内重複検知用）。"""

from __future__ import annotations

from pathlib import Path

import fitz


def export_embedded_images(
    pdf_path: Path | str,
    out_dir: Path | str,
    *,
    min_side: int = 80,
    max_images: int = 200,
) -> list[Path]:
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    written: list[Path] = []
    seen: set[int] = set()
    try:
        for page_i, page in enumerate(doc):
            for img in page.get_images(full=True):
                xref = int(img[0])
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    if min(pix.width, pix.height) < min_side:
                        continue
                    dest = out_dir / f"{pdf_path.stem}_p{page_i}_x{xref}.png"
                    pix.save(dest)
                    written.append(dest)
                except Exception:
                    continue
                if len(written) >= max_images:
                    return written
    finally:
        doc.close()
    return written
