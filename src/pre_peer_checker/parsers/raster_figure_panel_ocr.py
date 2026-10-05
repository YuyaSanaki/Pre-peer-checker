"""Raster publication figures: Apple Vision on Mac, Florence layout + tiled OCR elsewhere.

Benchmarked on quick profile (4 raster figures, ``scripts/dev_figure_ocr_bench.py``):
Vision 82.1%, Florence 82.1%, both together 87.2% panel-letter recall. Settings that
the benchmark measured — 300 dpi render, 1280 px layout pass, tiled Florence — are the
defaults here; changing them changes accuracy.

Vision alone (no Florence layout) reads the whole figure, overlapping tiles and each
split photo: a single whole-figure pass misses most isolated panel letters (65% vs 88%
on 10 Nature Extended Data figures), while Florence on Mac CPU takes minutes per figure.
"""

from __future__ import annotations

import os
import platform
import re
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pre_peer_checker.parsers.figure_panel_layout import (
    CASELESS_LETTERS,
    crops_from_panel_letters,
    dominant_panel_run,
    fill_label_gaps,
    panel_boxes_from_photos,
    panel_letter_dets,
)

_CASE_AMBIGUOUS = CASELESS_LETTERS | {"l", "i"}
_PRIMED_RE = re.compile(r"^\(?[A-Za-z][\'\"’′″‴`´]+\)?$")

