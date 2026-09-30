#!/usr/bin/env python3
"""Prepare a holdout case for blind gold curation (no extraction, no OCR output).

Writes, all gitignored:
  input/panel_extract/<case>/legend_excerpt.txt   captions via pdftotext -raw
  input/panel_extract/<case>/figures/FigN.pdf     one cropped page per figure (eval input)
  input/panel_extract/<case>/review/FigN.png      300 dpi render for reading panel letters
  fixtures/gold/panel_extract/<case>/panel_extract_gold.json   empty, exhaustive (holdout) / hard_span (dev)
  fixtures/gold/panel_extract/<case>/panel_labels_gold.json    empty panels per figure
  fixtures/gold/panel_extract/<case>/case_manifest.json        split, figures_in_scope

Existing gold files are never overwritten. Curate them from the excerpt / PNGs
before running scripts/dev_generalization_eval.py (see HUMAN_REVIEW.md).

On a dev slot an existing legend_excerpt.txt and figures_in_scope are kept.
Papers whose tool output was already seen belong in dev (``--new --split dev``).

Examples:
  mkdir -p input/panel_extract/paper_03 && cp ~/Downloads/<oa>.pdf input/panel_extract/paper_03/
  python scripts/dev_holdout_prepare.py --case paper_03 --new
  python scripts/dev_holdout_prepare.py --case paper_09 --new --split dev
  # figure page detection missed one? pin it (1-based page) and re-run:
  python scripts/dev_holdout_prepare.py --case paper_03 --figure-page 4=9
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pre_peer_checker.eval import generalization as gz
from pre_peer_checker.parsers.figure_chunks import figure_label

_CAP_RE = re.compile(r"^(?:Fig\.?|Figure)[^\S\n]*(\d+)[^\S\n]*(?:[|.:]|$)", re.MULTILINE | re.IGNORECASE)
_DPI = 300
_REACH = 12.0
_MIN_RATIO_WITH_CAPTION = 0.1
_MIN_RATIO_ALONE = 0.2
_VECTOR_ART_DRAWINGS = 200  # vector figures run to thousands of paths; text pages with rules stay near 50

def _create_slot(case_id: str, split: str = "holdout") -> Path:
    gold_dir = gz.GOLD_ROOT / case_id
    if gold_dir.exists():
        return gold_dir
    shutil.copytree(gz.GOLD_ROOT / "_template", gold_dir, ignore=shutil.ignore_patterns("README.md"))
    for p in gold_dir.glob("*.example.json"):
        p.write_text(p.read_text(encoding="utf-8").replace("REPLACE_WITH_SLOT_ID", case_id), encoding="utf-8")
    manifest_path = gold_dir / "case_manifest.example.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["split"] = split
    manifest["figures_in_scope"] = []
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    note = (
        "ブラインド gold 作成中。"
        if split == "holdout"
        else "dev（ツールの失敗を見てよい）。Legend gold は hard_span で可。"
    )
    (gold_dir / "README.md").write_text(
        f"# {case_id}（{split}）\n\n{note}手順: [`HUMAN_REVIEW.md`](../HUMAN_REVIEW.md)\n",
        encoding="utf-8",
    )
    return gold_dir


def _captions(case: gz.Case) -> list[tuple[str, str]]:
    from pre_peer_checker.parsers.legend_struct import (
        extract_figure_captions_from_pdf,
        extract_structured_legends,
    )

    src = case.legend_source()
    if src is None:
        raise SystemExit(f"{case.case_id}: put one PDF or docx under {case.input_dir}/")
    kind, path = src
    if kind == "pdf":
        return extract_figure_captions_from_pdf(path)
    if kind == "docx":
        return [(leg.figure, leg.text) for leg in extract_structured_legends(path)]
    return []


def _caption_top(page) -> float:
    tops = [
        line["bbox"][1]
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines") or []
        if _CAP_RE.match("".join(s["text"] for s in line["spans"]).strip())
    ]
    return min(tops) if tops else page.rect.y1


def _art_rects(page) -> list:
    """Images and vector paths, minus running header/footer rules."""
    import pymupdf

    rects = [pymupdf.Rect(r["bbox"]) for r in page.get_image_info()]
    rects += [
        d["rect"]
        for d in page.get_drawings()
        if not (d["rect"].height < 2 and d["rect"].width >= 0.8 * page.rect.width)
    ]
    return [r for r in rects if not r.is_empty]


def _grow(seed, pending: list, reach: float):
    """Union ``seed`` with every rect reachable through a chain of gaps <= ``reach``."""
    clip = seed
    grew = True
    while grew:
        zone = clip + (-reach, -reach, reach, reach)
        near = [r for r in pending if r.intersects(zone)]
        pending = [r for r in pending if not r.intersects(zone)]
        for r in near:
            clip |= r
        grew = bool(near)
    return clip


def _artwork_clip(page, big):
    """Largest image grown by chaining nearby vector art, text and images, above the caption.

    Composite figures keep panel letters and schematics as vector content outside the
    raster tiles. ``_REACH`` stays below the gap to the running header.
    """
    import pymupdf

    limit = _caption_top(page)
    rects = _art_rects(page)
    rects += [
        pymupdf.Rect(s["bbox"])
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines") or []
        for s in line["spans"]
        if s["text"].strip()
    ]
    pending = [r for r in rects if r.y1 <= limit + 0.5]
    return (_grow(pymupdf.Rect(big), pending, _REACH) + (-4, -4, 4, 4)) & page.rect


@dataclass
class _Page:
    index: int
    clip: object
    image_ratio: float
    vector_art: bool
    keys: list[str]

    def is_figure(self, min_ratio: float) -> bool:
        return self.vector_art or self.image_ratio >= min_ratio


def _figure_pages(pdf: Path, wanted: set[str], pinned: dict[str, int]) -> dict[str, tuple[int, object]]:
    """figure key -> (0-based page, crop rect). Pinned pages win over detected ones.

    A caption claims its own page when that page carries artwork; otherwise the
    artwork-only page right before it (figure page followed by a caption page).
    Figures left over (set inside text pages, Extended Data) come from the product's
    caption-anchored finder.
    """
    import pymupdf

    pages: list[_Page] = []
    doc = pymupdf.open(pdf)
    try:
        for i, page in enumerate(doc):
            area = page.rect.width * page.rect.height
            big = max(
                page.get_image_info(),
                key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]),
                default=None,
            )
            clip, ratio = page.rect, 0.0
            if big is not None:
                bb = pymupdf.Rect(big["bbox"]) & page.rect
                clip = _artwork_clip(page, bb)
                ratio = bb.width * bb.height / area
            keys = [m.group(1) for m in _CAP_RE.finditer(page.get_text()) if m.group(1) in wanted]
            pages.append(_Page(i, clip, ratio, len(page.get_drawings()) >= _VECTOR_ART_DRAWINGS, keys))
    finally:
        doc.close()

    out: dict[str, tuple[int, object]] = {}
    claimed: set[int] = set()
    for pos, pg in enumerate(pages):
        for key in pg.keys:
            if key in out:
                continue
            if pg.is_figure(_MIN_RATIO_WITH_CAPTION):
                out[key] = (pg.index, pg.clip)
                claimed.add(pg.index)
                continue
            prev = pages[pos - 1] if pos else None
            if prev and not prev.keys and prev.index not in claimed and prev.is_figure(_MIN_RATIO_ALONE):
                out[key] = (prev.index, prev.clip)
                claimed.add(prev.index)
    if wanted - out.keys():
        from pre_peer_checker.parsers.article_figures import find_article_figures

        for fig in find_article_figures(pdf):
            if fig.key in wanted and fig.key not in out:
                out[fig.key] = (fig.page_index, pymupdf.Rect(fig.clip))
    # A pin that agrees with detection keeps the detected crop (two figures can share a page).
    for key, pg in pinned.items():
        if 0 < pg <= len(pages) and (key not in out or out[key][0] != pg - 1):
            out[key] = (pages[pg - 1].index, pages[pg - 1].clip)
    return out


def _figure_sort_key(key: str) -> tuple[str, int]:
    """'1' < '2' < 'ED1' < 'S1' (main, then Extended Data, then supplementary)."""
    m = re.match(r"([A-Z]*)(\d+)", key)
    return (m.group(1), int(m.group(2))) if m else (key, 0)


def _write_figures(pdf: Path, pages: dict[str, tuple[int, object]], case: gz.Case) -> None:
    import pymupdf

    fig_dir = case.input_dir / "figures"
    review_dir = case.input_dir / "review"
    fig_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    src = pymupdf.open(pdf)
    try:
        for key, (i, clip) in sorted(pages.items(), key=lambda kv: _figure_sort_key(kv[0])):
            stem = f"Extended_Data_Fig{key[2:]}" if key.startswith("ED") else f"Fig{key}"
            one = pymupdf.open()
            one.insert_pdf(src, from_page=i, to_page=i)
            page = one[0]
            page.set_cropbox(clip & page.mediabox)
            one.save(fig_dir / f"{stem}.pdf")
            page.get_pixmap(dpi=_DPI, alpha=False).save(review_dir / f"{stem}.png")
            one.close()
    finally:
        src.close()


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_if_missing(path: Path, payload: dict) -> bool:
    if path.exists():
        return False
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", required=True, help="Anonymous slot id, e.g. paper_03")
    ap.add_argument("--new", action="store_true", help="Create the slot from _template")
    ap.add_argument(
        "--split",
        choices=gz.SPLITS,
        default="holdout",
        help="Split for a --new slot. Use dev for papers whose tool output was already seen.",
    )
    ap.add_argument("--figures", default=None, help="Comma list of figure numbers in scope (default: all captions)")
    ap.add_argument("--figure-page", action="append", default=[], metavar="N=PAGE", help="Pin figure N to 1-based PAGE")
    args = ap.parse_args(argv)

    if args.new:
        _create_slot(args.case, args.split)
    gold_dir = gz.GOLD_ROOT / args.case
    case = gz.load_case(gold_dir)
    if case is None:
        print(f"no slot {gold_dir}; use --new", file=sys.stderr)
        return 2
    holdout = case.split == "holdout"

    captions = _captions(case)
    keys = [gz.figure_key(fig) for fig, _ in captions]
    existing = [str(f) for f in case.manifest.get("figures_in_scope") or []]
    if args.figures:
        scope = [k.strip() for k in args.figures.split(",")]
    elif not holdout and existing:
        scope = existing
    else:
        scope = keys
    pinned = dict(case.manifest.get("figure_pages") or {})
    for spec in args.figure_page:
        k, _, pg = spec.partition("=")
        pinned[k.strip()] = int(pg)

    case.input_dir.mkdir(parents=True, exist_ok=True)
    excerpt = case.input_dir / "legend_excerpt.txt"
    if holdout or not excerpt.exists():
        excerpt.write_text(
            "\n\n".join(text.strip() for fig, text in captions if gz.figure_key(fig) in scope) + "\n",
            encoding="utf-8",
        )

    src = case.legend_source()
    pages: dict[str, tuple[int, object]] = {}
    if src and src[0] == "pdf":
        pages = _figure_pages(src[1], set(scope), {k: int(v) for k, v in pinned.items()})
        _write_figures(src[1], pages, case)

    case.manifest["figures_in_scope"] = scope
    case.manifest["figure_pages"] = {k: i + 1 for k, (i, _clip) in sorted(pages.items())}
    case.save_manifest()

    legend_new = _write_if_missing(
        case.gold_path("legend"),
        {
            "schema_version": "1.0",
            "case_id": case.case_id,
            "role": "legend_panel_extract_gold",
            "description": (
                "Holdout legend gold, curated blind (no tool output)."
                if holdout
                else "Dev legend gold (hard spans; tool output may be consulted)."
            ),
            "coverage": "exhaustive" if holdout else "hard_span",
            "notes": [f"figures_in_scope: {', '.join(scope)}"],
            "source": {
                "kind": "published_pdf" if src and src[0] == "pdf" else "manuscript_docx",
                "local_path": f"input/panel_extract/{case.case_id}/",
                "has_experimental_data": False,
            },
            "review": {"status": "draft", "reviewed_by": "", "reviewed_at": ""},
            "items": [],
        },
    )
    labels_new = _write_if_missing(
        case.gold_path("panel_ocr"),
        {
            "schema_version": "1.0",
            "case_id": case.case_id,
            "role": "panel_labels_gold",
            "description": (
                "Holdout panel letters printed on each figure, curated blind from review/FigN.png."
                if holdout
                else "Dev panel letters printed on each figure, read from review/FigN.png."
            ),
            "review": {"status": "draft", "reviewed_by": "", "reviewed_at": ""},
            "figures": [
                {"figure": figure_label(k), "case": "", "panels": []} for k in sorted(pages, key=_figure_sort_key)
            ],
        },
    )

    print(f"{case.case_id}: captions={len(captions)} scope={','.join(scope)}")
    print(f"  excerpt: {_rel(excerpt)}")
    missing = [k for k in scope if k not in pages]
    if src and src[0] == "pdf":
        print(f"  figures: {len(pages)} -> {_rel(case.input_dir)}/figures, review/")
        if missing:
            print(f"  no figure page found for: {', '.join(missing)} (pin with --figure-page N=PAGE)")
    else:
        print("  docx source: place FigN.pdf/png under figures/ yourself for the OCR task")
    golds = [
        ("legend gold", case.gold_path("legend"), legend_new),
        ("panel labels gold", case.gold_path("panel_ocr"), labels_new),
    ]
    for label, path, new in golds:
        print(f"  {label}: {_rel(path)} ({'created' if new else 'kept existing'})")
    if holdout:
        print("Next: curate both gold files blind, set review.status=confirmed, then run dev_generalization_eval.py")
    else:
        print("Next: fill the gold files (legend hard spans may use tool output), then run dev_generalization_eval.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
