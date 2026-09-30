"""出版 Figure PDF を HTML レポート用プレビュー（画像＋パネル領域）に変換。"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from pre_peer_checker.parsers.figure_chunks import normalize_figure_id
from pre_peer_checker.parsers.pdf_panel_geometry import (
    regions_from_labels,
    regions_from_page_elements,
)
from pre_peer_checker.parsers.pdf_panel_plots import _figure_label, _panel_labels

_FIG_HEAD_RE = re.compile(
    r"^(Figure|Fig\.?|Supplementary\s+Figure)\s*(S?\d+)",
    re.IGNORECASE,
)


def figure_id_from_label(label: str | None) -> str | None:
    if not label:
        return None
    m = _FIG_HEAD_RE.match(label.strip())
    if m:
        return normalize_figure_id(m.group(1), m.group(2))
    return None


def _figure_id_from_page_text(page) -> str | None:
    fid = figure_id_from_label(_figure_label(page))
    if fid:
        return fid
    # Fallback: scan first text blocks
    text = page.get_text("text") or ""
    for line in text.splitlines()[:12]:
        m = _FIG_HEAD_RE.match(line.strip())
        if m:
            return normalize_figure_id(m.group(1), m.group(2))
    return None


def _infer_supp_figure_id(pdf_path: Path, page_index: int) -> str | None:
    name = pdf_path.name.lower().replace(" ", "")
    if "supp" not in name and "sfig" not in name:
        return None
    # FigSupp.pdf page i → Figure S{i+1} (common multi-page supp layout)
    return normalize_figure_id("Supplementary Figure", f"S{page_index + 1}")


def panel_boxes_percent(
    labels: dict[str, tuple[float, float, float]],
    page_w: float,
    page_h: float,
    *,
    pad: float = 6.0,
) -> dict[str, dict[str, float]]:
    """Axis-aligned panel regions from label positions (label ≈ top-left of panel)."""
    regions = regions_from_labels(labels, page_w, page_h, pad=pad)
    return {r.panel: r.as_percent(page_w, page_h) for r in regions}


def _page_to_jpeg_data_uri(
    page,
    *,
    max_width: int = 960,
    quality: int = 70,
) -> str:
    import fitz

    zoom = max_width / float(page.rect.width)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    try:
        raw = pix.tobytes("jpeg", jpg_quality=quality)
    except TypeError:
        raw = pix.tobytes("jpeg")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _rgb_to_jpeg_data_uri(img, *, max_width: int = 960, quality: int = 70) -> str:
    from io import BytesIO

    from PIL import Image

    rgb = img.convert("RGB")
    w, h = rgb.size
    if w > max_width > 0:
        nh = max(1, int(h * max_width / w))
        rgb = rgb.resize((max_width, nh), Image.Resampling.LANCZOS)
    buf = BytesIO()
    rgb.save(buf, format="JPEG", quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _figure_id_from_filename(path: Path) -> str | None:
    from pre_peer_checker.parsers.figure_panel_labels import _figure_num_from_pdf_name

    fnum = _figure_num_from_pdf_name(path)
    if not fnum:
        return None
    if fnum.startswith("ED"):
        return normalize_figure_id("Extended Data Figure", fnum[2:])
    if fnum.startswith("S"):
        return normalize_figure_id("Supplementary Figure", fnum)
    return normalize_figure_id("Figure", fnum)


def _regions_for_path(path: Path, regions: list[dict]) -> list[dict]:
    want = {str(path), path.name}
    try:
        want.add(str(path.resolve()))
    except OSError:
        pass
    out: list[dict] = []
    for r in regions:
        src = str(r.get("source") or "")
        if src in want or Path(src).name == path.name:
            out.append(r)
    return out


def build_raster_figure_preview(
    path: Path,
    *,
    regions: list[dict] | None = None,
    max_width: int = 960,
    quality: int = 70,
) -> dict | None:
    """JPEG/PNG/TIFF figure → HTML preview using filename + letter-anchor boxes."""
    from pre_peer_checker.parsers.raster_figure_panel_ocr import load_figure_rgb

    figure_id = _figure_id_from_filename(path)
    if not figure_id:
        return None
    try:
        img = load_figure_rgb(path)
        uri = _rgb_to_jpeg_data_uri(img, max_width=max_width, quality=quality)
    except Exception:
        return None
    matched = _regions_for_path(path, regions or [])
    panels = []
    for r in matched:
        pct = r.get("pct") or {}
        if not pct:
            continue
        panels.append({"panel": r.get("panel"), **pct})
    panels.sort(key=lambda p: str(p.get("panel") or ""))
    return {
        "figure_id": figure_id,
        "source": str(path),
        "source_name": path.name,
        "page": 0,
        "image_data_uri": uri,
        "panels": panels,
        "n_panels": len(panels),
    }


def build_figure_preview_for_page(
    page,
    *,
    path: Path,
    page_index: int,
    max_width: int = 960,
    quality: int = 70,
    regions: list[dict] | None = None,
    single_page: bool = False,
) -> dict | None:
    """One page → {figure_id, image_data_uri, panels, source, page}."""
    figure_id = _figure_id_from_page_text(page) or _infer_supp_figure_id(path, page_index)
    if not figure_id and single_page:
        figure_id = _figure_id_from_filename(path)
    if not figure_id:
        return None
    labels = _panel_labels(page)
    rect = page.rect
    page_w, page_h = float(rect.width), float(rect.height)
    panels = [
        {"panel": r.panel, **r.as_percent(page_w, page_h)}
        for r in regions_from_page_elements(page, labels, page_index=page_index)
    ]
    if not panels:
        for r in _regions_for_path(path, regions or []):
            pct = r.get("pct") or {}
            if pct and int(r.get("page_index") or 0) == page_index:
                panels.append({"panel": r.get("panel"), **pct})
        panels.sort(key=lambda p: str(p.get("panel") or ""))
    try:
        uri = _page_to_jpeg_data_uri(page, max_width=max_width, quality=quality)
    except Exception:
        return None
    return {
        "figure_id": figure_id,
        "source": str(path),
        "source_name": path.name,
        "page": page_index,
        "image_data_uri": uri,
        "panels": panels,
        "n_panels": len(panels),
    }


def build_figure_previews(
    pdf_paths: list[Path | str],
    *,
    max_pages_per_pdf: int = 12,
    max_figures: int = 16,
    max_width: int = 960,
    quality: int = 70,
    regions: list[dict] | None = None,
) -> list[dict]:
    """Render publication figures (PDF or JPEG/PNG) into HTML-embeddable previews.

    Returns a list (stable order) and prefers the first preview per figure_id.
    """
    import fitz

    from pre_peer_checker.parsers.figure_panel_labels import is_raster_figure_path

    region_list = [r for r in (regions or []) if isinstance(r, dict)]
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for raw in pdf_paths:
        path = Path(raw)
        if not path.is_file():
            continue
        if is_raster_figure_path(path):
            prev = build_raster_figure_preview(
                path, regions=region_list, max_width=max_width, quality=quality
            )
            if not prev:
                continue
            fid = prev["figure_id"]
            if fid in by_id:
                continue
            if len(by_id) >= max_figures:
                break
            by_id[fid] = prev
            order.append(fid)
            continue
        try:
            doc = fitz.open(path)
        except Exception:
            continue
        try:
            for i, page in enumerate(doc):
                if i >= max_pages_per_pdf:
                    break
                prev = build_figure_preview_for_page(
                    page,
                    path=path,
                    page_index=i,
                    max_width=max_width,
                    quality=quality,
                    regions=region_list,
                    single_page=doc.page_count == 1,
                )
                if not prev:
                    continue
                fid = prev["figure_id"]
                if fid in by_id:
                    continue
                if len(by_id) >= max_figures:
                    break
                by_id[fid] = prev
                order.append(fid)
            if len(by_id) >= max_figures:
                break
        finally:
            doc.close()
        if len(by_id) >= max_figures:
            break
    return [by_id[k] for k in order]


def figure_previews_by_id(previews: list[dict]) -> dict[str, dict]:
    return {
        str(p.get("figure_id")): p
        for p in previews
        if isinstance(p, dict) and p.get("figure_id")
    }
