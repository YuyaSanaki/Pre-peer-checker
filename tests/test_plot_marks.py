"""Panel marks / panel kind on a synthetic figure page."""

from __future__ import annotations

import io

import fitz
import numpy as np
import pytest

from pre_peer_checker.parsers.pdf_panel_plots import _panel_labels, caption_top
from pre_peer_checker.parsers.plot_marks import raster_dot_centroids, read_panel_marks


def _png(arr: np.ndarray) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, arr.shape[1], arr.shape[0], arr.tobytes(), False)
    return pix.tobytes("png")


def _figure_page() -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    for letter, (x, y) in {"A": (20, 30), "B": (310, 30), "C": (20, 330), "D": (310, 330)}.items():
        page.insert_text((x, y), letter, fontsize=12, fontname="hebo")
    # A: filled and hollow markers
    for i in range(6):
        page.draw_circle((60 + 25 * i, 150), 2.5, color=None, fill=(0, 0, 0))
    for i in range(4):
        page.draw_circle((60 + 25 * i, 200), 2.5, color=(0, 0, 0), fill=None, width=0.6)
    # B: bars with capped error bars
    for i in range(3):
        x = 360 + 50 * i
        page.draw_rect(fitz.Rect(x, 150, x + 20, 280), color=None, fill=(0.5, 0.5, 0.5))
        page.draw_line((x + 10, 130), (x + 10, 150), width=0.5)
        page.draw_line((x + 6, 130), (x + 14, 130), width=0.5)
    # C: raster scatter on white paper (4 px per pt, ~2.5 pt markers)
    img = np.full((800, 960, 3), 255, np.uint8)
    yy, xx = np.mgrid[:800, :960]
    for cx, cy in [(160, 200), (360, 480), (600, 240), (800, 600), (480, 680)]:
        img[(xx - cx) ** 2 + (yy - cy) ** 2 <= 100] = 0
    page.insert_image(fitz.Rect(40, 350, 280, 550), stream=_png(img))
    # D: textured photo
    rng = np.random.default_rng(0)
    photo = rng.integers(40, 200, size=(200, 240, 3), dtype=np.uint8)
    page.insert_image(fitz.Rect(330, 350, 570, 550), stream=_png(photo))
    # caption with bold section letters that are not panel labels
    page.insert_text((20, 620), "Fig. 1 | Synthetic figure.", fontsize=8, fontname="hebo")
    for i, letter in enumerate("abcdefgh"):
        page.insert_text((20 + 30 * i, 640), letter, fontsize=8, fontname="hebo")
    return doc


@pytest.fixture(scope="module")
def marks():
    doc = _figure_page()
    page = doc[0]
    labels = _panel_labels(page)
    return page, labels, read_panel_marks(page, labels)


def test_caption_letters_are_not_panel_labels(marks):
    page, labels, _ = marks
    assert 600 < caption_top(page) < 620
    assert sorted(labels) == ["A", "B", "C", "D"]


def test_panel_kinds_and_marks(marks):
    _, _, pm = marks
    assert pm["A"].kind == "plot" and len(pm["A"].dots) == 10 and pm["A"].hollow == 4
    assert pm["B"].kind == "plot" and pm["B"].bars == 3 and pm["B"].errorbars == 3
    assert pm["C"].source == "raster" and pm["C"].n_estimate == 5
    assert pm["D"].kind == "image" and not pm["D"].dots


def test_merged_raster_blob_counts_as_lower_bound():
    img = np.full((240, 400), 255, np.uint8)
    yy, xx = np.mgrid[:240, :400]
    for cx in (60, 160, 260):
        img[(xx - cx) ** 2 + (yy - 60) ** 2 <= 100] = 0
    for cx in (120, 134):  # two touching markers
        img[(xx - cx) ** 2 + (yy - 170) ** 2 <= 100] = 0
    cents, est, overlapped = raster_dot_centroids(img)
    assert len(cents) == 4 and est == 5 and overlapped
