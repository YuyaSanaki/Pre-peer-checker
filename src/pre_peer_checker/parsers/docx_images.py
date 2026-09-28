"""Word (.docx) 埋め込み画像の書き出し（Supplemental 図などを H3 照合に回す）."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from PIL import Image

_RASTER_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def _raster_ok(data: bytes, min_side: int) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as im:
            return min(im.size) >= min_side
    except Exception:  # noqa: BLE001
        return False


def export_docx_images(
    docx_path: Path | str,
    out_dir: Path | str,
    *,
    min_side: int = 120,
    max_images: int = 60,
    seen_hashes: set[str] | None = None,
) -> list[Path]:
    """Write raster images under ``word/media/`` to ``out_dir``.

    ``seen_hashes`` is shared across calls so the same figure embedded in several
    manuscript versions is exported once. Vector media (EMF/WMF/SVG) is skipped.
    """
    docx_path = Path(docx_path)
    out_dir = Path(out_dir)
    seen = seen_hashes if seen_hashes is not None else set()
    written: list[Path] = []
    try:
        zf = zipfile.ZipFile(docx_path)
    except (OSError, zipfile.BadZipFile):
        return written
    with zf:
        for name in sorted(zf.namelist()):
            if not name.startswith("word/media/"):
                continue
            ext = Path(name).suffix.lower()
            if ext not in _RASTER_EXTS:
                continue
            try:
                data = zf.read(name)
            except (OSError, zipfile.BadZipFile, KeyError):
                continue
            digest = hashlib.sha1(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            if not _raster_ok(data, min_side):
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / f"{docx_path.stem[:60]}_{Path(name).stem}{ext}"
            dest.write_bytes(data)
            written.append(dest)
            if len(written) >= max_images:
                break
    return written
