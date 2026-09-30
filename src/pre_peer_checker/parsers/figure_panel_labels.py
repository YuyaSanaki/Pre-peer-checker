"""出版 Figure（PDF / JPEG / PNG）からパネル文字 (A,B,C,…) を収集し LLM ヒントにする。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.parsers.figure_panel_layout import (
    dominant_panel_run,
    region_dicts_from_crops,
)
from pre_peer_checker.parsers.pdf_panel_plots import _panel_labels

_RASTER_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}


def looks_like_figure_filename(path: Path | str) -> bool:
    """True for Fig1 / FigureS2 / Supp names; skips Rplot and editorial PDFs."""
    name = Path(path).name.lower()
    if name.startswith("rplot") or "editorial" in name:
        return False
    return any(k in name for k in ("fig", "figure", "supp"))


def is_publication_figure_raster(path: Path | str) -> bool:
    """Standalone JPEG/PNG/TIFF named like a publication figure."""
    p = Path(path)
    return p.suffix.lower() in _RASTER_FIGURE_SUFFIXES and looks_like_figure_filename(p)


def is_raster_figure_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _RASTER_FIGURE_SUFFIXES


def _panel_run(letters) -> set[str]:
    """``dominant_panel_run`` unless PRE_PEER_CHECKER_PANEL_RUN_FILTER=0 (ablation bench only)."""
    if (os.environ.get("PRE_PEER_CHECKER_PANEL_RUN_FILTER") or "").strip() == "0":
        return {c for c in letters if c.isascii() and c.isalpha() and len(c) == 1}
    return dominant_panel_run(letters)


def _figure_num_from_pdf_name(path: Path) -> str | None:
    """Infer figure key from PDF filename ('1', 'S1', ...).

    Supplementary forms (FigS1, FigSupp1) must be matched *before* main-figure
    patterns: ``figs?(\\d+)`` would treat ``figs1.pdf`` (from FigS1) as main ``1``.
    """
    name = path.name.lower().replace(" ", "")
    m = re.search(r"extended[_-]?data[_-]?fig(?:ure)?[_-]?(\d+)", name)
    if m:
        return f"ED{m.group(1)}"
    # Supplementary first: FigSupp1 / FigSup1 / FigureSupp2
    m = re.search(r"fig(?:ure)?supp?(\d+)", name)
    if m:
        return f"S{m.group(1)}"
    # FigS1.pdf → figs1 (literal S after fig, not optional s of figs?)
    m = re.search(r"figs(\d+)", name)
    if m:
        return f"S{m.group(1)}"
    # FigureS1.png → figures1 (JPEG/PNG export of a supplementary figure)
    m = re.search(r"figures(\d+)", name)
    if m:
        return f"S{m.group(1)}"
    # Main: Fig1 / Figure2 / Fig2_rev
    m = re.search(r"fig(?:ure)?(\d+)", name)
    if m:
        return m.group(1)
    return None


def _vector_panel_labels(page) -> list[str]:
    labels: set[str] = set()
    for letter in _panel_labels(page):
        if letter.isalpha() and letter.isupper():
            labels.add(letter)
    return sorted(labels)


def panel_labels_from_pdf(
    path: Path | str,
    *,
    max_pages: int = 4,
    raster_fallback: bool | None = None,
) -> list[str]:
    """Return sorted unique panel letters (vector text first, optional raster OCR)."""
    labels, _meta = panel_labels_from_pdf_detailed(
        path, max_pages=max_pages, raster_fallback=raster_fallback
    )
    return labels


@dataclass
class PanelLabelProvenance:
    labels: list[str] = field(default_factory=list)
    source: str = "none"  # vector | raster_ocr | mixed
    needs_review: bool = False
    ocr_engine: str = ""
    pdf: str = ""  # source path (PDF or raster figure)
    dropped: list[str] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    width: int = 0
    height: int = 0


def _merge_provenance(
    prev: PanelLabelProvenance, prov: PanelLabelProvenance, labels: list[str]
) -> PanelLabelProvenance:
    return PanelLabelProvenance(
        labels=labels,
        source=prev.source if prev.source == prov.source else "mixed",
        needs_review=prev.needs_review or prov.needs_review,
        ocr_engine=prov.ocr_engine or prev.ocr_engine,
        pdf=prev.pdf or prov.pdf,
        dropped=sorted(set(prev.dropped) | set(prov.dropped)),
        regions=list(prev.regions) + list(prov.regions),
        width=prev.width or prov.width,
        height=prev.height or prov.height,
    )


def _regions_for_kept_letters(
    path: Path, analysis, kept: set[str]
) -> list[dict]:
    keep = {c.upper() for c in kept}
    crops = [
        c
        for c in (analysis.crops or [])
        if str(c.get("panel") or "").upper() in keep
    ]
    return region_dicts_from_crops(
        path, crops, float(analysis.width), float(analysis.height)
    )


def _has_internal_gap(labels: set[str]) -> bool:
    """True when the vector letters skip a letter inside their own span.

    A skipped letter usually means that panel's label is part of the artwork
    bitmap rather than PDF text, so OCR can still recover it.
    """
    ascii_letters = sorted(c for c in labels if c.isascii() and c.isalpha())
    if len(ascii_letters) < 2:
        return False
    span = ord(ascii_letters[-1]) - ord(ascii_letters[0]) + 1
    return len(ascii_letters) < span


def panel_labels_from_pdf_detailed(
    path: Path | str,
    *,
    max_pages: int = 4,
    raster_fallback: bool | None = None,
) -> tuple[list[str], PanelLabelProvenance]:
    """Vector PyMuPDF labels; OCR the artwork when they are absent or incomplete."""
    import fitz

    path = Path(path)
    prov = PanelLabelProvenance(pdf=str(path))
    vector: set[str] = set()
    try:
        doc = fitz.open(path)
    except Exception:
        return [], prov
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            vector.update(_vector_panel_labels(page))
    finally:
        doc.close()

    use_raster = raster_fallback
    if use_raster is None:
        from pre_peer_checker.parsers.raster_figure_panel_ocr import raster_panel_ocr_enabled

        use_raster = raster_panel_ocr_enabled()
    if vector and not _has_internal_gap(vector):
        use_raster = False

    if not use_raster:
        prov.labels = sorted(vector)
        prov.source = "vector" if vector else "none"
        return prov.labels, prov

    from pre_peer_checker.parsers.raster_figure_panel_ocr import raster_panel_letters_from_pdf

    raw, engine = raster_panel_letters_from_pdf(path, max_pages=min(max_pages, 2))
    ocr = _panel_run(raw)
    prov.dropped = sorted(raw - ocr)
    if not ocr:
        prov.labels = sorted(vector)
        prov.source = "vector" if vector else "none"
        return prov.labels, prov

    prov.labels = sorted(vector | ocr)
    prov.source = "mixed" if vector else "raster_ocr"
    prov.needs_review = True
    prov.ocr_engine = engine
    return prov.labels, prov


def panel_labels_from_raster_image_detailed(
    path: Path | str,
    *,
    raster_fallback: bool | None = None,
) -> tuple[list[str], PanelLabelProvenance]:
    """OCR a standalone JPEG/PNG/TIFF figure at native resolution (no vector layer)."""
    path = Path(path)
    prov = PanelLabelProvenance(pdf=str(path))
    use_raster = raster_fallback
    if use_raster is None:
        from pre_peer_checker.parsers.raster_figure_panel_ocr import raster_panel_ocr_enabled

        use_raster = raster_panel_ocr_enabled()
    if not use_raster:
        return [], prov

    from pre_peer_checker.parsers.raster_figure_panel_ocr import (
        raster_panel_analysis_from_image,
    )

    analysis = raster_panel_analysis_from_image(path)
    prov.width = analysis.width
    prov.height = analysis.height
    ocr = _panel_run(analysis.letters)
    prov.dropped = sorted(analysis.letters - ocr)
    if not ocr:
        return [], prov
    prov.labels = sorted(ocr)
    prov.source = "raster_ocr"
    prov.needs_review = True
    prov.ocr_engine = analysis.engine_label
    prov.regions = _regions_for_kept_letters(path, analysis, ocr)
    return prov.labels, prov


def panel_labels_from_figure_detailed(
    path: Path | str,
    *,
    max_pages: int = 4,
    raster_fallback: bool | None = None,
) -> tuple[list[str], PanelLabelProvenance]:
    """Dispatch: native raster OCR for JPEG/PNG/TIFF, vector-then-OCR for PDF."""
    path = Path(path)
    if is_raster_figure_path(path):
        return panel_labels_from_raster_image_detailed(
            path, raster_fallback=raster_fallback
        )
    return panel_labels_from_pdf_detailed(
        path, max_pages=max_pages, raster_fallback=raster_fallback
    )


def collect_panel_labels_by_figure(
    pdf_paths: list[Path | str],
    *,
    raster_fallback: bool | None = None,
) -> dict[str, list[str]]:
    """Map figure number key ('1','2','S1') -> panel letters on matching PDFs."""
    by_fig, _meta = collect_panel_labels_by_figure_detailed(
        pdf_paths, raster_fallback=raster_fallback
    )
    return by_fig


def collect_panel_labels_by_figure_detailed(
    pdf_paths: list[Path | str],
    *,
    raster_fallback: bool | None = None,
) -> tuple[dict[str, list[str]], dict[str, PanelLabelProvenance]]:
    """Like collect_panel_labels_by_figure with per-figure provenance for artifacts.

    ``pdf_paths`` may also contain JPEG/PNG/TIFF figures named like Fig1.png.
    """
    by_fig: dict[str, set[str]] = {}
    meta: dict[str, PanelLabelProvenance] = {}
    for raw in pdf_paths:
        path = Path(raw)
        if not looks_like_figure_filename(path):
            continue
        fnum = _figure_num_from_pdf_name(path)
        labels, prov = panel_labels_from_figure_detailed(
            path, raster_fallback=raster_fallback
        )
        if not labels:
            continue
        key = fnum or "*"
        by_fig.setdefault(key, set()).update(labels)
        prev = meta.get(key)
        if prev is None:
            meta[key] = prov
        else:
            meta[key] = _merge_provenance(prev, prov, sorted(by_fig[key]))
    out = {k: sorted(v) for k, v in by_fig.items()}
    for k, prov in meta.items():
        prov.labels = out.get(k, prov.labels)
    return out, meta


def raster_regions_from_meta(meta_by_figure: dict[str, PanelLabelProvenance]) -> list[dict]:
    """Flatten letter-anchor regions stored on raster-figure provenance."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for prov in meta_by_figure.values():
        for r in prov.regions:
            key = (
                str(r.get("source") or ""),
                str(r.get("panel") or ""),
                int(r.get("page_index") or 0),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


def attach_panel_labels_to_chunks(
    chunks: list,
    labels_by_figure: dict[str, list[str]],
    *,
    meta_by_figure: dict[str, PanelLabelProvenance] | None = None,
) -> None:
    """Mutate FigureChunk.panel_labels_from_figure in place."""
    wildcard = labels_by_figure.get("*") or []
    wild_review = bool(
        meta_by_figure and meta_by_figure.get("*") and meta_by_figure["*"].needs_review
    )
    for ch in chunks:
        labels = list(labels_by_figure.get(ch.figure_num) or [])
        review = False
        if not labels and wildcard:
            labels = list(wildcard)
            review = wild_review
        elif meta_by_figure:
            m = meta_by_figure.get(ch.figure_num)
            review = bool(m and m.needs_review)
        ch.panel_labels_from_figure = labels
        ch.panel_labels_needs_review = review
