"""VLM check of doubtful panel-label positions on a raster figure (box geometry only).

OCR gives letter positions; two kinds stay doubtful after the geometric rules:

- a letter that is not at the top-left corner of any artwork (axis ticks, table
  text, "P value") — probably not a panel label;
- a photo with no letter at its top-left corner — its label may be one OCR missed.

The figure is drawn with numbered boxes on those spots and the VLM reads the letter
in each box (or ``null``). Only answers the geometry can use are applied: a doubtful
letter the VLM calls ``null`` loses its box, and an unlabeled photo gets a letter
from the figure's own letter range that has no position yet. The panel letter list
itself is not changed, so warnings still rest on deterministic signals only.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pre_peer_checker.parsers.figure_panel_layout import _contains, _corner_owner, box_center

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_MAX_SPOTS = 24
_MAX_SIDE = 1600

PROMPT = (
    "This is a scientific figure made of several panels. Numbered red boxes mark spots "
    "where a panel label (a single letter such as a, b, C or D) might be printed.\n"
    "For each number, give the panel label letter printed inside that box, or null if "
    "the box holds no panel label (axis tick labels, table text, gene names, units, or "
    "nothing count as null). Answer only with JSON, for example "
    '{"1": "b", "2": null, "3": "E"}.'
)


def vlm_select_enabled() -> bool:
    mode = (os.environ.get("PRE_PEER_CHECKER_PANEL_VLM_SELECT") or "").strip().lower()
    return mode in {"1", "true", "yes", "on"}


@dataclass
class Spot:
    kind: str  # "label" (doubtful OCR letter) | "photo" (photo without a letter)
    box: list[float]
    letter: str | None = None
    photo: list[float] | None = None


@dataclass
class SelectResult:
    labels: dict[str, list[float]]
    not_labels: set[str] = field(default_factory=set)
    added: set[str] = field(default_factory=set)
    answers: dict[str, Any] = field(default_factory=dict)


def _cornered(k: str, centers: dict[str, tuple[float, float]], cores, label_h: float) -> bool:
    return any(_corner_owner(el, {k: centers[k]}, label_h) == k for el in cores)


def doubtful_spots(
    labels: dict[str, list[float]],
    photos: list[list[float]],
    ink: list[list[float]] | None = None,
) -> list[Spot]:
    if not labels:
        return []
    heights = sorted(b[3] - b[1] for b in labels.values())
    label_h = heights[len(heights) // 2]
    core_side = 2.0 * label_h
    cores = [e for e in list(photos) + list(ink or []) if min(e[2] - e[0], e[3] - e[1]) >= core_side]
    centers = {k: box_center(b) for k, b in labels.items()}
    pad = 0.5 * label_h
    spots = [
        Spot("label", [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad], letter=k)
        for k, b in sorted(labels.items())
        if not _cornered(k, centers, cores, label_h)
    ]
    for p in photos:
        if _corner_owner(p, centers, label_h) is not None:
            continue
        zone = [p[0] - 2 * label_h, p[1] - 2 * label_h, p[0] + 3 * label_h, p[1] + 3 * label_h]
        if any(_contains(zone, *box_center(s.box)) for s in spots):
            continue
        spots.append(Spot("photo", zone, photo=list(p)))
    return spots[:_MAX_SPOTS]


def _draw(image, spots: list[Spot], path: Path) -> None:
    from PIL import ImageDraw

    scale = min(1.0, _MAX_SIDE / max(image.width, image.height))
    img = image.convert("RGB")
    if scale < 1.0:
        img = img.resize((int(image.width * scale), int(image.height * scale)))
    dr = ImageDraw.Draw(img)
    for i, s in enumerate(spots, start=1):
        b = [v * scale for v in s.box]
        dr.rectangle(b, outline=(255, 0, 0), width=3)
        dr.text((b[2] + 3, b[1]), str(i), fill=(255, 0, 0))
    img.save(path)


def parse_answers(text: str) -> dict[str, Any]:
    m = _JSON_RE.search(str(text or ""))
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return {str(k): v for k, v in obj.items()} if isinstance(obj, dict) else {}


def _letter(v: Any) -> str | None:
    t = str(v or "").strip().strip("()'\".")
    return t.upper() if len(t) == 1 and t.isalpha() and t.isascii() else None


def _is_null(answers: dict[str, Any], key: str) -> bool:
    """An explicit "no label here"; a different letter is not trusted (small VLMs count boxes)."""
    if key not in answers:
        return False
    v = answers[key]
    return v is None or str(v).strip().lower() in {"", "null", "none"}


def apply_answers(
    labels: dict[str, list[float]],
    spots: list[Spot],
    answers: dict[str, Any],
    letter_range: tuple[str, str] | None,
) -> SelectResult:
    """Keys are upper-case letters. ``letter_range`` bounds letters a photo may take."""
    heights = sorted(b[3] - b[1] for b in labels.values()) or [0.0]
    label_h = heights[len(heights) // 2]
    out = SelectResult(labels=dict(labels), answers=answers)
    for i, s in enumerate(spots, start=1):
        if s.kind == "label" and s.letter is not None and _is_null(answers, str(i)):
            out.labels.pop(s.letter, None)
            out.not_labels.add(s.letter)
    for i, s in enumerate(spots, start=1):
        said = _letter(answers.get(str(i)))
        if s.kind != "photo" or said is None or s.photo is None:
            continue
        if letter_range and not letter_range[0] <= said <= letter_range[1]:
            continue
        if said in out.labels:
            continue
        p = s.photo
        out.labels[said] = [p[0], p[1], p[0] + label_h, p[1] + label_h]
        out.added.add(said)
        out.not_labels.discard(said)
    return out


def select_labels_with_vlm(
    image,
    labels: dict[str, list[float]],
    photos: list[list[float]],
    ink: list[list[float]] | None,
    backend,
) -> SelectResult | None:
    """None when nothing is doubtful (no VLM call)."""
    spots = doubtful_spots(labels, photos, ink)
    if not spots or backend is None:
        return None
    keys = sorted(labels)
    letter_range = (keys[0], chr(ord(keys[-1]) + 1)) if keys else None
    with tempfile.TemporaryDirectory(prefix="ppc-panel-vlm-") as tmp:
        path = Path(tmp) / "spots.png"
        _draw(image, spots, path)
        text = backend.generate(PROMPT, path, max_tokens=256)
    return apply_answers(labels, spots, parse_answers(text), letter_range)
