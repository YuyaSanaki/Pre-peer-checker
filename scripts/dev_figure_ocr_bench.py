"""図中文字 OCR エンジン比較（開発用・非公開データを使うため gitignore）。

  prepare  ローカルで図を 300 dpi 画像化し正解（gt.json）を作る
  subset   既存 gt.json からプロファイル用 gt_<profile>.json を切り出す
  run      エンジン 1 つで全画像を読む（preds_<engine>.json）
  score    正解と突き合わせて表を出す

  --layout panels  Florence でパネル文字位置 → パネル crop ごとに OCR（大図・VLM 向け）

プロファイル（--profile）:
  quick   4 枚 — ラスタ代表（Cell 大/小 + Nature）。エンジン選定向け（Spark ~30–45 分）
  raster  10 枚 — 出版 Figure のラスタのみ（Rplot 除外）
  full    42 枚 — 従来どおり

正解は 2 種類:
  vector  埋め込み文字のある図（Nature Fig.1/2 と Rplot 類）→ 単語単位・座標付き
  raster  文字が画像に焼き込まれた図 → Legend から得たパネル文字集合（A..最大文字）

エンジン: tesseract / paddle_server / paddle_mobile / florence2 / qwen25vl / qwen3vl / vision(macOS)
"""

from __future__ import annotations

import argparse
import json
import os

# Spark(aarch64): Triton JIT に Python.h が要る。解釈モードでコンパイルを避ける。
if os.uname().machine == "aarch64":
    os.environ.setdefault("TRITON_INTERPRET", "1")
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICE = os.environ.get("BENCH_DEVICE", "cuda")
DPI = 300
SCALE = DPI / 72.0
PAPER_DIRS = {
    "p01": ROOT / "input/panel_extract/paper_01",
    "p02": ROOT / "input/panel_extract/paper_02",
}
_CAP_RE = re.compile(r"^(?:Fig\.?|Figure)\s*(\d+)\s*[|.:]", re.M)
_TRIM = "()[]{},.;:'\"“”‘’"

# 図中 OCR の本命は raster。quick は最難関+代表で早く決める。
PROFILE_QUICK = ("p02_fig3", "p02_fig1", "p02_fig4", "p01_fig3")


def _gt_path(out: Path, profile: str) -> Path:
    return out / "gt.json" if profile == "full" else out / f"gt_{profile}.json"


def _pred_tag(profile: str, layout: str) -> str:
    tag = "" if profile == "full" else f"_{profile}"
    if layout == "panels":
        tag += "_panels"
    return tag


def _pred_path(out: Path, engine: str, profile: str, layout: str) -> Path:
    return out / f"preds_{engine}{_pred_tag(profile, layout)}.json"


def _profile_ids(profile: str) -> set[str] | None:
    if profile == "full":
        return None
    if profile == "quick":
        return set(PROFILE_QUICK)
    if profile == "raster":
        return None  # subset コマンドで set=="raster" を使う
    raise ValueError(profile)


# --------------------------------------------------------------------------- prepare
def _paper_pdf(paper: str) -> Path:
    """Local PDF under input/panel_extract/. Do not hardcode the filename."""
    directory = PAPER_DIRS[paper]
    pdfs = sorted(p for p in directory.glob("*.pdf") if p.is_file())
    if len(pdfs) != 1:
        rel = directory.relative_to(ROOT)
        raise SystemExit(f"{paper}: expected one PDF under {rel}/, found {len(pdfs)}")
    return pdfs[0]


def _legend_panels(paper: str, fig: str) -> list[str]:
    sys.path.insert(0, str(ROOT / "src"))
    from pre_peer_checker.parsers.manuscript_text import pdf_text

    paras = pdf_text(_paper_pdf(paper)).paragraphs
    head = re.compile(rf"^(?:Fig\.?|Figure)\s*{fig}\s*[|.:]")
    other = re.compile(r"^(?:Fig\.?|Figure)\s*\d+\s*[|.:]|^References\b")
    legend: list[str] = []
    for p in paras:
        if legend and other.match(p):
            break
        if legend or head.match(p):
            legend.append(p)
    text = " ".join(legend)
    if paper == "p02":
        hits = re.findall(r"\(([A-Z])((?:\s*(?:[-–,]|and)\s*[A-Z])*)\)", text)
        letters = [a for a, _ in hits] + [c for _, rest in hits for c in re.findall(r"[A-Z]", rest)]
    else:
        hits = re.findall(r"(?:\|[^.]*?\.|\.)\s+([a-z])((?:\s*[,–-]\s*[a-z])*)\s+(?=[A-Z0-9])", text)
        letters = [a for a, _ in hits] + [c for _, rest in hits for c in re.findall(r"[a-z]", rest)]
    if not letters:
        return []
    base = "A" if paper == "p02" else "a"
    top = max(letters)
    return [chr(c) for c in range(ord(base), ord(top) + 1)]


