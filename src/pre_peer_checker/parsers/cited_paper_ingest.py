"""引用先論文 PDF → ローカル cited_papers ライブラリ.

ポリシー:
- PDF / 抽出テキストは ``cache/cited_papers/`` のみ（git 外・コミュニティ共有なし）
- H3 の ``cache/past_papers/``（画像）とは分離
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.paths import REPO_ROOT

DEFAULT_LIBRARY_DIR = REPO_ROOT / "cache" / "cited_papers"

_MAX_PDF_BYTES = 80 * 1024 * 1024
_CHUNK_CHARS = 900
_CHUNK_OVERLAP = 120

_DOI_RE = re.compile(r"\b(?:doi:\s*)?(10\.\d{4,9}/[^\s\]）,;]+)", re.I)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_stem(name: str) -> str:
    stem = Path(name).stem.strip() or "paper"
    stem = re.sub(r"[^\w.\-]+", "_", stem, flags=re.UNICODE)
    return stem[:80] or "paper"


def library_paths(library_root: Path | None = None) -> tuple[Path, Path, Path]:
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


def list_entries(library_root: Path | None = None) -> list[dict[str, Any]]:
    data = load_index(library_root)
    kept: list[dict[str, Any]] = []
    dirty = False
    for item in data["entries"]:
        if not isinstance(item, dict):
            dirty = True
            continue
        eid = str(item.get("id") or "")
        if not eid or not (entry_dir(eid, library_root) / "text.txt").is_file():
            dirty = True
            continue
        kept.append(item)
    if dirty:
        data["entries"] = kept
        save_index(data, library_root)
    return list(reversed(kept))


def delete_entry(entry_id: str, library_root: Path | None = None) -> dict[str, Any]:
    eid = str(entry_id).strip()
    if not eid:
        return {"ok": False, "error": "empty id"}
    ed = entry_dir(eid, library_root)
    if ed.is_dir():
        shutil.rmtree(ed, ignore_errors=True)
    data = load_index(library_root)
    data["entries"] = [
        e for e in data["entries"] if isinstance(e, dict) and str(e.get("id")) != eid
    ]
    save_index(data, library_root)
    return {"ok": True, "id": eid, "entries": list_entries(library_root)}


def pdftotext_raw(pdf_path: Path) -> str:
    try:
        proc = subprocess.run(
            ["pdftotext", "-raw", str(pdf_path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Fallback: PyMuPDF
    try:
        import fitz

        doc = fitz.open(pdf_path)
        try:
            return "\n".join(page.get_text("text") for page in doc)
        finally:
            doc.close()
    except Exception:
        return ""


def extract_pdf_meta(text: str, *, filename: str = "") -> dict[str, Any]:
    head = (text or "")[:4000]
    doi_m = _DOI_RE.search(head) or _DOI_RE.search(text or "")
    doi = None
    if doi_m:
        doi = doi_m.group(1).strip().rstrip(".,;)")
    years = [int(y) for y in _YEAR_RE.findall(head)]
    year = years[0] if years else None
    # Title heuristic: first non-trivial line
    title = None
    for line in head.splitlines():
        s = line.strip()
        if len(s) < 12 or len(s) > 240:
            continue
        if re.match(r"^(abstract|introduction|references)\b", s, re.I):
            continue
        if _DOI_RE.search(s):
            continue
        title = s
        break
    if not title and filename:
        title = _safe_stem(filename).replace("_", " ")
    return {
        "title": title,
        "year": year,
        "doi": doi,
        "n_chars": len(text or ""),
    }


def chunk_text(text: str, *, size: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> list[dict[str, Any]]:
    blob = re.sub(r"\r\n?", "\n", text or "").strip()
    if not blob:
        return []
    # Prefer paragraph breaks
    paras = [p.strip() for p in re.split(r"\n\s*\n", blob) if p.strip()]
    if not paras:
        paras = [blob]
    chunks: list[dict[str, Any]] = []
    buf = ""
    for para in paras:
        if not buf:
            buf = para
        elif len(buf) + 1 + len(para) <= size:
            buf = f"{buf}\n{para}"
        else:
            chunks.append({"i": len(chunks), "text": buf})
            # overlap tail
            tail = buf[-overlap:] if overlap and len(buf) > overlap else ""
            buf = f"{tail}\n{para}".strip() if tail else para
    if buf:
        chunks.append({"i": len(chunks), "text": buf})
    # Hard-split oversized
    out: list[dict[str, Any]] = []
    for ch in chunks:
        t = ch["text"]
        if len(t) <= size * 2:
            out.append({"i": len(out), "text": t})
            continue
        start = 0
        while start < len(t):
            out.append({"i": len(out), "text": t[start : start + size]})
            start += max(size - overlap, 1)
    return out


def ingest_cited_paper_pdf(
    pdf_path: Path | str,
    *,
    library_root: Path | None = None,
    title_hint: str | None = None,
) -> dict[str, Any]:
    path = Path(pdf_path)
    if not path.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    size = path.stat().st_size
    if size > _MAX_PDF_BYTES:
        return {"ok": False, "error": f"PDF too large ({size} bytes)"}
    if size < 64:
        return {"ok": False, "error": "PDF too small"}

    ensure_library(library_root)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    for item in load_index(library_root)["entries"]:
        if not isinstance(item, dict) or item.get("sha256") != sha:
            continue
        existing = str(item.get("id") or "")
        if existing and (entry_dir(existing, library_root) / "text.txt").is_file():
            meta_path = entry_dir(existing, library_root) / "meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
            return {"ok": True, "id": existing, "meta": meta, "reused": True}

    eid = f"{_safe_stem(path.name)}_{uuid.uuid4().hex[:8]}"
    ed = entry_dir(eid, library_root)
    ed.mkdir(parents=True, exist_ok=True)
    dest = ed / "source.pdf"
    shutil.copy2(path, dest)

    text = pdftotext_raw(dest)
    (ed / "text.txt").write_text(text, encoding="utf-8")
    chunks = chunk_text(text)
    with (ed / "chunks.jsonl").open("w", encoding="utf-8") as fh:
        for ch in chunks:
            fh.write(json.dumps(ch, ensure_ascii=False) + "\n")
    meta = extract_pdf_meta(text, filename=path.name)
    if title_hint:
        meta["title"] = title_hint
    meta.update(
        {
            "id": eid,
            "source_name": path.name,
            "sha256": sha,
            "ingested_at": _utc_now(),
            "n_chunks": len(chunks),
        }
    )
    (ed / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    data = load_index(library_root)
    data["entries"].append(
        {
            "id": eid,
            "source_name": path.name,
            "sha256": sha,
            "title": meta.get("title"),
            "year": meta.get("year"),
            "doi": meta.get("doi"),
            "n_chunks": len(chunks),
            "n_chars": meta.get("n_chars"),
            "ingested_at": meta.get("ingested_at"),
        }
    )
    save_index(data, library_root)
    return {"ok": True, "id": eid, "meta": meta}


def ingest_cited_paper_bytes(
    filename: str,
    data: bytes,
    *,
    library_root: Path | None = None,
) -> dict[str, Any]:
    if len(data) > _MAX_PDF_BYTES:
        return {"ok": False, "error": f"PDF too large ({len(data)} bytes)"}
    ensure_library(library_root)
    tmp_dir = library_paths(library_root)[0] / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{_safe_stem(filename)}_{uuid.uuid4().hex[:6]}.pdf"
    try:
        tmp.write_bytes(data)
        return ingest_cited_paper_pdf(tmp, library_root=library_root)
    finally:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)


def resolve_cited_paper_dirs(
    entry_ids: list[str] | None = None,
    *,
    library_root: Path | None = None,
    extra_roots: list[Path | str] | None = None,
) -> list[Path]:
    """Return entry directories (with text.txt) for selected ids and/or loose roots.

    Loose roots may be a directory of PDFs (ingested in-place as path parents
    are not mutated) or an entry dir / parent containing entry dirs.
    For CLI ``--cited-papers`` pointing at a folder of PDFs, callers should
    ingest first; this resolver accepts already-ingested entry dirs or a
    library root subset.
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        rp = p.resolve()
        key = str(rp)
        if key in seen:
            return
        if (rp / "text.txt").is_file() or (rp / "meta.json").is_file():
            seen.add(key)
            roots.append(rp)

    for raw in entry_ids or []:
        eid = str(raw).strip()
        if eid:
            _add(entry_dir(eid, library_root))

    for raw in extra_roots or []:
        p = Path(raw)
        if not p.exists():
            continue
        if p.is_file() and p.suffix.lower() == ".pdf":
            # Expect sibling ingest; skip bare pdf here
            continue
        if (p / "text.txt").is_file():
            _add(p)
            continue
        # directory of entry dirs
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_dir():
                    _add(child)
    return roots


