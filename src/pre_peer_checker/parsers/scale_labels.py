"""Scale-bar length labels drawn inside figures (``50 µm`` next to the bar).

Vector text from figure PDFs first; raster figures fall back to the panel OCR
detections (``raster_figure_panel_ocr``).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

_MICRO = "\u00b5\u03bc"  # micro sign / Greek mu
_UNIT_TO_UM = {"nm": 0.001, "um": 1.0, "mm": 1000.0}

_LABEL_RE = re.compile(
    rf"^\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>[{_MICRO}u]m|nm|mm)\s*$"
)
# OCR reads µ as u/p and glues labels to nearby words ("Merge 50 µm").
_OCR_LABEL_RE = re.compile(
    rf"(?:^|\s)(?P<num>\d+(?:[.,]\d+)?)\s?(?P<unit>[{_MICRO}up]m|nm|mm)\s*$"
)
_OCR_MAX_CHARS = 20
_LENGTH_RE = re.compile(
    rf"(?<![\w.])(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[{_MICRO}u]m|nm|mm)(?![\w/²³])"
)


@dataclass(frozen=True)
class FigureScaleLabel:
    value_um: float
    raw: str
    unit: str  # um | nm | mm as printed
    source: str  # figure file
    page_index: int
    origin: str  # vector | raster_ocr
    bbox: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = list(self.bbox) if self.bbox else None
        return d


def _unit_key(unit: str) -> str:
    u = unit.lower()
    if u[0] in _MICRO or u in {"um", "pm"}:
        return "um"
    return u


def _to_um(num: str, unit: str) -> tuple[float, str] | None:
    try:
        value = float(num.replace(",", "."))
    except ValueError:
        return None
    if value <= 0:
        return None
    key = _unit_key(unit)
    return value * _UNIT_TO_UM[key], key


def parse_scale_label(text: str, *, ocr: bool = False) -> tuple[float, str] | None:
    """``(value in µm, unit)`` when ``text`` is a bare scale-bar label, else None."""
    s = (text or "").strip()
    m = _LABEL_RE.match(s)
    if m is None and ocr and len(s) <= _OCR_MAX_CHARS:
        m = _OCR_LABEL_RE.search(s)
    if m is None:
        return None
    return _to_um(m.group("num"), m.group("unit"))


def lengths_in_text(text: str) -> list[tuple[float, str, str]]:
    """Every ``<number> µm|nm|mm`` in running text as ``(value in µm, unit, raw)``."""
    out: list[tuple[float, str, str]] = []
    for m in _LENGTH_RE.finditer(text or ""):
        conv = _to_um(m.group("num"), m.group("unit"))
        if conv:
            out.append((conv[0], conv[1], m.group(0)))
    return out


def _line_text(spans: list[dict]) -> str:
    parts: list[str] = []
    for s in spans:
        t = str(s.get("text") or "")
        # Symbol-font µ is stored as a plain "m" glyph code.
        if t.strip() == "m" and "symbol" in str(s.get("font") or "").lower():
            t = t.replace("m", "\u00b5")
        parts.append(t)
    return "".join(parts)


def vector_scale_labels_from_pdf(
    path: Path | str, *, max_pages: int = 4
) -> list[FigureScaleLabel]:
    """Scale-bar labels stored as PDF text in a figure PDF."""
    import fitz

    path = Path(path)
    out: list[FigureScaleLabel] = []
    try:
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001
        return out
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    text = _line_text(line.get("spans") or [])
                    conv = parse_scale_label(text)
                    if conv is None:
                        continue
                    out.append(
                        FigureScaleLabel(
                            value_um=conv[0],
                            raw=text.strip(),
                            unit=conv[1],
                            source=str(path),
                            page_index=i,
                            origin="vector",
                            bbox=tuple(float(v) for v in line.get("bbox") or ()) or None,
                        )
                    )
    finally:
        doc.close()
    return out


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def scale_labels_from_ocr_texts(
    texts: list[dict], *, source: str, page_index: int = 0
) -> list[FigureScaleLabel]:
    """Scale-bar labels among raster OCR detections (``{"text", "box"}``)."""
    out: list[FigureScaleLabel] = []
    for d in texts:
        text = str(d.get("text") or "")
        conv = parse_scale_label(text, ocr=True)
        if conv is None:
            continue
        box = d.get("box")
        # Crop and tile passes read the same label more than once.
        if box and any(
            lb.bbox and lb.value_um == conv[0] and _overlaps(lb.bbox, box) for lb in out
        ):
            continue
        out.append(
            FigureScaleLabel(
                value_um=conv[0],
                raw=text.strip(),
                unit=conv[1],
                source=source,
                page_index=page_index,
                origin="raster_ocr",
                bbox=tuple(float(v) for v in box) if box else None,
            )
        )
    return out


def raster_scale_labels(path: Path | str, *, max_pages: int = 2) -> list[FigureScaleLabel]:
    """OCR scale-bar labels from a raster figure or the artwork of a figure PDF."""
    from pre_peer_checker.parsers.figure_panel_labels import is_raster_figure_path
    from pre_peer_checker.parsers.raster_figure_panel_ocr import (
        raster_panel_analyses_from_pdf,
        raster_panel_analysis_from_image,
    )

    path = Path(path)
    if is_raster_figure_path(path):
        analysis = raster_panel_analysis_from_image(path)
        return scale_labels_from_ocr_texts(analysis.texts, source=str(path))
    out: list[FigureScaleLabel] = []
    for page in raster_panel_analyses_from_pdf(path, max_pages=max_pages):
        out.extend(
            scale_labels_from_ocr_texts(
                page.analysis.texts, source=str(path), page_index=page.page_index
            )
        )
    return out
