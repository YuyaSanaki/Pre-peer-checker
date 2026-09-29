"""Raster publication figures: Florence panel layout + Vision (Mac) / Florence crop OCR.

Benchmarked on quick profile (4 raster figures, ``scripts/dev_figure_ocr_bench.py``):
Vision 82.1%, Florence 82.1%, both together 87.2% panel-letter recall. Settings that
the benchmark measured — 300 dpi render, 1280 px layout pass, tiled Florence — are the
defaults here; changing them changes accuracy.
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pre_peer_checker.parsers.figure_panel_layout import (
    crops_from_panel_letters,
    panel_letter_dets,
)

_DPI = int(os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_DPI", "300"))
_MAX_SIDE = int(os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_MAX_SIDE", "1280"))
_FLORENCE_ID = "florence-community/Florence-2-large"

_lock = threading.Lock()
_florence: Any = None


def unload_raster_ocr_models() -> None:
    """Drop Florence weights so later GPU stages (LightGlue / DINOv2) keep VRAM."""
    global _florence
    with _lock:
        _florence = None
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def raster_panel_ocr_enabled() -> bool:
    mode = (os.environ.get("PRE_PEER_CHECKER_RASTER_PANEL_OCR") or "auto").strip().lower()
    if mode in {"0", "false", "no", "off"}:
        return False
    if mode in {"1", "true", "yes", "on"}:
        return True
    return bool(ocr_engine_names())


def apple_vision_available() -> bool:
    """True when pyobjc Vision can be imported (macOS, ``.[vision-mac]`` extra)."""
    if sys.platform != "darwin":
        return False
    try:
        import Vision  # noqa: F401
        from Foundation import NSURL  # noqa: F401

        return True
    except Exception:
        return False


def florence_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401

        return True
    except Exception:
        return False


def ocr_engine_names() -> list[str]:
    """Crop OCR engines to run, best-recall first. Both are used when available."""
    raw = (os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_ENGINES") or "").strip().lower()
    wanted = [e for e in raw.replace(",", " ").split() if e] or ["vision", "florence"]
    probes = {"vision": apple_vision_available, "florence": florence_available}
    return [name for name in wanted if name in probes and probes[name]()]


def _prepare_torch_env() -> None:
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
    os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")
    if platform.machine() == "aarch64":
        # Triton JIT needs Python.h, which aarch64 hosts (DGX Spark) often lack.
        os.environ.setdefault("TRITON_INTERPRET", "1")


def _resolve_device() -> str:
    forced = (os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_DEVICE") or "").strip()
    if forced:
        return forced
    if sys.platform == "darwin":
        # Florence on MPS is unverified here; the benchmark ran it on CPU.
        return "cpu"
    from pre_peer_checker.accel import gpu_device

    return gpu_device() or "cpu"


class _FlorenceOcr:
    TILE = 768
    OVERLAP = 128

    def __init__(self) -> None:
        _prepare_torch_env()
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        from pre_peer_checker.accel import GPU_DEVICES, load_pretrained

        self.torch = torch
        self.device = _resolve_device()
        self.dtype = torch.bfloat16 if self.device in GPU_DEVICES else torch.float32
        self.proc = AutoProcessor.from_pretrained(_FLORENCE_ID)
        self.model = load_pretrained(
            Florence2ForConditionalGeneration,
            _FLORENCE_ID,
            device=self.device,
            dtype=self.dtype,
        )
        self.model.eval()

    def _ocr_whole(self, img) -> list[dict]:
        task = "<OCR_WITH_REGION>"
        inputs = self.proc(text=task, images=img, return_tensors="pt")
        inputs = {
            k: (
                v.to(self.device, self.dtype)
                if v.is_floating_point()
                else v.to(self.device)
            )
            if hasattr(v, "to")
            else v
            for k, v in inputs.items()
        }
        with self.torch.inference_mode():
            ids = self.model.generate(**inputs, max_new_tokens=1024, num_beams=3, do_sample=False)
        text = self.proc.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = self.proc.post_process_generation(text, task=task, image_size=img.size)[task]
        out: list[dict] = []
        for quad, label in zip(parsed["quad_boxes"], parsed["labels"], strict=False):
            xs, ys = quad[0::2], quad[1::2]
            t = label.replace("</s>", "").replace("<s>", "").strip()
            if t:
                out.append({"text": t, "box": [min(xs), min(ys), max(xs), max(ys)]})
        return out

    def ocr_image(self, img) -> list[dict]:
        """Tiled OCR — small panel letters are missed in a single whole-image pass."""
        w, h = img.size
        step = self.TILE - self.OVERLAP
        dets: list[dict] = []
        for y in range(0, max(h - self.OVERLAP, 1), step):
            for x in range(0, max(w - self.OVERLAP, 1), step):
                tile = img.crop((x, y, min(x + self.TILE, w), min(y + self.TILE, h)))
                for d in self._ocr_whole(tile):
                    b = d["box"]
                    dets.append(
                        {"text": d["text"], "box": [b[0] + x, b[1] + y, b[2] + x, b[3] + y]}
                    )
        return dets


class _AppleVisionOcr:
    def ocr_path(self, path: Path) -> list[dict]:
        import Vision
        from Foundation import NSURL
        from PIL import Image

        img = Image.open(path).convert("RGB")
        w, h = img.size
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
            NSURL.fileURLWithPath_(str(path)), None
        )
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(False)
        req.setRecognitionLanguages_(["en-US"])
        req.setMinimumTextHeight_(0.0)
        handler.performRequests_error_([req], None)
        out: list[dict] = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)[0]
            bb = obs.boundingBox()
            x0 = bb.origin.x * w
            y1 = (1.0 - bb.origin.y) * h
            out.append(
                {
                    "text": str(cand.string()),
                    "score": float(cand.confidence()),
                    "box": [x0, y1 - bb.size.height * h, x0 + bb.size.width * w, y1],
                }
            )
        return out


def _get_florence() -> Any:
    global _florence
    with _lock:
        if _florence is None:
            _florence = _FlorenceOcr()
        return _florence


def _resize_for_layout(img):
    from PIL import Image

    w, h = img.size
    if max(w, h) <= _MAX_SIDE:
        return img, 1.0, 1.0
    scale = _MAX_SIDE / max(w, h)
    nw, nh = max(int(w * scale), 1), max(int(h * scale), 1)
    return img.resize((nw, nh), Image.Resampling.LANCZOS), w / nw, h / nh


def _infer_panel_case(dets: list[dict]) -> str:
    lower = upper = 0
    for d in dets:
        for tok in str(d.get("text", "")).split():
            if len(tok) == 1 and tok.isalpha():
                if tok.islower():
                    lower += 1
                elif tok.isupper():
                    upper += 1
    return "lower" if lower > upper else "upper"


def _page_figure_image(page):
    """PIL RGB of the dominant embedded image, else the whole page."""
    from PIL import Image

    infos = page.get_image_info()
    rect = page.rect
    area = sum((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) for r in infos)
    if infos and area >= 0.15 * rect.width * rect.height:
        big = max(infos, key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]))
        pix = page.get_pixmap(dpi=_DPI, clip=big["bbox"], alpha=False)
    else:
        pix = page.get_pixmap(dpi=_DPI, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _panel_crops(full, engines: list[str]) -> tuple[list[dict], str]:
    """Panel boxes in full-resolution coordinates, plus the panel letter case."""
    if "florence" not in engines and not florence_available():
        return [{"panel": "*", "box": [0.0, 0.0, float(full.width), float(full.height)]}], "upper"
    layout_img, sx, sy = _resize_for_layout(full)
    dets = _get_florence().ocr_image(layout_img)
    case = _infer_panel_case(dets)
    crops = crops_from_panel_letters(
        layout_img.width, layout_img.height, panel_letter_dets(dets, case=case)
    )
    for ent in crops:
        b = ent["box"]
        ent["box"] = [b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy]
    return crops, case


@dataclass
class RasterPanelOcrResult:
    """Native-resolution panel OCR (JPEG/PNG/TIFF or a rendered PDF page)."""

    letters: set[str] = field(default_factory=set)
    engines: list[str] = field(default_factory=list)
    crops: list[dict] = field(default_factory=list)
    width: int = 0
    height: int = 0

    @property
    def engine_label(self) -> str:
        return "+".join(self.engines)


def load_figure_rgb(path: Path | str):
    """RGB PIL image at native pixels (no 300 dpi upscale). Honours EXIF orientation."""
    from PIL import Image, ImageOps

    path = Path(path)
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im) or im
        return im.convert("RGB")


def raster_panel_analysis_from_rgb(full, *, engines: list[str] | None = None) -> RasterPanelOcrResult:
    """Layout + crop OCR on an already-loaded RGB image."""
    engines = list(engines) if engines is not None else ocr_engine_names()
    result = RasterPanelOcrResult(
        engines=engines, width=int(full.width), height=int(full.height)
    )
    if not engines:
        return result
    crops, case = _panel_crops(full, engines)
    result.crops = crops

    vision = _AppleVisionOcr() if "vision" in engines else None
    florence = _get_florence() if "florence" in engines else None
    dets: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="ppc-panel-ocr-") as tmp:
        for ent in crops:
            x0, y0, x1, y1 = (int(v) for v in ent["box"])
            crop = full.crop((x0, y0, x1, y1))
            if vision is not None:
                cp = Path(tmp) / f"{ent['panel']}.png"
                crop.save(cp)
                dets.extend(vision.ocr_path(cp))
            if florence is not None:
                dets.extend(florence.ocr_image(crop))

    result.letters = {ch for ch, _ in panel_letter_dets(dets, case=case)}
    return result


def raster_panel_analysis_from_image(path: Path | str) -> RasterPanelOcrResult:
    """OCR a standalone JPEG/PNG/TIFF figure at native resolution."""
    try:
        full = load_figure_rgb(path)
    except Exception:
        return RasterPanelOcrResult()
    return raster_panel_analysis_from_rgb(full)


def raster_panel_letters_from_image(path: Path | str) -> tuple[set[str], str]:
    """Raw panel letters from a raster figure. Returns (letters, engine label)."""
    result = raster_panel_analysis_from_image(path)
    return result.letters, result.engine_label


def raster_panel_letters_from_page(page) -> tuple[set[str], list[str]]:
    """OCR one page. Returns (raw single letters, engines used) — unfiltered."""
    engines = ocr_engine_names()
    if not engines:
        return set(), []
    result = raster_panel_analysis_from_rgb(_page_figure_image(page), engines=engines)
    return result.letters, result.engines


def raster_panel_letters_from_pdf(
    path: Path | str, *, max_pages: int = 2
) -> tuple[set[str], str]:
    """Raw panel letters across the first pages. Returns (letters, engine label)."""
    import fitz

    letters: set[str] = set()
    engines: list[str] = []
    try:
        doc = fitz.open(Path(path))
    except Exception:
        return set(), ""
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            page_letters, used = raster_panel_letters_from_page(page)
            letters |= page_letters
            engines = used or engines
    finally:
        doc.close()
    return letters, "+".join(engines)