_DPI = int(os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_DPI", "300"))
_MAX_SIDE = int(os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_MAX_SIDE", "1280"))
_FLORENCE_ID = "florence-community/Florence-2-large"

_lock = threading.Lock()
_florence: Any = None
_vlm: Any = None
# Same rendered figure is OCR'd for panel letters and again for scale-bar labels.
_ocr_memo: dict[str, tuple[list[dict], str, list[dict]]] = {}
_OCR_MEMO_MAX = 64


def unload_raster_ocr_models() -> None:
    """Drop Florence weights so later GPU stages (LightGlue / DINOv2) keep VRAM."""
    global _florence, _vlm
    with _lock:
        _florence = None
        _vlm = None
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
    """Crop OCR engines to run: Apple Vision on Mac, Florence elsewhere."""
    raw = (os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_ENGINES") or "").strip().lower()
    default = ["vision"] if sys.platform == "darwin" else ["florence"]
    wanted = [e for e in raw.replace(",", " ").split() if e] or default
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
        # Only reached when Florence is requested explicitly on Mac; MPS is unverified.
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
            if len(tok) == 1 and tok.isalpha() and tok.lower() not in _CASE_AMBIGUOUS:
                if tok.islower():
                    lower += 1
                elif tok.isupper():
                    upper += 1
    return "lower" if lower > upper else "upper"


def _page_figure_image(page):
    """PIL RGB of the page, or of its one image when that bitmap is the whole figure.

    Figures assembled from several bitmaps keep panels (and their letters) outside
    the largest one, so they are read as the whole page.
    """
    import fitz
    from PIL import Image

    def area(r) -> float:
        return (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1])

    infos = page.get_image_info()
    rect = page.rect
    clip = rect
    big = max(infos, key=area) if infos else None
    if big is not None and area(big) >= 0.9 * rect.width * rect.height:
        clip = fitz.Rect(big["bbox"]) & rect
    pix = page.get_pixmap(dpi=_DPI, clip=clip, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img, (clip.x0, clip.y0, clip.x1, clip.y1)


def _token_dets(dets: list[dict]) -> list[dict]:
    """Split multi-word detections ("a GFP") so each token gets its share of the box."""
    out: list[dict] = []
    for d in dets:
        text = str(d.get("text", ""))
        toks = text.split()
        if len(toks) <= 1:
            out.append(d)
            continue
        x0, y0, x1, y1 = d["box"]
        per = (x1 - x0) / max(len(text), 1)
        pos = 0
        for i, tok in enumerate(toks):
            start = text.index(tok, pos)
            pos = start + len(tok)
            box = [x0 + start * per, y0, x0 + pos * per, y1]
            out.append({"text": tok, "box": box, "lead": i == 0})
    return out


def _label_boxes(dets: list[dict], case: str, letters: set[str]) -> dict[str, list[float]]:
    """Where each panel letter sits; a letter leading its line beats one inside a title."""
    boxes = dict(panel_letter_dets(dets, case=case))
    boxes.update(panel_letter_dets([d for d in dets if d.get("lead", True)], case=case))
    return {ch: b for ch, b in boxes.items() if ch in letters}


def _ink_boxes(full, max_side: int = 800, level: int = 235) -> list[list[float]]:
    """Bounding boxes of connected non-white ink (text lines, plots, diagrams) in pixels."""
    import numpy as np
    from scipy import ndimage

    scale = min(1.0, max_side / max(full.width, full.height, 1))
    small = full.convert("L")
    if scale < 1.0:
        small = small.resize((max(int(full.width * scale), 1), max(int(full.height * scale), 1)))
    mask = ndimage.binary_dilation(np.asarray(small) < level, iterations=2)
    labelled, _n = ndimage.label(mask)
    boxes = []
    for sl in ndimage.find_objects(labelled):
        if sl is None:
            continue
        ys, xs = sl
        if xs.stop - xs.start < 4 and ys.stop - ys.start < 4:
            continue
        boxes.append([xs.start / scale, ys.start / scale, xs.stop / scale, ys.stop / scale])
    return boxes


def _photo_boxes(full, pad: int = 10) -> list[list[float]]:
    """Whitespace-split photo panels in image pixels (white pad so edge photos split).

    Abutting micrographs (channels, time points) form one long strip; it is kept and
    later divided at the letters sitting on it.
    """
    from PIL import ImageOps

    from pre_peer_checker.imaging.panel_split import split_panels

    padded = ImageOps.expand(full, border=pad, fill="white")
    boxes = []
    for b in split_panels(padded, max_aspect=8.0):
        x0, y0 = max(b.left - pad, 0), max(b.top - pad, 0)
        x1, y1 = min(b.right - pad, full.width), min(b.bottom - pad, full.height)
        if x1 > x0 and y1 > y0:
            boxes.append([float(x0), float(y0), float(x1), float(y1)])
    return boxes


def _panel_crops(full, engines: list[str]) -> tuple[list[dict], str]:
    """Panel boxes in full-resolution coordinates, plus the panel letter case."""
    if "florence" not in engines:
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
    label_boxes: dict[str, list[float]] = field(default_factory=dict)
    photo_boxes: list[list[float]] = field(default_factory=list)
    ink_boxes: list[list[float]] = field(default_factory=list)
    inferred: set[str] = field(default_factory=set)
    not_labels: set[str] = field(default_factory=set)
    vlm_answers: dict[str, Any] = field(default_factory=dict)
    # every OCR detection ({"text", "box"} in image pixels), e.g. scale-bar labels
    texts: list[dict] = field(default_factory=list)

    @property
    def engine_label(self) -> str:
        return "+".join(self.engines)

    def panel_boxes(self, kept) -> dict[str, list[float]]:
        """Upper-case panel -> box in image pixels for the kept letters.

        Boxes fit the split photos and ink the letter owns; letters without any
        keep the letter-grid crop from the layout pass. Letters OCR missed inside a
        row of photos are filled from the letters around them.
        """
        keep = {str(c).upper() for c in kept} - self.not_labels
        boxes = {
            str(c["panel"]).upper(): list(c["box"])
            for c in self.crops
            if str(c.get("panel") or "").upper() in keep
        }
        labels = {
            k.upper(): b
            for k, b in self.label_boxes.items()
            if k.upper() in keep or k.upper() in self.inferred
        }
        labels, _filled = fill_label_gaps(labels, self.photo_boxes)
        boxes.update(panel_boxes_from_photos(labels, self.photo_boxes, self.ink_boxes))
        return boxes


@dataclass
class PdfPageRasterOcr:
    """Raster OCR of one PDF page; ``clip`` is the page rect the analysed image covers."""

    page_index: int
    analysis: RasterPanelOcrResult
    clip: tuple[float, float, float, float]
    page_size: tuple[float, float]

    def panel_boxes_on_page(self, kept) -> dict[str, list[float]]:
        """Kept panel boxes mapped from image pixels to page points."""
        a = self.analysis
        if not a.width or not a.height:
            return {}
        x0, y0, x1, y1 = self.clip
        sx, sy = (x1 - x0) / a.width, (y1 - y0) / a.height
        return {
            k: [x0 + b[0] * sx, y0 + b[1] * sy, x0 + b[2] * sx, y0 + b[3] * sy]
            for k, b in a.panel_boxes(kept).items()
        }


def load_figure_rgb(path: Path | str):
    """RGB PIL image at native pixels (no 300 dpi upscale). Honours EXIF orientation."""
    from PIL import Image, ImageOps

    path = Path(path)
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im) or im
        return im.convert("RGB")


_VISION_TILE = 768
_VISION_PHOTO_PAD = 60


def _vision_tiles_and_photos(vision: _AppleVisionOcr, full, tmp: Path) -> list[dict]:
    """Vision on overlapping tiles and on each split photo (with margin for its letter).

    Without a Florence layout the whole-figure pass is all Vision gets; small isolated
    letters only surface once the region around them is enlarged.
    """
    regions: list[tuple[int, int, int, int]] = []
    w, h = full.size
    overlap = _VISION_TILE // 4
    step = _VISION_TILE - overlap
    if max(w, h) > _VISION_TILE:
        for y in range(0, max(h - overlap, 1), step):
            for x in range(0, max(w - overlap, 1), step):
                regions.append((x, y, min(x + _VISION_TILE, w), min(y + _VISION_TILE, h)))
    try:
        photos = _photo_boxes(full)
    except Exception:
        photos = []
    pad = _VISION_PHOTO_PAD
    for b in photos:
        regions.append(
            (max(int(b[0]) - pad, 0), max(int(b[1]) - pad, 0),
             min(int(b[2]) + pad, w), min(int(b[3]) + pad, h))
        )
    dets: list[dict] = []
    for i, (x0, y0, x1, y1) in enumerate(regions):
        if x1 - x0 < 16 or y1 - y0 < 16:
            continue
        cp = tmp / f"vision_{i}.png"
        full.crop((x0, y0, x1, y1)).save(cp)
        for d in vision.ocr_path(cp):
            b = d["box"]
            dets.append({**d, "box": [b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0]})
    return dets


def _run_ocr(full, engines: list[str]) -> tuple[list[dict], str, list[dict]]:
    """Layout crops, their case, and every OCR detection in full-image pixels."""
    crops, case = _panel_crops(full, engines)
    vision = _AppleVisionOcr() if "vision" in engines else None
    florence = _get_florence() if "florence" in engines else None
    dets: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="ppc-panel-ocr-") as tmp:
        for ent in crops:
            x0, y0, x1, y1 = (int(v) for v in ent["box"])
            if x1 <= x0 or y1 <= y0:
                continue
            crop = full.crop((x0, y0, x1, y1))
            crop_dets: list[dict] = []
            if vision is not None:
                cp = Path(tmp) / f"{ent['panel']}.png"
                crop.save(cp)
                crop_dets.extend(vision.ocr_path(cp))
            if florence is not None and ent["panel"] != "*":
                crop_dets.extend(florence.ocr_image(crop))
            for d in crop_dets:
                b = d["box"]
                dets.append({**d, "box": [b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0]})
        if vision is not None and florence is None:
            dets.extend(_vision_tiles_and_photos(vision, full, Path(tmp)))
    if florence is not None:
        # Also the whole image in overlapping tiles: each crop starts at its own label,
        # and Florence drops text sitting on the image border.
        dets.extend(florence.ocr_image(full))
    return crops, case, dets


def _cached_ocr(full, engines: list[str]) -> tuple[list[dict], str, list[dict]]:
    """``PRE_PEER_CHECKER_RASTER_OCR_CACHE=<dir>`` (evaluation only) reuses OCR per image,
    so changes after OCR can be compared on identical detections."""
    import hashlib
    import json

    h = hashlib.sha256(full.tobytes())
    h.update(f"{full.size}|{'+'.join(engines)}|{_MAX_SIDE}|vt{_VISION_TILE}".encode())
    digest = h.hexdigest()[:24]
    cache_dir = (os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_CACHE") or "").strip()
    if not cache_dir:
        with _lock:
            hit = _ocr_memo.get(digest)
        if hit is not None:
            return hit
        res = _run_ocr(full, engines)
        with _lock:
            _ocr_memo[digest] = res
            while len(_ocr_memo) > _OCR_MEMO_MAX:
                _ocr_memo.pop(next(iter(_ocr_memo)))
        return res
    path = Path(cache_dir) / f"{digest}.json"
    if path.is_file():
        d = json.loads(path.read_text(encoding="utf-8"))
        return d["crops"], d["case"], d["dets"]
    crops, case, dets = _run_ocr(full, engines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"crops": crops, "case": case, "dets": dets}), encoding="utf-8")
    return crops, case, dets