def load_entry_payload(entry_path: Path) -> dict[str, Any] | None:
    ed = Path(entry_path)
    meta_path = ed / "meta.json"
    text_path = ed / "text.txt"
    if not meta_path.is_file() and not text_path.is_file():
        return None
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    text = ""
    if text_path.is_file():
        text = text_path.read_text(encoding="utf-8", errors="ignore")
    chunks: list[dict[str, Any]] = []
    chunks_path = ed / "chunks.jsonl"
    if chunks_path.is_file():
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                chunks.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    elif text:
        chunks = chunk_text(text)
    return {
        "path": str(ed.resolve()),
        "meta": meta,
        "text": text,
        "chunks": chunks,
    }


def ensure_pdfs_ingested(
    paths: list[Path | str],
    *,
    library_root: Path | None = None,
) -> list[Path]:
    """Ingest PDF files / dirs of PDFs; return entry directories."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file() and p.suffix.lower() == ".pdf":
            res = ingest_cited_paper_pdf(p, library_root=library_root)
            if res.get("ok"):
                out.append(entry_dir(str(res["id"]), library_root))
            continue
        if p.is_dir():
            # Already an entry?
            if (p / "text.txt").is_file():
                out.append(p.resolve())
                continue
            pdfs = sorted(p.glob("*.pdf")) + sorted(p.glob("**/*.pdf"))
            # de-dupe
            seen: set[str] = set()
            for pdf in pdfs:
                key = str(pdf.resolve())
                if key in seen:
                    continue
                seen.add(key)
                res = ingest_cited_paper_pdf(pdf, library_root=library_root)
                if res.get("ok"):
                    out.append(entry_dir(str(res["id"]), library_root))
    unique: list[Path] = []
    seen_dirs: set[str] = set()
    for d in out:
        key = str(Path(d).resolve())
        if key not in seen_dirs:
            seen_dirs.add(key)
            unique.append(d)
    return unique
