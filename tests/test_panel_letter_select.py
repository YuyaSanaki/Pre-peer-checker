"""VLM check of doubtful raster panel-label spots (stub backend)."""

from __future__ import annotations

from PIL import Image

from pre_peer_checker.llm.panel_letter_select import (
    apply_answers,
    doubtful_spots,
    parse_answers,
    select_labels_with_vlm,
)
from pre_peer_checker.llm.vlm_backend import CallableVlmBackend

PHOTOS = [[x, 100.0, x + 90.0, 200.0] for x in (0.0, 100.0, 200.0)]
LABELS = {
    "A": [2.0, 80.0, 14.0, 96.0],
    "C": [202.0, 80.0, 214.0, 96.0],
    "P": [150.0, 300.0, 162.0, 316.0],  # table header far from any artwork corner
}


def test_doubtful_spots_are_stray_letters_and_unlabeled_photos():
    spots = doubtful_spots(LABELS, PHOTOS)
    assert [(s.kind, s.letter) for s in spots] == [("label", "P"), ("photo", None)]
    assert spots[1].photo == PHOTOS[1]


def test_answers_drop_unconfirmed_letters_and_label_photos():
    spots = doubtful_spots(LABELS, PHOTOS)
    res = apply_answers(LABELS, spots, {"1": None, "2": "b"}, ("A", "D"))
    assert res.not_labels == {"P"}
    assert res.added == {"B"}
    assert "P" not in res.labels
    assert res.labels["B"][:2] == [100.0, 100.0]


def test_a_different_letter_or_no_answer_keeps_the_ocr_letter():
    spots = doubtful_spots(LABELS, PHOTOS)
    assert apply_answers(LABELS, spots, {"1": "D"}, ("A", "D")).not_labels == set()
    assert apply_answers(LABELS, spots, {}, ("A", "D")).not_labels == set()
    assert apply_answers(LABELS, spots, {"1": "null"}, ("A", "D")).not_labels == {"P"}


def test_answers_outside_the_figure_letters_or_already_placed_are_ignored():
    spots = doubtful_spots(LABELS, PHOTOS)
    assert apply_answers(LABELS, spots, {"1": "P", "2": "x"}, ("A", "D")).added == set()
    assert apply_answers(LABELS, spots, {"1": "P", "2": "c"}, ("A", "D")).added == set()


def test_parse_answers_tolerates_prose_around_json():
    assert parse_answers('Sure: {"1": "b", "2": null}') == {"1": "b", "2": None}
    assert parse_answers("no json") == {}


def test_select_calls_vlm_only_when_something_is_doubtful():
    calls = []

    def fake(prompt, path):
        calls.append(path)
        return '{"1": null, "2": "B"}'

    img = Image.new("RGB", (400, 400), "white")
    be = CallableVlmBackend(fake)
    res = select_labels_with_vlm(img, LABELS, PHOTOS, None, be)
    assert res is not None and res.added == {"B"} and res.not_labels == {"P"}
    clean = {"A": LABELS["A"], "C": LABELS["C"]}
    assert select_labels_with_vlm(img, clean, [PHOTOS[0], PHOTOS[2]], None, be) is None
    assert len(calls) == 1
