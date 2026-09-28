"""入力ファイルの収集と種別判定."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from zipfile import BadZipFile, ZipFile


class FileKind(str, Enum):
    R_SCRIPT = "r_script"
    PYTHON = "python"
    NOTEBOOK = "notebook"
    YAML = "yaml"
    EXCEL = "excel"
    CSV = "csv"
    TSV = "tsv"
    MTX = "mtx"
    JSON = "json"
    TEXT = "text"
    CZI = "czi"
    LIF = "lif"
    DOCX = "docx"
    PDF = "pdf"
    IMAGE = "image"
    PRISM = "prism"
    KALEIDA = "kaleida"
    UNKNOWN = "unknown"


# Longer / compound suffixes first (matched via name.endswith)
_COMPOUND_SUFFIXES: list[tuple[str, FileKind]] = [
    (".tsv.gz", FileKind.TSV),
    (".csv.gz", FileKind.CSV),
    (".mtx.gz", FileKind.MTX),
    (".json.gz", FileKind.JSON),
    (".txt.gz", FileKind.TEXT),
]

_EXT_MAP: dict[str, FileKind] = {
    ".r": FileKind.R_SCRIPT,
    ".py": FileKind.PYTHON,
    ".ipynb": FileKind.NOTEBOOK,
    ".yaml": FileKind.YAML,
    ".yml": FileKind.YAML,
    ".xlsx": FileKind.EXCEL,
    ".xls": FileKind.EXCEL,
    ".csv": FileKind.CSV,
    ".tsv": FileKind.TSV,
    ".mtx": FileKind.MTX,
    ".json": FileKind.JSON,
    ".txt": FileKind.TEXT,
    ".czi": FileKind.CZI,
    ".lif": FileKind.LIF,
    ".docx": FileKind.DOCX,
    ".pdf": FileKind.PDF,
    ".tif": FileKind.IMAGE,
    ".tiff": FileKind.IMAGE,
    ".png": FileKind.IMAGE,
    ".jpg": FileKind.IMAGE,
    ".jpeg": FileKind.IMAGE,
    ".pzfx": FileKind.PRISM,
    ".pzf": FileKind.PRISM,
    ".qpd": FileKind.KALEIDA,
    ".qpc": FileKind.KALEIDA,
    ".qpt": FileKind.KALEIDA,
}

# Archives to expand (.docx/.xlsx are OPC zips — never treat as data archives)
_ARCHIVE_SUFFIXES = {".zip"}


@dataclass
class InputBundle:
    """検証対象ファイル群."""

    root: Path | None = None
    files: dict[FileKind, list[Path]] = field(default_factory=dict)
    extract_dirs: list[Path] = field(default_factory=list)
    extracted_from: list[str] = field(default_factory=list)

    def all_paths(self) -> list[Path]:
        paths: list[Path] = []
        for items in self.files.values():
            paths.extend(items)
        return sorted(paths)

    def get(self, kind: FileKind) -> list[Path]:
        return list(self.files.get(kind, []))

    def cleanup_extracts(self) -> None:
        for d in self.extract_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self.extract_dirs.clear()


# Dotfiles we intentionally collect (R session history is often the only script trace)
_ALLOWED_DOT_NAMES = frozenset({".rhistory"})


def is_office_temp_name(name: str) -> bool:
    """Word/Excel lock files and Finder junk (``~$…``, ``.~lock…``).

    Hidden dotfiles are skipped *except* allowlisted names such as ``.Rhistory``.
    """
    n = name.strip()
    if not n:
        return True
    lower = n.lower()
    if lower in _ALLOWED_DOT_NAMES or lower.endswith(".rhistory"):
        return False
    if n.startswith("."):
        return True
    if n.startswith("~$") or n.startswith(".~"):
        return True
    if n.startswith("~") and n.endswith(".tmp"):
        return True
    if n == "__MACOSX" or n.startswith("__MACOSX"):
        return True
    return False


def is_r_history_name(name: str) -> bool:
    lower = name.lower()
    return lower == ".rhistory" or lower.endswith(".rhistory")


def classify(path: Path) -> FileKind:
    name = path.name.lower()
    for suffix, kind in _COMPOUND_SUFFIXES:
        if name.endswith(suffix):
            return kind
    if is_r_history_name(name):
        return FileKind.R_SCRIPT
    if name.endswith(".rmd"):
        return FileKind.R_SCRIPT
    return _EXT_MAP.get(path.suffix.lower(), FileKind.UNKNOWN)


def is_valid_docx_package(path: Path) -> bool:
    """True if *path* looks like an OPC zip with ``[Content_Types].xml``."""
    if not path.is_file() or path.stat().st_size < 22:
        return False
    try:
        with ZipFile(path) as zf:
            names = set(zf.namelist())
        return "[Content_Types].xml" in names
    except Exception:  # noqa: BLE001
        return False


def is_zip_archive(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _ARCHIVE_SUFFIXES


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Extract *zip_path* into *dest_dir* with Zip-Slip protection. Returns extracted files."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_root = dest_dir.resolve()
    extracted: list[Path] = []
    with ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if not name or name.endswith("/"):
                continue
            # Skip macOS resource forks / hidden junk inside archives
            parts = Path(name).parts
            if any(is_office_temp_name(part) or part == "__MACOSX" for part in parts):
                continue
            target = (dest_root / name).resolve()
            try:
                target.relative_to(dest_root)
            except ValueError:
                continue  # zip slip
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            extracted.append(target)
    return extracted


def collect_inputs(
    paths: list[Path | str],
    *,
    extract_zips: bool = True,
    max_zip_depth: int = 2,
) -> InputBundle:
    """ファイルまたはディレクトリのリストから InputBundle を構築.

    ``manuscript/``・``data/`` 配下の ``.zip`` は一時ディレクトリへ展開し、
    中身を通常ファイルと同様に収集する（``.docx`` 等の Office パッケージは対象外）。
    """
    bundle = InputBundle()
    pending_zips: list[tuple[Path, int]] = []

    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            if bundle.root is None:
                bundle.root = p
            for child in sorted(p.rglob("*")):
                if not child.is_file() or is_office_temp_name(child.name):
                    continue
                if extract_zips and is_zip_archive(child):
                    pending_zips.append((child, 0))
                else:
                    _add(bundle, child)
        elif p.is_file():
            if extract_zips and is_zip_archive(p):
                pending_zips.append((p, 0))
            else:
                _add(bundle, p)

    while pending_zips:
        zip_path, depth = pending_zips.pop(0)
        if depth > max_zip_depth:
            continue
        try:
            td = Path(
                tempfile.mkdtemp(
                    prefix=f"mc-zip-{zip_path.stem[:40]}-",
                )
            )
        except OSError:
            continue
        bundle.extract_dirs.append(td)
        try:
            files = safe_extract_zip(zip_path, td)
        except (BadZipFile, OSError, RuntimeError):
            shutil.rmtree(td, ignore_errors=True)
            if td in bundle.extract_dirs:
                bundle.extract_dirs.remove(td)
            continue
        bundle.extracted_from.append(str(zip_path))
        for child in files:
            if extract_zips and is_zip_archive(child) and depth < max_zip_depth:
                pending_zips.append((child, depth + 1))
            else:
                _add(bundle, child)

    return bundle


def _add(bundle: InputBundle, path: Path) -> None:
    if is_office_temp_name(path.name):
        return
    kind = classify(path)
    if kind is FileKind.UNKNOWN:
        return
    # Skip Word lock leftovers / corrupt stubs that would raise Package not found
    if kind is FileKind.DOCX and not is_valid_docx_package(path):
        return
    bundle.files.setdefault(kind, []).append(path)
