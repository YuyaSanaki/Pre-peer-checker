"""出版 Figure PDF からパネル文字 (A,B,C,…) を収集し LLM ヒントにする。"""

from __future__ import annotations

import re
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


def panel_labels_from_pdf(path: Path | str, *, max_pages: int = 4) -> list[str]:
    """Return sorted unique uppercase panel letters found as text on the PDF."""
    import fitz

    path = Path(path)
    labels: set[str] = set()
    try:
        doc = fitz.open(path)
    except Exception:
        return []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            for letter in _panel_labels(page):
                if letter.isalpha() and letter.isupper():
                    labels.add(letter)
    finally:
        doc.close()
    return sorted(labels)


def collect_panel_labels_by_figure(
    pdf_paths: list[Path | str],
) -> dict[str, list[str]]:
    """Map figure number key ('1','2','S1') -> panel letters on matching PDFs."""
    by_fig: dict[str, set[str]] = {}
    for raw in pdf_paths:
        path = Path(raw)
        name_l = path.name.lower()
        if not any(k in name_l for k in ("fig", "figure", "supp")):
            continue
        if "editorial" in name_l or name_l.startswith("rplot"):
            continue
        fnum = _figure_num_from_pdf_name(path)
        labels = panel_labels_from_pdf(path)
        if not labels:
            continue
        # If name doesn't pin a figure, still expose under '*'
        key = fnum or "*"
        by_fig.setdefault(key, set()).update(labels)
    return {k: sorted(v) for k, v in by_fig.items()}


def attach_panel_labels_to_chunks(
    chunks: list,
    labels_by_figure: dict[str, list[str]],
) -> None:
    """Mutate FigureChunk.panel_labels_from_figure in place."""
    wildcard = labels_by_figure.get("*") or []
    for ch in chunks:
        labels = list(labels_by_figure.get(ch.figure_num) or [])
        if not labels and wildcard:
            labels = list(wildcard)
        ch.panel_labels_from_figure = labels
