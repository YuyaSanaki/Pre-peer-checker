"""Panel layout helpers for raster figure OCR."""

from __future__ import annotations

from pre_peer_checker.parsers.figure_panel_layout import (
    crops_from_panel_letters,
    dominant_panel_run,
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


def test_region_dicts_from_crops_skip_star():
    from pre_peer_checker.parsers.figure_panel_layout import region_dicts_from_crops

    regs = region_dicts_from_crops(
        "Fig1.png",
        [
            {"panel": "*", "box": [0, 0, 200, 100]},
            {"panel": "A", "box": [0, 0, 90, 100]},
            {"panel": "B", "box": [90, 0, 200, 100]},
        ],
        200,
        100,
    )
    assert {r["panel"] for r in regs} == {"A", "B"}
    assert regs[0]["geometry_source"] == "raster_ocr"
    assert regs[0]["pct"]["width_pct"] > 0


def test_dominant_run_keeps_single_missed_letter():
    """Observed OCR output: A..K with E and I missed, plus stray O and U."""
    assert dominant_panel_run(set("ABCDFGHJK") | {"O", "U"}) == set("ABCDFGHJK")


def test_dominant_run_drops_non_ascii():
    assert dominant_panel_run(set("ABCDEF") | {"Г"}) == set("ABCDEF")


def test_dominant_run_allows_lowercase_and_empty():
    assert dominant_panel_run(set("abcd")) == set("abcd")
    assert dominant_panel_run(set()) == set()