def _words_in(page, clip) -> list[dict]:
    out = []
    for x0, y0, x1, y1, w, *_ in page.get_text("words", clip=clip):
        t = w.strip(_TRIM)
        if not t or any(ord(ch) < 32 for ch in t):
            continue
        out.append({
            "text": t,
            "box": [(x0 - clip.x0) * SCALE, (y0 - clip.y0) * SCALE, (x1 - clip.x0) * SCALE, (y1 - clip.y0) * SCALE],
        })
    return out


def _vector_panel_letters(page, clip) -> list[dict]:
    out = []
    for b in page.get_text("dict", clip=clip)["blocks"]:
        for ln in b.get("lines") or []:
            for s in ln["spans"]:
                t = s["text"].strip()
                if len(t) == 1 and t.isalpha() and s["size"] >= 10 and "Bold" in s["font"]:
                    x0, y0, x1, y1 = s["bbox"]
                    out.append({"text": t, "box": [(x0 - clip.x0) * SCALE, (y0 - clip.y0) * SCALE,
                                                   (x1 - clip.x0) * SCALE, (y1 - clip.y0) * SCALE]})
    return out


def cmd_subset(out: Path, profile: str) -> None:
    full = json.loads((out / "gt.json").read_text())
    if profile == "quick":
        items = [it for it in full if it["id"] in PROFILE_QUICK]
    elif profile == "raster":
        items = [it for it in full if it["set"] == "raster"]
    else:
        items = list(full)
    dest = _gt_path(out, profile)
    dest.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    print(f"wrote {dest} ({len(items)} images)")


def cmd_prepare(out: Path, n_rplots: int, *, profile: str) -> None:
    import pymupdf

    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []

    for paper in PAPER_DIRS:
        doc = pymupdf.open(_paper_pdf(paper))
        for page in doc:
            infos = page.get_image_info()
            area = sum((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) for r in infos)
            if area < 0.2 * page.rect.width * page.rect.height:
                continue
            big = max(infos, key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]))
            clip = pymupdf.Rect(big["bbox"])
            m = _CAP_RE.search(page.get_text())
            fig = m.group(1) if m else None
            if fig is None:
                # Nature Fig.1/2 pages: caption split across spans; fall back to page order
                fig = {3: "1", 5: "2"}.get(page.number + 1) if paper == "p01" else None
            if fig is None:
                continue
            iid = f"{paper}_fig{fig}"
            allow = _profile_ids(profile)
            if allow is not None and iid not in allow:
                continue
            pix = page.get_pixmap(dpi=DPI, clip=clip, alpha=False)
            img = img_dir / f"{iid}.png"
            pix.save(img)
            words = _words_in(page, clip)
            item = {"id": iid, "image": f"images/{img.name}", "W": pix.width, "H": pix.height,
                    "case": "upper" if paper == "p02" else "lower", "panels": _legend_panels(paper, fig)}
            if words:
                item["set"] = "vector"
                item["words"] = words
                item["panel_boxes"] = _vector_panel_letters(page, clip)
            else:
                item["set"] = "raster"
            items.append(item)

    if profile != "full":
        dest = _gt_path(out, profile)
        dest.write_text(json.dumps(items, ensure_ascii=False, indent=1))
        for it in items:
            print(it["id"], it["set"], it["W"], it["H"], len(it.get("words") or []), "".join(it["panels"]))
        return

    seen: set[str] = set()
    n = 0
    for pdf in sorted((ROOT / "input").rglob("*.pdf")):
        if n >= n_rplots or "panel_extract" in pdf.parts or "editorial" in pdf.name.lower():
            continue
        try:
            doc = pymupdf.open(pdf)
        except Exception:  # noqa: BLE001
            continue
        if doc.page_count != 1 or doc[0].get_images():
            continue
        page = doc[0]
        words = _words_in(page, page.rect)
        key = " ".join(w["text"] for w in words)
        if not 4 <= len(words) <= 150 or key in seen:
            continue
        seen.add(key)
        iid = f"plot{n:02d}"
        pix = page.get_pixmap(dpi=DPI, alpha=False)
        pix.save(img_dir / f"{iid}.png")
        items.append({"id": iid, "image": f"images/{iid}.png", "W": pix.width, "H": pix.height,
                      "set": "vector", "words": words, "panels": [], "src": str(pdf.relative_to(ROOT))})
        n += 1

    (out / "gt.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    for it in items:
        print(it["id"], it["set"], it["W"], it["H"], len(it.get("words") or []), "".join(it["panels"]))


# --------------------------------------------------------------------------- engines
def _pil(path: Path):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    return Image.open(path).convert("RGB")


def _vlm_image(path: Path):
    """VLM 用にリサイズ（Spark 等で 300dpi 全画面は OOM になりやすい）。"""
    img = _pil(path)
    max_side = int(os.environ.get("BENCH_VLM_MAX_SIDE", "1280"))
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), resample=3)  # LANCZOS
    return img


