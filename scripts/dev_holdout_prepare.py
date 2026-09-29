#!/usr/bin/env python3
"""Prepare a holdout case for blind gold curation (no extraction, no OCR output).

Writes, all gitignored:
  input/panel_extract/<case>/legend_excerpt.txt   captions via pdftotext -raw
  input/panel_extract/<case>/figures/FigN.pdf     one cropped page per figure (eval input)
  input/panel_extract/<case>/review/FigN.png      300 dpi render for reading panel letters
  fixtures/gold/panel_extract/<case>/panel_extract_gold.json   empty, coverage=exhaustive
  fixtures/gold/panel_extract/<case>/panel_labels_gold.json    empty panels per figure
  fixtures/gold/panel_extract/<case>/case_manifest.json        split=holdout, figures_in_scope

Existing gold files are never overwritten. Curate them from the excerpt / PNGs
before running scripts/dev_generalization_eval.py (see HUMAN_REVIEW.md).

Examples:
  mkdir -p input/panel_extract/paper_03 && cp ~/Downloads/<oa>.pdf input/panel_extract/paper_03/
  python scripts/dev_holdout_prepare.py --case paper_03 --new
  # figure page detection missed one? pin it (1-based page) and re-run:
  python scripts/dev_holdout_prepare.py --case paper_03 --figure-page 4=9
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pre_peer_checker.eval import generalization as gz

_CAP_RE = re.compile(r"^(?:Fig\.?|Figure)\s*(\d+)\s*[|.:]", re.MULTILINE | re.IGNORECASE)
_DPI = 300


def _create_slot(case_id: str) -> Path:
    gold_dir = gz.GOLD_ROOT / case_id
    if gold_dir.exists():
        return gold_dir
    shutil.copytree(gz.GOLD_ROOT / "_template", gold_dir, ignore=shutil.ignore_patterns("README.md"))
    for p in gold_dir.glob("*.example.json"):
        p.write_text(p.read_text(encoding="utf-8").replace("REPLACE_WITH_SLOT_ID", case_id), encoding="utf-8")
    (gold_dir / "README.md").write_text(
        f"# {case_id}（holdout）\n\nブラインド gold 作成中。手順: [`HUMAN_REVIEW.md`](../HUMAN_REVIEW.md)\n",
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


def _figure_pages(pdf: Path, wanted: set[str], pinned: dict[str, int]) -> dict[str, tuple[int, object]]:
    """figure key -> (0-based page, crop rect). Pinned pages win over detection."""
    import pymupdf

    out: dict[str, tuple[int, object]] = {}
    doc = pymupdf.open(pdf)
    try:
        for i, page in enumerate(doc):
            area = page.rect.width * page.rect.height
            infos = page.get_image_info()
            big = max(
                infos,
                key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]),
                default=None,
            )
            clip = page.rect
            has_artwork = False
            if big is not None:
                bb = pymupdf.Rect(big["bbox"]) & page.rect
                if bb.width * bb.height >= 0.2 * area:
                    clip = bb
                    has_artwork = True
            for k in [k for k, pg in pinned.items() if pg - 1 == i]:
                out[k] = (i, clip)
            if not has_artwork and len(page.get_drawings()) < 50:
                continue
            for m in _CAP_RE.finditer(page.get_text()):
                key = m.group(1)
                if key in wanted and key not in out and key not in pinned:
                    out[key] = (i, clip)
    finally:
        doc.close()
    return out


def _write_figures(pdf: Path, pages: dict[str, tuple[int, object]], case: gz.Case) -> None:
    import pymupdf

    fig_dir = case.input_dir / "figures"
    review_dir = case.input_dir / "review"
    fig_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    src = pymupdf.open(pdf)
    try:
        for key, (i, clip) in sorted(pages.items(), key=lambda kv: int(kv[0])):
            one = pymupdf.open()
            one.insert_pdf(src, from_page=i, to_page=i)
            page = one[0]
            page.set_cropbox(clip & page.mediabox)
            one.save(fig_dir / f"Fig{key}.pdf")
            page.get_pixmap(dpi=_DPI, alpha=False).save(review_dir / f"Fig{key}.png")
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
    ap.add_argument("--new", action="store_true", help="Create the slot from _template (split=holdout)")
    ap.add_argument("--figures", default=None, help="Comma list of figure numbers in scope (default: all captions)")
    ap.add_argument("--figure-page", action="append", default=[], metavar="N=PAGE", help="Pin figure N to 1-based PAGE")
    args = ap.parse_args(argv)

    if args.new:
        _create_slot(args.case)
    gold_dir = gz.GOLD_ROOT / args.case
    case = gz.load_case(gold_dir)
    if case is None:
        print(f"no slot {gold_dir}; use --new", file=sys.stderr)
        return 2
    if case.split != "holdout":
        print(f"{args.case} is split={case.split}; prepare is for holdout slots", file=sys.stderr)
        return 2

    captions = _captions(case)
    keys = [gz.figure_key(fig) for fig, _ in captions]
    scope = [k.strip() for k in args.figures.split(",")] if args.figures else keys
    pinned = dict(case.manifest.get("figure_pages") or {})
    for spec in args.figure_page:
        k, _, pg = spec.partition("=")
        pinned[k.strip()] = int(pg)

    case.input_dir.mkdir(parents=True, exist_ok=True)
    excerpt = case.input_dir / "legend_excerpt.txt"
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
            "description": "Holdout legend gold, curated blind (no tool output).",
            "coverage": "exhaustive",
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
            "description": "Holdout panel letters printed on each figure, curated blind from review/FigN.png.",
            "review": {"status": "draft", "reviewed_by": "", "reviewed_at": ""},
            "figures": [
                {"figure": f"Figure {k}", "case": "", "panels": []} for k in sorted(pages, key=int)
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
    for label, path, new in (
        ("legend gold", case.gold_path("legend"), legend_new),
        ("panel labels gold", case.gold_path("panel_ocr"), labels_new),
    ):
        print(f"  {label}: {_rel(path)} ({'created' if new else 'kept existing'})")
    print("Next: curate both gold files blind, set review.status=confirmed, then run dev_generalization_eval.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
