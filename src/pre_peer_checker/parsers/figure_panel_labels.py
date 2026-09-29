"""出版 Figure PDF からパネル文字 (A,B,C,…) を収集し LLM ヒントにする。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.parsers.pdf_panel_plots import _panel_labels


def _figure_num_from_pdf_name(path: Path) -> str | None:
    """Infer figure key from PDF filename ('1', 'S1', ...).

    Supplementary forms (FigS1, FigSupp1) must be matched *before* main-figure
    patterns: ``figs?(\\d+)`` would treat ``figs1.pdf`` (from FigS1) as main ``1``.
    """
    name = path.name.lower().replace(" ", "")
    # Supplementary first: FigSupp1 / FigSup1 / FigureSupp2
    m = re.search(r"fig(?:ure)?supp?(\d+)", name)
    if m:
        return f"S{m.group(1)}"
    # FigS1.pdf → figs1 (literal S after fig, not optional s of figs?)
    m = re.search(r"figs(\d+)", name)
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
    pdf: str = ""


def panel_labels_from_pdf_detailed(
    path: Path | str,
    *,
    max_pages: int = 4,
    raster_fallback: bool | None = None,
) -> tuple[list[str], PanelLabelProvenance]:
    """Vector PyMuPDF labels; if empty, Florence layout + Vision/Florence crop OCR."""
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

    if vector:
        prov.labels = sorted(vector)
        prov.source = "vector"
        return prov.labels, prov

    use_raster = raster_fallback
    if use_raster is None:
        from pre_peer_checker.parsers.raster_figure_panel_ocr import raster_panel_ocr_enabled

        use_raster = raster_panel_ocr_enabled()
    if not use_raster:
        return [], prov

    from pre_peer_checker.parsers.raster_figure_panel_ocr import raster_panel_labels_from_pdf

    raster, engine = raster_panel_labels_from_pdf(path, max_pages=min(max_pages, 2))
    if not raster:
        return [], prov

    prov.labels = list(raster)
    prov.source = "raster_ocr"
    prov.needs_review = True
    prov.ocr_engine = engine
    return prov.labels, prov


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
    """Like collect_panel_labels_by_figure with per-figure provenance for artifacts."""
    by_fig: dict[str, set[str]] = {}
    meta: dict[str, PanelLabelProvenance] = {}
    for raw in pdf_paths:
        path = Path(raw)
        name_l = path.name.lower()
        if not any(k in name_l for k in ("fig", "figure", "supp")):
            continue
        if "editorial" in name_l or name_l.startswith("rplot"):
            continue
        fnum = _figure_num_from_pdf_name(path)
        labels, prov = panel_labels_from_pdf_detailed(
            path, raster_fallback=raster_fallback
        )
        if not labels:
            continue
        key = fnum or "*"
        by_fig.setdefault(key, set()).update(labels)
        prev = meta.get(key)
        if prev is None:
            meta[key] = prov
        elif prev.source != prov.source:
            merged = PanelLabelProvenance(
                labels=sorted(by_fig[key]),
                source="mixed",
                needs_review=prev.needs_review or prov.needs_review,
                ocr_engine=prov.ocr_engine or prev.ocr_engine,
                pdf=prov.pdf or prev.pdf,
            )
            meta[key] = merged
        else:
            prev.labels = sorted(by_fig[key])
            if prov.needs_review:
                prev.needs_review = True
    out = {k: sorted(v) for k, v in by_fig.items()}
    for k, prov in meta.items():
        prov.labels = out.get(k, prov.labels)
    return out, meta


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
