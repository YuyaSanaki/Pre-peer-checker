#!/usr/bin/env python3
"""VLM パネル地図補助の検証（Mac mlx-vlm / Linux CUDA）.

Mac::

    pip install -e ".[vlm-mlx]"
    python scripts/dev_vlm_panel_map_verify.py --prefer mlx \\
      --synthetic --require-vlm

合成 PDF は「画像として描いたパネル文字」を使う（テキスト抽出では拾わず、
ベクター分割は空のまま。ラスタ上では VLM が A/B を見える）。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _build_synthetic_raster_letter_pdf(dest: Path) -> Path:
    """PDF with panel letters as embedded images (no extractable text spans)."""
    import fitz
    from PIL import Image, ImageDraw, ImageFont

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((40, 28), "Figure 1", fontsize=14)

    def letter_png(ch: str) -> bytes:
        img = Image.new("RGB", (96, 96), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72
            )
        except Exception:
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", 72)
            except Exception:
                font = ImageFont.load_default()
        draw.text((16, 4), ch, fill=(0, 0, 0), font=font)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # Two panels: A top-left, B top-right — visible to VLM, invisible to text labels
    for ch, rect in (
        ("A", fitz.Rect(30, 50, 126, 146)),
        ("B", fitz.Rect(320, 50, 416, 146)),
    ):
        page.insert_image(rect, stream=letter_png(ch))
        page.draw_rect(
            fitz.Rect(rect.x0 - 10, rect.y0 - 10, rect.x0 + 250, rect.y0 + 160),
            color=(0.7, 0.7, 0.7),
        )

    doc.save(dest)
    doc.close()
    return dest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prefer", default="auto", help="auto|mlx|cuda|transformers|none")
    p.add_argument("--profile", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--pdf", type=Path, default=None, help="出版 Fig PDF")
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="画像パネル文字 PDF を作り、ベクター空→VLM 補助を強制",
    )
    p.add_argument("--require-vlm", action="store_true")
    p.add_argument("-o", "--out", type=Path, default=None)
    args = p.parse_args(argv)

    from pre_peer_checker.llm.panel_map_assist import extract_panel_regions_vector_then_vlm
    from pre_peer_checker.llm.vlm_backend import select_vlm_backend, vlm_probe
    from pre_peer_checker.parsers.pdf_panel_geometry import extract_panel_regions_from_pdf

    probe = vlm_probe()
    print("=== probe ===")
    print(json.dumps(probe, ensure_ascii=False, indent=2))

    backend = select_vlm_backend(
        args.prefer, model_id=args.model, profile_id=args.profile
    )
    if backend is None:
        print("FAIL: no VLM backend", file=sys.stderr)
        return 2
    print("=== backend ===")
    print(json.dumps(backend.info().__dict__, ensure_ascii=False, indent=2))

    pdf = args.pdf
    if args.synthetic or pdf is None:
        td = Path(args.out).parent if args.out else Path("outputs/metrics")
        td.mkdir(parents=True, exist_ok=True)
        pdf = _build_synthetic_raster_letter_pdf(td / "vlm_assist_raster_letters.pdf")
        vec_n = len(extract_panel_regions_from_pdf(pdf, max_pages=1))
        print(f"synthetic raster-letter pdf: {pdf} (vector_regions={vec_n})")
        if vec_n > 0:
            print(
                "WARN: vector found labels; expected 0 for this fixture",
                file=sys.stderr,
            )

    regions, status = extract_panel_regions_vector_then_vlm(
        [pdf],
        vlm_assist=True,
        vlm_prefer=args.prefer,
        vlm_profile=args.profile,
        vlm_model=args.model,
        min_vector_panels=1,
    )
    print("=== status ===")
    print(json.dumps(status, ensure_ascii=False, indent=2)[:2500])
    print(f"n_regions={len(regions)} n_vlm={status.get('n_vlm')}")
    if regions:
        print("panels:", sorted({r.get("panel") for r in regions}))

    report = {"probe": probe, "status": status, "regions": regions[:20]}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.out}")

    attempts = status.get("vlm_attempts") or []
    notes = [a.get("note") for a in attempts]
    if args.require_vlm and not status.get("vlm_used"):
        print(
            "FAIL: VLM did not yield panels. "
            f"attempts_notes={notes}. "
            "Expect GenerationResult unwrap + visible A/B on raster.",
            file=sys.stderr,
        )
        return 3
    if args.require_vlm and status.get("n_vlm", 0) < 1:
        print("FAIL: no VLM regions", file=sys.stderr)
        return 4
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
