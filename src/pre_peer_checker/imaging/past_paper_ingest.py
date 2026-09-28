"""過去論文 PDF → ローカル H3 コーパスライブラリ.

ポリシー:
- PDF / 抽出図は ``cache/past_papers/`` のみ（git 外・コミュニティ共有なし）
- 照合時は各エントリの ``figures/`` を ``--corpus`` 相当として渡す
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.paths import REPO_ROOT
from pre_peer_checker.parsers.pdf_images import export_embedded_images

DEFAULT_LIBRARY_DIR = REPO_ROOT / "cache" / "past_papers"

_MAX_PDF_BYTES = 80 * 1024 * 1024
_MAX_IMAGES = 200
_MIN_SIDE_EMBED = 80
_MIN_SIDE_PAGE = 200
_PAGE_MAX_SIDE = 1600


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_stem(name: str) -> str:
    stem = Path(name).stem.strip() or "paper"
    stem = re.sub(r"[^\w.\-]+", "_", stem, flags=re.UNICODE)
    return stem[:80] or "paper"


def library_paths(library_root: Path | None = None) -> tuple[Path, Path, Path]:
    """Return (library_dir, index_path, entries_dir)."""
    root = Path(library_root) if library_root is not None else DEFAULT_LIBRARY_DIR
    return root, root / "index.json", root / "entries"


def ensure_library(library_root: Path | None = None) -> Path:
    root, index_path, entries_dir = library_paths(library_root)
    entries_dir.mkdir(parents=True, exist_ok=True)
    if not index_path.is_file():
        index_path.write_text(
            json.dumps({"entries": []}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return root


def load_index(library_root: Path | None = None) -> dict[str, Any]:
    ensure_library(library_root)
    _, index_path, _ = library_paths(library_root)
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"entries": []}
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    return data


def save_index(data: dict[str, Any], library_root: Path | None = None) -> None:
    ensure_library(library_root)
    _, index_path, _ = library_paths(library_root)
    index_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def entry_dir(entry_id: str, library_root: Path | None = None) -> Path:
    _, _, entries_dir = library_paths(library_root)
    return entries_dir / entry_id


def figures_dir(entry_id: str, library_root: Path | None = None) -> Path:
    return entry_dir(entry_id, library_root) / "figures"


def list_entries(library_root: Path | None = None) -> list[dict[str, Any]]:
    """Return index entries (newest first), dropping missing dirs."""
    data = load_index(library_root)
    kept: list[dict[str, Any]] = []
    dirty = False
    for item in data["entries"]:
        if not isinstance(item, dict):
            dirty = True
            continue
        eid = str(item.get("id") or "")
        if not eid or not figures_dir(eid, library_root).is_dir():
            dirty = True
            continue
        kept.append(item)
    if dirty:
        data["entries"] = kept
        save_index(data, library_root)
    return list(reversed(kept))


def resolve_corpus_roots(
    entry_ids: list[str] | None,
    *,
    library_root: Path | None = None,
) -> list[Path]:
    """Map selected entry ids to figures/ directories that exist."""
    if not entry_ids:
        return []
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in entry_ids:
        eid = str(raw).strip()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        fig = figures_dir(eid, library_root)
        if fig.is_dir() and any(fig.iterdir()):
            roots.append(fig.resolve())
    return roots


def _export_page_rasters(
    pdf_path: Path,
    out_dir: Path,
    *,
    min_side: int = _MIN_SIDE_PAGE,
    max_side: int = _PAGE_MAX_SIDE,
    max_pages: int = 40,
) -> list[Path]:
    import fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    doc = fitz.open(pdf_path)
    try:
        for page_i, page in enumerate(doc):
            if page_i >= max_pages:
                break
            rect = page.rect
            if min(rect.width, rect.height) < 1:
                continue
            scale = min(max_side / max(rect.width, rect.height), 2.0)
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            if min(pix.width, pix.height) < min_side:
                continue
            dest = out_dir / f"{pdf_path.stem}_page{page_i:03d}.png"
            pix.save(dest)
            written.append(dest)
    finally:
        doc.close()
    return written


def ingest_past_paper_pdf(
    pdf_path: Path | str,
    *,
    source_name: str | None = None,
    library_root: Path | None = None,
) -> dict[str, Any]:
    """Save PDF into the local library and extract figure PNGs."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        return {"ok": False, "error": f"PDF が見つかりません: {pdf_path}"}
    if pdf_path.suffix.lower() != ".pdf":
        return {"ok": False, "error": "PDF ファイルのみ受け付けます"}

    ensure_library(library_root)
    entry_id = uuid.uuid4().hex[:12]
    dest_root = entry_dir(entry_id, library_root)
    fig_dir = dest_root / "figures"
    dest_root.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    name = source_name or pdf_path.name
    dest_pdf = dest_root / "source.pdf"
    shutil.copy2(pdf_path, dest_pdf)

    embedded = export_embedded_images(
        dest_pdf,
        fig_dir,
        min_side=_MIN_SIDE_EMBED,
        max_images=_MAX_IMAGES,
    )
    method = "embedded"
    figures = list(embedded)
    if not figures:
        figures = _export_page_rasters(dest_pdf, fig_dir)
        method = "page_raster" if figures else "none"

    title = _safe_stem(name)
    created = _utc_now()
    meta = {
        "id": entry_id,
        "title": title,
        "source_name": name,
        "n_figures": len(figures),
        "extract_method": method,
        "n_embedded": len(embedded),
        "created_at": created,
        "figures": [p.name for p in figures],
    }
    (dest_root / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    index = load_index(library_root)
    summary = {
        "id": entry_id,
        "title": title,
        "source_name": name,
        "n_figures": len(figures),
        "extract_method": method,
        "created_at": created,
    }
    index["entries"].append(summary)
    save_index(index, library_root)

    return {
        "ok": True,
        "entry": summary,
        "meta": meta,
        "figures_dir": str(fig_dir.resolve()),
        "n_figures": len(figures),
    }


def ingest_past_paper_bytes(
    filename: str,
    data: bytes,
    *,
    library_root: Path | None = None,
) -> dict[str, Any]:
    """Write upload bytes to a staging PDF then ingest."""
    if not data:
        return {"ok": False, "error": "空のファイルです"}
    if len(data) > _MAX_PDF_BYTES:
        return {"ok": False, "error": "ファイルが大きすぎます（80MB 超）"}
    name = filename or "upload.pdf"
    if not name.lower().endswith(".pdf"):
        return {"ok": False, "error": "PDF ファイルのみ受け付けます"}

    root = ensure_library(library_root)
    staging = root / "_staging"
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"{uuid.uuid4().hex}_{_safe_stem(name)}.pdf"
    try:
        tmp.write_bytes(data)
        return ingest_past_paper_pdf(
            tmp, source_name=name, library_root=library_root
        )
    finally:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)


def delete_entry(
    entry_id: str,
    *,
    library_root: Path | None = None,
) -> dict[str, Any]:
    eid = str(entry_id).strip()
    if not eid:
        return {"ok": False, "error": "entry_id が空です"}

    root = entry_dir(eid, library_root)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)

    index = load_index(library_root)
    before = len(index["entries"])
    index["entries"] = [
        e for e in index["entries"] if isinstance(e, dict) and str(e.get("id")) != eid
    ]
    save_index(index, library_root)
    return {
        "ok": True,
        "deleted": eid,
        "removed_from_index": before - len(index["entries"]),
        "entries": list_entries(library_root),
    }