def raster_panel_analysis_from_rgb(full, *, engines: list[str] | None = None) -> RasterPanelOcrResult:
    """Layout + crop OCR on an already-loaded RGB image."""
    engines = list(engines) if engines is not None else ocr_engine_names()
    result = RasterPanelOcrResult(
        engines=engines, width=int(full.width), height=int(full.height)
    )
    if not engines:
        return result
    crops, case, dets = _cached_ocr(full, engines)
    result.crops = crops
    result.texts = [{"text": d.get("text", ""), "box": d.get("box")} for d in dets]

    if dets:
        case = _infer_panel_case(dets)
    result.letters = {ch for ch, _ in panel_letter_dets(dets, case=case)}
    # c' / c'' are sub-panels of c; only the bare letter marks where panel c starts.
    unprimed = [d for d in _token_dets(dets) if not _PRIMED_RE.match(str(d.get("text", "")))]
    result.label_boxes = _label_boxes(unprimed, case, result.letters)
    try:
        result.photo_boxes = _photo_boxes(full)
        result.ink_boxes = _ink_boxes(full)
    except Exception:
        result.photo_boxes, result.ink_boxes = [], []
    _vlm_check_labels(full, result)
    return result


def _get_vlm() -> Any:
    global _vlm
    with _lock:
        if _vlm is None:
            from pre_peer_checker.llm.vlm_backend import select_vlm_backend

            _vlm = select_vlm_backend(os.environ.get("PRE_PEER_CHECKER_PANEL_VLM_PREFER") or "auto") or False
        return _vlm or None