class Tesseract:
    def __init__(self) -> None:
        import pymupdf

        self.pymupdf = pymupdf
        self.tessdata = pymupdf.get_tessdata()

    def __call__(self, path: Path) -> list[dict]:
        pix = self.pymupdf.Pixmap(str(path))
        if pix.alpha:
            pix = self.pymupdf.Pixmap(pix, 0)
        doc = self.pymupdf.open("pdf", pix.pdfocr_tobytes(language="eng", tessdata=self.tessdata))
        page = doc[0]
        sx, sy = pix.width / page.rect.width, pix.height / page.rect.height
        return [{"text": w[4], "box": [w[0] * sx, w[1] * sy, w[2] * sx, w[3] * sy]} for w in page.get_text("words")]


class Paddle:
    def __init__(self, size: str) -> None:
        from paddleocr import PaddleOCR

        self.ocr = PaddleOCR(
            text_detection_model_name=f"PP-OCRv5_{size}_det",
            text_recognition_model_name=f"PP-OCRv5_{size}_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_det_limit_type="max",
            text_det_limit_side_len=4096,
            enable_mkldnn=False,
        )

    def __call__(self, path: Path) -> list[dict]:
        out = []
        for r in self.ocr.predict(str(path)):
            j = r.json["res"]
            for t, s, b in zip(j["rec_texts"], j["rec_scores"], j["rec_boxes"], strict=False):
                out.append({"text": t, "box": [float(v) for v in b], "score": float(s)})
        return out


def _iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Florence2:
    TILE, OVERLAP = 768, 128

    def __init__(self) -> None:
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        mid = "florence-community/Florence-2-large"
        self.torch = torch
        self.dtype = torch.bfloat16 if DEVICE == "cuda" else torch.float32
        self.proc = AutoProcessor.from_pretrained(mid)
        self.model = Florence2ForConditionalGeneration.from_pretrained(mid, dtype=self.dtype).to(DEVICE).eval()

    def _tile(self, img) -> list[dict]:
        task = "<OCR_WITH_REGION>"
        inputs = self.proc(text=task, images=img, return_tensors="pt").to(DEVICE, self.dtype)
        with self.torch.inference_mode():
            ids = self.model.generate(**inputs, max_new_tokens=1024, num_beams=3, do_sample=False)
        text = self.proc.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = self.proc.post_process_generation(text, task=task, image_size=img.size)[task]
        out = []
        for quad, label in zip(parsed["quad_boxes"], parsed["labels"], strict=False):
            xs, ys = quad[0::2], quad[1::2]
            out.append({"text": label.replace("</s>", "").replace("<s>", "").strip(),
                        "box": [min(xs), min(ys), max(xs), max(ys)]})
        return out

    def __call__(self, path: Path) -> list[dict]:
        img = _pil(path)
        W, H = img.size
        step = self.TILE - self.OVERLAP
        dets: list[dict] = []
        for y in range(0, max(H - self.OVERLAP, 1), step):
            for x in range(0, max(W - self.OVERLAP, 1), step):
                box = (x, y, min(x + self.TILE, W), min(y + self.TILE, H))
                for d in self._tile(img.crop(box)):
                    b = d["box"]
                    d["box"] = [b[0] + x, b[1] + y, b[2] + x, b[3] + y]
                    if d["text"]:
                        dets.append(d)
        dets.sort(key=lambda d: -len(d["text"]))
        kept: list[dict] = []
        for d in dets:
            if all(_iou(d["box"], k["box"]) < 0.3 for k in kept):
                kept.append(d)
        return kept


