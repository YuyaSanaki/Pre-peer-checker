"""Panel layout helpers for raster figure OCR."""

from __future__ import annotations

from pre_peer_checker.parsers.figure_panel_layout import (
    crops_from_panel_letters,
    panel_letter_dets,
)


def test_panel_letter_dets_picks_largest_box():
    dets = [
        {"text": "A", "box": [0, 0, 10, 10]},
        {"text": "A", "box": [0, 0, 30, 30]},
        {"text": "B", "box": [50, 0, 60, 10]},
    ]
    letters = panel_letter_dets(dets, case="upper")
    assert {ch for ch, _ in letters} == {"A", "B"}
    a_box = next(b for ch, b in letters if ch == "A")
    assert a_box[2] - a_box[0] == 30


def test_crops_from_letters_two_columns():
    letters = [("A", [10, 10, 20, 20]), ("B", [100, 12, 110, 22])]
    crops = crops_from_panel_letters(200, 100, letters)
    assert len(crops) >= 2
    assert all(c["panel"] in {"A", "B"} for c in crops)