def _vlm_check_labels(full, result: RasterPanelOcrResult) -> None:
    """Opt-in (PRE_PEER_CHECKER_PANEL_VLM_SELECT=1): VLM reads doubtful label spots."""
    from pre_peer_checker.llm.panel_letter_select import select_labels_with_vlm, vlm_select_enabled

    if not vlm_select_enabled() or not result.label_boxes:
        return
    keep = {c.upper() for c in dominant_panel_run(result.letters)}
    labels = {k.upper(): b for k, b in result.label_boxes.items() if k.upper() in keep}
    labels, _filled = fill_label_gaps(labels, result.photo_boxes)
    try:
        sel = select_labels_with_vlm(full, labels, result.photo_boxes, result.ink_boxes, _get_vlm())
    except Exception:
        return
    if sel is None:
        return
    lower = next(iter(result.label_boxes)).islower()
    result.label_boxes = {
        k: b for k, b in result.label_boxes.items() if k.upper() not in sel.not_labels
    }
    for k in sel.added:
        result.label_boxes[k.lower() if lower else k] = sel.labels[k]
    result.inferred |= sel.added
    result.not_labels |= sel.not_labels
    result.vlm_answers = sel.answers


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


def raster_panel_analysis_from_page(page, page_index: int = 0) -> PdfPageRasterOcr | None:
    """OCR one PDF page (its dominant image, else the whole page)."""
    engines = ocr_engine_names()
    if not engines:
        return None
    img, clip = _page_figure_image(page)
    return PdfPageRasterOcr(
        page_index=page_index,
        analysis=raster_panel_analysis_from_rgb(img, engines=engines),
        clip=clip,
        page_size=(float(page.rect.width), float(page.rect.height)),
    )


def raster_panel_letters_from_page(page) -> tuple[set[str], list[str]]:
    """OCR one page. Returns (raw single letters, engines used) — unfiltered."""
    res = raster_panel_analysis_from_page(page)
    if res is None:
        return set(), []
    return res.analysis.letters, res.analysis.engines


def raster_panel_analyses_from_pdf(
    path: Path | str, *, max_pages: int = 2
) -> list[PdfPageRasterOcr]:
    """Per-page raster OCR of the first pages of a figure PDF."""
    import fitz

    try:
        doc = fitz.open(Path(path))
    except Exception:
        return []
    out: list[PdfPageRasterOcr] = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            res = raster_panel_analysis_from_page(page, page_index=i)
            if res is not None:
                out.append(res)
    finally:
        doc.close()
    return out


def raster_panel_letters_from_pdf(
    path: Path | str, *, max_pages: int = 2
) -> tuple[set[str], str]:
    """Raw panel letters across the first pages. Returns (letters, engine label)."""
    pages = raster_panel_analyses_from_pdf(path, max_pages=max_pages)
    letters: set[str] = set()
    engines: list[str] = []
    for p in pages:
        letters |= p.analysis.letters
        engines = p.analysis.engines or engines
    return letters, "+".join(engines)