_QWEN_PROMPT = (
    "Spot all the text in the image with line-level, and output in JSON format as "
    '[{"bbox_2d": [x1, y1, x2, y2], "text_content": "text"}, ...].'
)
_ITEM_RE = re.compile(r'\{\s*"bbox_2d"\s*:\s*\[([^\]]+)\]\s*,\s*"text_content"\s*:\s*"((?:[^"\\]|\\.)*)"')


class QwenVL:
    def __init__(self, version: str) -> None:
        import torch
        from transformers import AutoProcessor

        self.torch = torch
        self.version = version
        low_mem = os.environ.get("BENCH_VLM_LOW_MEM", "1" if os.uname().machine == "aarch64" else "0") == "1"
        if version == "2.5":
            from transformers import Qwen2_5_VLForConditionalGeneration as M

            mid = "Qwen/Qwen2.5-VL-7B-Instruct"
        else:
            from transformers import Qwen3VLForConditionalGeneration as M

            mid = "Qwen/Qwen3-VL-4B-Instruct" if low_mem else "Qwen/Qwen3-VL-8B-Instruct"
        self.proc = AutoProcessor.from_pretrained(mid)
        # Spark 等 aarch64: sdpa が Triton JIT を走らせ Python.h 未導入で落ちる → eager が安全
        attn = os.environ.get("BENCH_ATTN")
        if attn is None and os.uname().machine == "aarch64":
            attn = "eager"
        attn = attn or "sdpa"
        kwargs: dict = {"dtype": torch.bfloat16, "device_map": DEVICE, "attn_implementation": attn}
        self.model = M.from_pretrained(mid, **kwargs).eval()
        self._max_new = int(os.environ.get("BENCH_VLM_MAX_NEW", "2048" if low_mem else "8192"))

    def __call__(self, path: Path) -> list[dict]:
        img = _vlm_image(path)
        W, H = img.size
        msgs = [{"role": "user", "content": [{"type": "image", "image": img}, {"type": "text", "text": _QWEN_PROMPT}]}]
        text = self.proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = self.proc(text=[text], images=[img], return_tensors="pt").to(DEVICE)
        try:
            with self.torch.inference_mode():
                ids = self.model.generate(
                    **inputs,
                    max_new_tokens=self._max_new,
                    do_sample=False,
                    repetition_penalty=1.05,
                )
            raw = self.proc.batch_decode(ids[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]
        finally:
            del inputs
            if DEVICE == "cuda":
                self.torch.cuda.empty_cache()
        if self.version == "2.5":
            _, gh, gw = (int(v) for v in inputs["image_grid_thw"][0])
            sx, sy = W / (gw * 14), H / (gh * 14)
        else:
            sx, sy = W / 1000.0, H / 1000.0
        out = []
        for m in _ITEM_RE.finditer(raw):
            try:
                x0, y0, x1, y1 = (float(v) for v in m.group(1).split(","))
                t = json.loads(f'"{m.group(2)}"')
            except ValueError:
                continue
            out.append({"text": t, "box": [x0 * sx, y0 * sy, x1 * sx, y1 * sy]})
        return out


class AppleVision:
    def __init__(self) -> None:
        import Vision  # pyobjc-framework-Vision
        from Foundation import NSURL

        self.Vision, self.NSURL = Vision, NSURL

    def __call__(self, path: Path) -> list[dict]:
        V = self.Vision
        img = _pil(path)
        W, H = img.size
        handler = V.VNImageRequestHandler.alloc().initWithURL_options_(self.NSURL.fileURLWithPath_(str(path)), None)
        req = V.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(V.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(False)
        req.setRecognitionLanguages_(["en-US"])
        req.setMinimumTextHeight_(0.0)
        handler.performRequests_error_([req], None)
        out = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)[0]
            bb = obs.boundingBox()
            x0 = bb.origin.x * W
            y1 = (1.0 - bb.origin.y) * H
            out.append({"text": str(cand.string()), "score": float(cand.confidence()),
                        "box": [x0, y1 - bb.size.height * H, x0 + bb.size.width * W, y1]})
        return out


ENGINES = {
    "tesseract": Tesseract,
    "paddle_server": lambda: Paddle("server"),
    "paddle_mobile": lambda: Paddle("mobile"),
    "florence2": Florence2,
    "qwen25vl": lambda: QwenVL("2.5"),
    "qwen3vl": lambda: QwenVL("3"),
    "vision": AppleVision,
}


def _panel_letter_dets(dets: list[dict], case: str) -> list[tuple[str, list[float]]]:
    want = str.isupper if case == "upper" else str.islower
    best: dict[str, list[float]] = {}
    for d in dets:
        for tok in str(d["text"]).split():
            t = tok.strip(_TRIM)
            if len(t) == 1 and t.isalpha() and want(t):
                key = t.upper() if case == "upper" else t.lower()
                box = d["box"]
                if key not in best or (box[2] - box[0]) * (box[3] - box[1]) > (
                    best[key][2] - best[key][0]
                ) * (best[key][3] - best[key][1]):
                    best[key] = box
    return sorted(best.items(), key=lambda x: (_center(x[1])[1], _center(x[1])[0]))


def _crops_from_letters(w: int, h: int, letters: list[tuple[str, list[float]]]) -> list[dict]:
    if not letters:
        return [{"panel": "*", "box": [0.0, 0.0, float(w), float(h)]}]
    centers = [(_center(b)[0], _center(b)[1], ch, b) for ch, b in letters]
    row_eps = max(8.0, h * 0.04)
    rows: list[list[tuple]] = []
    for cx, cy, ch, b in sorted(centers, key=lambda t: (t[1], t[0])):
        placed = False
        for row in rows:
            if abs(cy - row[0][1]) <= row_eps:
                row.append((cx, cy, ch, b))
                placed = True
                break
        if not placed:
            rows.append([(cx, cy, ch, b)])
    for row in rows:
        row.sort(key=lambda t: t[0])
    rows.sort(key=lambda r: r[0][1])
    row_ys = [sum(c[1] for c in r) / len(r) for r in rows]
    out: list[dict] = []
    for ri, row in enumerate(rows):
        y_top = 0.0 if ri == 0 else (row_ys[ri - 1] + row_ys[ri]) / 2
        y_bot = float(h) if ri == len(rows) - 1 else (row_ys[ri] + row_ys[ri + 1]) / 2
        for ci, (cx, _cy, ch, b) in enumerate(row):
            x_left = 0.0 if ci == 0 else (row[ci - 1][0] + cx) / 2
            x_right = float(w) if ci == len(row) - 1 else (cx + row[ci + 1][0]) / 2
            pad = 4.0
            x0 = max(0.0, min(b[0], x_left) - pad)
            y0 = max(0.0, min(b[1], y_top) - pad)
            x1 = min(float(w), max(b[2], x_right) + pad)
            y1 = min(float(h), max(b[3], y_bot) + pad)
            if x1 - x0 > 20 and y1 - y0 > 20:
                out.append({"panel": ch, "box": [x0, y0, x1, y1]})
    return out or [{"panel": "*", "box": [0.0, 0.0, float(w), float(h)]}]


def _load_panel_layout(
    out: Path, item: dict, img_path: Path, *, layout_fn: Florence2,
) -> list[dict]:
    cache = out / "panel_layout" / f"{item['id']}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    work = out / "crops" / f"{item['id']}_layout.png"
    work.parent.mkdir(parents=True, exist_ok=True)
    img = _vlm_image(img_path)
    img.save(work)
    dets = layout_fn(work)
    letters = _panel_letter_dets(dets, item.get("case") or "upper")
    layout = _crops_from_letters(img.width, img.height, letters)
    scale_x = item["W"] / img.width
    scale_y = item["H"] / img.height
    for ent in layout:
        b = ent["box"]
        ent["box"] = [b[0] * scale_x, b[1] * scale_y, b[2] * scale_x, b[3] * scale_y]
        ent["layout_from"] = [L[0] for L in letters]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(layout, ensure_ascii=False, indent=1))
    print(f"[layout] {item['id']} {len(layout)} crops letters={''.join(x[0] for x in letters)}", flush=True)
    return layout


