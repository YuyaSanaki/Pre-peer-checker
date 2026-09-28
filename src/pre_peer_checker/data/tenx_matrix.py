"""Lightweight 10x Genomics matrix folder checks (barcodes / features / mtx)."""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.warnings import WarningItem, WarningTag

_BARCODE_NAMES = ("barcodes.tsv.gz", "barcodes.tsv")
_FEATURE_NAMES = ("features.tsv.gz", "features.tsv", "genes.tsv.gz", "genes.tsv")
_MATRIX_NAMES = ("matrix.mtx.gz", "matrix.mtx")


@dataclass
class TenxMatrixSummary:
    directory: Path
    n_barcodes: int | None = None
    n_features: int | None = None
    mtx_n_features: int | None = None
    mtx_n_cells: int | None = None
    mtx_nnz: int | None = None
    ok: bool = True
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "directory": str(self.directory),
            "n_barcodes": self.n_barcodes,
            "n_features": self.n_features,
            "mtx_n_features": self.mtx_n_features,
            "mtx_n_cells": self.mtx_n_cells,
            "mtx_nnz": self.mtx_nnz,
            "ok": self.ok,
            "issues": list(self.issues),
        }


@dataclass
class TenxScanResult:
    summaries: list[TenxMatrixSummary] = field(default_factory=list)
    warnings: list[WarningItem] = field(default_factory=list)

    @property
    def artifacts(self) -> dict:
        return {
            "n_matrices": len(self.summaries),
            "n_ok": sum(1 for s in self.summaries if s.ok),
            "total_barcodes": sum(s.n_barcodes or 0 for s in self.summaries),
            "matrices": [s.to_dict() for s in self.summaries],
        }


def _open_text(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def count_nonempty_lines(path: Path, *, max_lines: int | None = None) -> int:
    n = 0
    with _open_text(path) as fh:
        for line in fh:
            if line.strip():
                n += 1
                if max_lines is not None and n >= max_lines:
                    break
    return n


def read_mtx_shape(path: Path) -> tuple[int, int, int] | None:
    """Return (n_rows, n_cols, nnz) from Matrix Market header without loading body."""
    with _open_text(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("%"):
                continue
            parts = line.split()
            if len(parts) >= 3:
                try:
                    return int(parts[0]), int(parts[1]), int(parts[2])
                except ValueError:
                    return None
            return None
    return None


def _first_existing(directory: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        p = directory / name
        if p.is_file():
            return p
    return None


def find_tenx_directories(paths: list[Path]) -> list[Path]:
    """Unique parent dirs that look like 10x matrix folders."""
    found: set[Path] = set()
    for p in paths:
        parent = p.parent
        if parent in found:
            continue
        has_bc = _first_existing(parent, _BARCODE_NAMES) is not None
        has_ft = _first_existing(parent, _FEATURE_NAMES) is not None
        has_mx = _first_existing(parent, _MATRIX_NAMES) is not None
        if has_bc and has_ft and has_mx:
            found.add(parent.resolve())
    return sorted(found)


def summarize_tenx_dir(directory: Path) -> TenxMatrixSummary:
    summary = TenxMatrixSummary(directory=directory)
    bc = _first_existing(directory, _BARCODE_NAMES)
    ft = _first_existing(directory, _FEATURE_NAMES)
    mx = _first_existing(directory, _MATRIX_NAMES)
    if bc is None or ft is None or mx is None:
        summary.ok = False
        summary.issues.append("barcodes/features/matrix のいずれかが欠けています")
        return summary
    try:
        summary.n_barcodes = count_nonempty_lines(bc)
        summary.n_features = count_nonempty_lines(ft)
        shape = read_mtx_shape(mx)
        if shape is None:
            summary.ok = False
            summary.issues.append("matrix.mtx ヘッダを読めませんでした")
            return summary
        summary.mtx_n_features, summary.mtx_n_cells, summary.mtx_nnz = shape
        # 10x mtx is typically features × barcodes (genes × cells)
        if summary.n_features != summary.mtx_n_features:
            summary.ok = False
            summary.issues.append(
                f"features 行数 {summary.n_features} ≠ mtx 行 {summary.mtx_n_features}"
            )
        if summary.n_barcodes != summary.mtx_n_cells:
            summary.ok = False
            summary.issues.append(
                f"barcodes 行数 {summary.n_barcodes} ≠ mtx 列 {summary.mtx_n_cells}"
            )
    except OSError as exc:
        summary.ok = False
        summary.issues.append(str(exc))
    return summary


def scan_tenx_matrices(paths: list[Path]) -> TenxScanResult:
    """Scan candidate files for 10x folders; emit warnings on dimension mismatch."""
    result = TenxScanResult()
    for directory in find_tenx_directories(paths):
        summary = summarize_tenx_dir(directory)
        result.summaries.append(summary)
        if not summary.ok:
            result.warnings.append(
                WarningItem(
                    tag=WarningTag.CONFIG_MISMATCH,
                    title=f"10x 行列の次元不一致: {directory.name}",
                    location=str(directory),
                    reason="; ".join(summary.issues) or "不明な不整合",
                    sources=[str(directory)],
                    metadata={
                        "pattern_id": "P-CONFIG-MISMATCH",
                        "n_barcodes": summary.n_barcodes,
                        "n_features": summary.n_features,
                        "mtx_n_cells": summary.mtx_n_cells,
                        "mtx_n_features": summary.mtx_n_features,
                    },
                )
            )
    return result
