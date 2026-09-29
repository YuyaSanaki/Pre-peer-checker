"""Raster publication figures: Florence panel layout + Vision (Mac) / Florence (Linux) crop OCR."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from pre_peer_checker.parsers.figure_panel_layout import (
    crops_from_panel_letters,
    panel_letter_dets,
)

_DPI = int(os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_DPI", "200"))
_MAX_SIDE = int(os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_MAX_SIDE", "1280"))
_FLORENCE_ID = "florence-community/Florence-2-large"

_lock = threading.Lock()
_layout_svc: Any = None
_ocr_florence: Any = None


def raster_panel_ocr_enabled() -> bool:
    mode = (os.environ.get("PRE_PEER_CHECKER_RASTER_PANEL_OCR") or "auto").strip().lower()
    if mode in {"0", "false", "no", "off"}:
        return False
    if mode in {"1", "true", "yes", "on"}:
        return True
    return _backend_name() is not None


def _backend_name() -> str | None:
    if sys.platform == "darwin" and _apple_vision_available():
        return "vision"
    if _florence_available():
        return "florence"
    return None


def apple_vision_available() -> bool:
    """True when pyobjc Vision can be imported (macOS, install.sh vision-mac extra)."""
    return _apple_vision_available()


def _apple_vision_available() -> bool:
    try:
        import Vision  # noqa: F401
        from Foundation import NSURL  # noqa: F401

        return True
    except Exception:
        return False


def _florence_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401

        return True
    except Exception:
        return False


def _torch_device() -> str:
    from pre_peer_checker.accel import torch_device

    return torch_device()


def _prepare_torch_env() -> None:
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
    os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")
    if sys.platform == "darwin" and os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_DEVICE") is None:
        # Mac torch wheels are often CPU/MPS-only; avoid defaulting to cuda.
        os.environ.setdefault("PRE_PEER_CHECKER_RASTER_OCR_DEVICE", "cpu")


def _resolve_device() -> str:
    forced = (os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_DEVICE") or "").strip()
    if forced:
        return forced
    _prepare_torch_env()
    dev = _torch_device()
    if dev == "cuda" or dev == "xpu":
        return dev
    if sys.platform == "darwin":
        try:
            import torch

            if torch.backends.mps.is_available():
                return "mps"
        except Exception:
            pass
    return "cpu"


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

    def _ocr_pil(self, img) -> list[dict]:
        task = "<OCR_WITH_REGION>"
        inputs = self.proc(text=task, images=img, return_tensors="pt")
        inputs = {k: v.to(self.device, self.dtype) if hasattr(v, "to") else v for k, v in inputs.items()}
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
        w, h = img.size
        step = self.TILE - self.OVERLAP
        dets: list[dict] = []
        for y in range(0, max(h - self.OVERLAP, 1), step):
            for x in range(0, max(w - self.OVERLAP, 1), step):
                box = (x, y, min(x + self.TILE, w), min(y + self.TILE, h))
                for d in self._ocr_pil(img.crop(box)):
                    b = d["box"]
                    dets.append(
                        {
                            "text": d["text"],
                            "box": [b[0] + x, b[1] + y, b[2] + x, b[3] + y],
                        }
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


def _get_florence() -> _FlorenceOcr:
    global _layout_svc, _ocr_florence
    with _lock:
        if _ocr_florence is None:
            _ocr_florence = _FlorenceOcr()
            _layout_svc = _ocr_florence
        return _ocr_florence


def _resize_for_layout(img):
    from PIL import Image

    w, h = img.size
    if max(w, h) <= _MAX_SIDE:
        return img, 1.0, 1.0
    scale = _MAX_SIDE / max(w, h)
    nw, nh = int(w * scale), int(h * scale)
    return img.resize((nw, nh), Image.Resampling.LANCZOS), w / nw, h / nh


def _infer_panel_case(dets: list[dict]) -> str:
    lower = upper = 0
    for d in dets:
        for tok in str(d.get("text", "")).split():
            t = tok.strip()
            if len(t) == 1 and t.isalpha():
                if t.islower():
                    lower += 1
                elif t.isupper():
                    upper += 1
    return "lower" if lower > upper else "upper"


def _page_figure_image(page) -> Any:
    """PIL RGB for dominant embedded image, else full page render."""
    from PIL import Image

    infos = page.get_image_info()
    rect = page.rect
    area = sum((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) for r in infos)
    if infos and area >= 0.15 * rect.width * rect.height:
        big = max(infos, key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]))
        clip = big["bbox"]
        pix = page.get_pixmap(dpi=_DPI, clip=clip, alpha=False)
    else:
        pix = page.get_pixmap(dpi=_DPI, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def raster_panel_labels_from_page(page) -> tuple[list[str], str]:
    """OCR panel letters on one PDF page. Returns (sorted labels, engine name)."""
    backend = _backend_name()
    if backend is None:
        return [], ""

    from PIL import Image

    full = _page_figure_image(page)
    layout_img, sx, sy = _resize_for_layout(full)
    florence = _get_florence()
    layout_dets = florence._ocr_pil(layout_img)
    case = _infer_panel_case(layout_dets)
    letters = panel_letter_dets(layout_dets, case=case)
    lw, lh = layout_img.size
    crops = crops_from_panel_letters(lw, lh, letters)
    merged: list[dict] = []
    if backend == "vision":
        ocr = _AppleVisionOcr()
        with tempfile.TemporaryDirectory(prefix="ppc-raster-ocr-") as tmp:
            td = Path(tmp)
            for ent in crops:
                x0, y0, x1, y1 = (int(v) for v in ent["box"])
                x0f, y0f, x1f, y1f = (
                    int(x0 * sx),
                    int(y0 * sy),
                    int(x1 * sx),
                    int(y1 * sy),
                )
                crop = full.crop((x0f, y0f, x1f, y1f))
                cp = td / f"{ent['panel']}.png"
                crop.save(cp)
                for d in ocr.ocr_path(cp):
                    b = d["box"]
                    merged.append(
                        {
                            "text": d["text"],
                            "box": [b[0] + x0f, b[1] + y0f, b[2] + x0f, b[3] + y0f],
                        }
                    )
    else:
        for ent in crops:
            x0, y0, x1, y1 = (int(v) for v in ent["box"])
            x0f, y0f, x1f, y1f = (
                int(x0 * sx),
                int(y0 * sy),
                int(x1 * sx),
                int(y1 * sy),
            )
            crop = full.crop((x0f, y0f, x1f, y1f))
            for d in florence.ocr_image(crop):
                b = d["box"]
                merged.append(
                    {
                        "text": d["text"],
                        "box": [b[0] + x0f, b[1] + y0f, b[2] + x0f, b[3] + y0f],
                    }
                )

    found = panel_letter_dets(merged, case=case)
    labels = sorted({ch.upper() if case == "upper" else ch.lower() for ch, _ in found})
    return labels, backend


def raster_panel_labels_from_pdf(path: Path | str, *, max_pages: int = 2) -> tuple[list[str], str]:
    import fitz

    path = Path(path)
    labels: set[str] = set()
    engine = ""
    try:
        doc = fitz.open(path)
    except Exception:
        return [], ""
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            page_labels, eng = raster_panel_labels_from_page(page)
            if eng and not engine:
                engine = eng
            for lab in page_labels:
                labels.add(lab)
    finally:
        doc.close()
    out = sorted(labels, key=lambda x: (not x.isupper(), x))
    return out, engine