def _ocr_via_panels(
    ocr_fn,
    out: Path,
    item: dict,
    img_path: Path,
    layout_fn: Florence2,
) -> list[dict]:
    panels = _load_panel_layout(out, item, img_path, layout_fn=layout_fn)
    full = _pil(img_path)
    merged: list[dict] = []
    crop_dir = out / "crops" / item["id"]
    crop_dir.mkdir(parents=True, exist_ok=True)
    for ent in panels:
        x0, y0, x1, y1 = (int(v) for v in ent["box"])
        crop = full.crop((x0, y0, x1, y1))
        cp = crop_dir / f"{ent['panel']}.png"
        crop.save(cp)
        for d in ocr_fn(cp):
            b = d["box"]
            merged.append({**d, "box": [b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0]})
    return merged


def cmd_run(
    out: Path, engine: str, only: str | None, *, profile: str, layout: str,
) -> None:
    items = json.loads(_gt_path(out, profile).read_text())
    pred_path = _pred_path(out, engine, profile, layout)
    preds = json.loads(pred_path.read_text()) if pred_path.exists() else {}
    t0 = time.time()
    fn = ENGINES[engine]()
    layout_fn = Florence2() if layout == "panels" and engine != "florence2" else None
    if layout_fn:
        print(f"[{engine}] layout florence load {time.time() - t0:.1f}s", flush=True)
    else:
        print(f"[{engine}] load {time.time() - t0:.1f}s", flush=True)
    for it in items:
        done = it["id"] in preds and not preds[it["id"]].get("error")
        if done or (only and not re.search(only, it["id"])):
            continue
        t = time.time()
        try:
            img_path = out / it["image"]
            if layout == "panels":
                if engine == "florence2":
                    dets = _ocr_via_panels(fn, out, it, img_path, layout_fn=fn)
                else:
                    assert layout_fn is not None
                    dets = _ocr_via_panels(fn, out, it, img_path, layout_fn=layout_fn)
            else:
                dets = fn(img_path)
            err = None
        except Exception as exc:  # noqa: BLE001
            dets, err = [], repr(exc)[:300]
        dt = time.time() - t
        preds[it["id"]] = {"dets": dets, "sec": dt, "error": err, "layout": layout}
        print(f"[{engine}] {it['id']} {len(dets)} dets {dt:.1f}s {err or ''}", flush=True)
        pred_path.write_text(json.dumps(preds, ensure_ascii=False))
    print(f"[{engine}] done", flush=True)


