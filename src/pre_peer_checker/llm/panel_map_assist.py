"""Vector-first panel regions with optional VLM assist when geometry is empty."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pre_peer_checker.llm.panel_map import (
    build_panel_map_prompt,
    coerce_panel_map_dict,
    panel_map_to_region_dicts,
    parse_panel_map_response,
)
from pre_peer_checker.llm.vlm_backend import LocalVlmBackend, select_vlm_backend
from pre_peer_checker.parsers.pdf_panel_geometry import extract_panel_regions_from_pdf


def _rasterize_pdf_page(pdf: Path, page_index: int = 0, *, zoom: float = 1.5) -> tuple[Path, float, float]:
    import fitz

    doc = fitz.open(pdf)
    try:
        page = doc[page_index]
        rect = page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        td = Path(tempfile.mkdtemp(prefix="mc_vlm_page_"))
        out = td / f"{pdf.stem}_p{page_index}.png"
        out.write_bytes(pix.tobytes("png"))
        return out, float(rect.width), float(rect.height)
    finally:
        doc.close()


def assist_panel_regions_with_vlm(
    pdf: Path | str,
    *,
    backend: LocalVlmBackend,
    page_index: int = 0,
    figure_hint: str | None = None,
    max_tokens: int = 768,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run VLM on one page raster; return region dicts + meta."""
    pdf = Path(pdf)
    meta: dict[str, Any] = {
        "source": str(pdf),
        "page_index": page_index,
        "backend": backend.info().__dict__,
        "ok": False,
    }
    try:
        image_path, page_w, page_h = _rasterize_pdf_page(pdf, page_index)
        meta["image_path"] = str(image_path)
        prompt = build_panel_map_prompt(figure_hint=figure_hint or pdf.stem, page_index=page_index)
        raw = backend.generate(prompt, image_path, max_tokens=max_tokens)
        meta["raw_preview"] = (raw or "")[:600]
        parsed = parse_panel_map_response(raw or "")
        if not parsed:
            data: dict[str, Any] | None = None
            try:
                obj = json.loads(raw or "")
                if isinstance(obj, dict):
                    data = obj
            except Exception:
                m = re.search(r"\{[\s\S]*\}", raw or "")
                if m:
                    try:
                        obj = json.loads(m.group(0))
                        if isinstance(obj, dict):
                            data = obj
                    except Exception:
                        data = None
            if isinstance(data, dict):
                coerced, _ = coerce_panel_map_dict(data)
                empty = not coerced["panels"]
                meta["note"] = "vlm_empty_panels" if empty else "vlm_coerce_dropped_all"
                meta["parsed_empty"] = empty
            else:
                meta["note"] = "vlm_parse_failed"
            return [], meta
        regions = panel_map_to_region_dicts(
            parsed,
            page_w=page_w,
            page_h=page_h,
            source=str(pdf),
            source_name=pdf.name,
        )
        meta["ok"] = bool(regions)
        meta["n_panels"] = len(regions)
        meta["figure"] = parsed.get("figure")
        return regions, meta
    except Exception as exc:  # noqa: BLE001
        meta["note"] = f"vlm_error:{exc}"
        return [], meta


def extract_panel_regions_vector_then_vlm(
    pdfs: list[Path],
    *,
    vlm_assist: bool = False,
    vlm_prefer: str = "auto",
    vlm_profile: str | None = None,
    vlm_model: str | None = None,
    max_pages_vector: int = 4,
    min_vector_panels: int = 1,
    on_item: Callable[[int, int, str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Primary: vector geometry. If a PDF yields < min panels and vlm_assist, call VLM once.

    on_item(done, total, label) is called before each PDF (label notes VLM use) and at the end.
    """
    regions: list[dict[str, Any]] = []
    status: dict[str, Any] = {
        "vlm_assist_requested": bool(vlm_assist),
        "vlm_used": False,
        "vector_pdfs": [],
        "vlm_attempts": [],
        "backend": None,
    }
    backend: LocalVlmBackend | None = None

    def _ensure_backend() -> LocalVlmBackend | None:
        nonlocal backend
        if not vlm_assist:
            return None
        if backend is not None:
            return backend
        backend = select_vlm_backend(
            vlm_prefer, model_id=vlm_model, profile_id=vlm_profile
        )
        status["backend"] = backend.info().__dict__ if backend else None
        if backend is None:
            status["note"] = "no VLM backend (install .[vlm-mlx] or .[vlm-cuda])"
        return backend

    def _notify(done: int, label: str) -> None:
        if on_item is not None:
            on_item(done, len(pdfs), label)

    for i, pdf in enumerate(pdfs):
        pdf = Path(pdf)
        _notify(i, f"{pdf.name}（ベクター解析）")
        try:
            vec = extract_panel_regions_from_pdf(pdf, max_pages=max_pages_vector)
        except Exception as exc:  # noqa: BLE001
            vec = []
            status["vector_pdfs"].append({"source": str(pdf), "n": 0, "error": str(exc)})
        else:
            for r in vec:
                r.setdefault("geometry_source", "vector")
            status["vector_pdfs"].append({"source": str(pdf), "n": len(vec)})
            regions.extend(vec)

        if not vlm_assist:
            continue
        if len(vec) >= min_vector_panels:
            continue
        # Assist: first page only (keep cost bounded); load VLM lazily
        _notify(i, f"{pdf.name}（VLM パネル地図）")
        be = _ensure_backend()
        if be is None:
            continue
        vregs, vmeta = assist_panel_regions_with_vlm(pdf, backend=be, page_index=0)
        status["vlm_attempts"].append(vmeta)
        if vregs:
            status["vlm_used"] = True
            regions.extend(vregs)

    _notify(len(pdfs), "")
    status["n_regions"] = len(regions)
    status["n_vector"] = sum(1 for r in regions if r.get("geometry_source") == "vector")
    status["n_vlm"] = sum(1 for r in regions if r.get("geometry_source") == "vlm")
    return regions, status