# --------------------------------------------------------------------------- score
def _tokens(dets: list[dict]) -> list[tuple[str, list[float]]]:
    out = []
    for d in dets:
        for w in str(d["text"]).split():
            t = w.strip(_TRIM)
            if t:
                out.append((t, d["box"]))
    return out


def _center(b: list[float]) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _near(gt_box: list[float], pred_box: list[float], pad: float) -> bool:
    cx, cy = _center(gt_box)
    return pred_box[0] - pad <= cx <= pred_box[2] + pad and pred_box[1] - pad <= cy <= pred_box[3] + pad


def _match(gt: list[dict], toks: list[tuple[str, list[float]]], pad: float, *, fold: bool = False) -> int:
    used = [False] * len(toks)
    hit = 0
    for g in gt:
        gt_text = g["text"].casefold() if fold else g["text"]
        for i, (t, b) in enumerate(toks):
            if used[i]:
                continue
            if (t.casefold() if fold else t) == gt_text and _near(g["box"], b, pad):
                used[i] = True
                hit += 1
                break
    return hit


def cmd_score(out: Path, *, profile: str, layout: str) -> None:
    items = {it["id"]: it for it in json.loads(_gt_path(out, profile).read_text())}
    tag = _pred_tag(profile, layout)
    rows = []
    pred_paths: list[Path] = []
    for p in sorted(out.glob(f"preds_*{tag}.json")):
        engine = p.stem.removeprefix("preds_").removesuffix(tag)
        if engine not in ENGINES:
            continue
        pred_paths.append(p)
    for pred_path in pred_paths:
        engine = pred_path.stem.removeprefix("preds_").removesuffix(tag)
        preds = json.loads(pred_path.read_text())
        agg = {"w_gt": 0, "w_hit": 0, "w_hit_ci": 0, "w_pred": 0, "num_gt": 0, "num_hit": 0,
               "vp_gt": 0, "vp_hit": 0, "rp_gt": 0, "rp_hit": 0, "rp_extra": 0, "sec": 0.0, "n": 0, "err": 0}
        per_fig = []
        for iid, it in items.items():
            p = preds.get(iid)
            if p is None:
                continue
            agg["n"] += 1
            agg["sec"] += p["sec"]
            agg["err"] += bool(p.get("error"))
            toks = _tokens(p["dets"])
            pad = 0.01 * max(it["W"], it["H"])
            if it["set"] == "vector":
                gt = it["words"]
                agg["w_gt"] += len(gt)
                agg["w_hit"] += _match(gt, toks, pad)
                agg["w_hit_ci"] += _match(gt, toks, pad, fold=True)
                agg["w_pred"] += len(toks)
                nums = [g for g in gt if re.fullmatch(r"[-−]?\d+(?:\.\d+)?%?", g["text"])]
                agg["num_gt"] += len(nums)
                agg["num_hit"] += _match(nums, toks, pad)
                vp = it.get("panel_boxes") or []
                agg["vp_gt"] += len(vp)
                agg["vp_hit"] += _match(vp, toks, pad)
            if it["panels"]:
                want = str.isupper if it["case"] == "upper" else str.islower
                found = {t for t, _ in toks if len(t) == 1 and t.isalpha() and want(t)}
                gt_p = set(it["panels"])
                agg["rp_gt"] += len(gt_p)
                agg["rp_hit"] += len(gt_p & found)
                agg["rp_extra"] += len(found - gt_p)
                per_fig.append(f"{iid}:{len(gt_p & found)}/{len(gt_p)}")
        rows.append((engine, agg, per_fig))

    def pct(a: int, b: int) -> str:
        return f"{100 * a / b:5.1f}%" if b else "   - "

    print(f"{'engine':14} {'imgs':>4} {'word R':>7} {'R(ci)':>7} {'word P':>7} {'num R':>7} "
          f"{'vecPanel':>8} {'figPanel':>8} {'extra':>5} {'s/img':>6} err")
    for engine, a, _ in rows:
        print(f"{engine:14} {a['n']:4d} {pct(a['w_hit'], a['w_gt']):>7} {pct(a['w_hit_ci'], a['w_gt']):>7} "
              f"{pct(a['w_hit'], a['w_pred']):>7} {pct(a['num_hit'], a['num_gt']):>7} "
              f"{pct(a['vp_hit'], a['vp_gt']):>8} {pct(a['rp_hit'], a['rp_gt']):>8} {a['rp_extra']:5d} "
              f"{a['sec'] / max(a['n'], 1):6.1f} {a['err']}")
    for engine, _, per_fig in rows:
        print(f"  {engine}: {' '.join(per_fig)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "subset", "run", "score"])
    ap.add_argument("--out", type=Path, default=ROOT / "tmp/figure_ocr_bench")
    ap.add_argument("--profile", choices=("quick", "raster", "full"), default="full")
    ap.add_argument("--layout", choices=("full", "panels"), default="full")
    ap.add_argument("--engine", choices=sorted(ENGINES))
    ap.add_argument("--only", help="regex on item id")
    ap.add_argument("--n-rplots", type=int, default=30)
    a = ap.parse_args()
    if a.cmd == "prepare":
        cmd_prepare(a.out, a.n_rplots, profile=a.profile)
    elif a.cmd == "subset":
        cmd_subset(a.out, a.profile)
    elif a.cmd == "run":
        cmd_run(a.out, a.engine, a.only, profile=a.profile, layout=a.layout)
    else:
        cmd_score(a.out, profile=a.profile, layout=a.layout)


if __name__ == "__main__":
    main()
