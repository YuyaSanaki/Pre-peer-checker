from __future__ import annotations

import random
from fractions import Fraction
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.engine.derived_precision import (
    PATTERN_ID,
    classify_group,
    repeating_fraction,
    warnings_from_derived_precision,
)

SRC = Path("/x/Fig1B/graph_qpcr.csv")


def _vec(values: list[float], key: str = "treated") -> GroupVector:
    return GroupVector(SRC, key, tuple(sorted(values)), len(values))


def test_repeating_fraction_rounded_and_truncated():
    assert repeating_fraction(0.3333) == Fraction(1, 3)
    assert repeating_fraction(0.6667) == Fraction(2, 3)
    assert repeating_fraction(0.6666) == Fraction(2, 3)
    assert repeating_fraction(0.142857) == Fraction(1, 7)
    assert repeating_fraction(1 / 3) == Fraction(1, 3)
    assert repeating_fraction(8 / 7) == Fraction(8, 7)


def test_repeating_fraction_ignores_short_or_terminating():
    assert repeating_fraction(0.33) is None  # already rounded
    assert repeating_fraction(0.125) is None  # 1/8 terminates
    assert repeating_fraction(0.3125) is None  # 5/16 terminates
    assert repeating_fraction(2.0) is None


def test_random_four_decimal_measurements_rarely_group_flagged():
    rng = random.Random(0)
    flagged = 0
    for _ in range(500):
        vals = [round(rng.uniform(0.1, 3.0), 4) for _ in range(6)]
        if classify_group(_vec(vals)) is not None:
            flagged += 1
    assert flagged <= 5


def test_float_noise_on_short_decimals_is_not_a_fraction():
    assert repeating_fraction(886918.1541099997) is None
    assert repeating_fraction(65400.96390000001) is None
    f = classify_group(
        _vec([886918.1541099997, 995020.3620299997, 1008144.0268700001, 65400.96390000001])
    )
    assert f is None or f.kind != "repeating_fraction"


def test_full_double_ratios_of_counts():
    assert repeating_fraction(0.43548387096774194) == Fraction(27, 62)
    assert repeating_fraction(0.4666666666666667) == Fraction(7, 15)


def test_plain_measurements_not_flagged():
    assert classify_group(_vec([1.21, 0.98, 1.05, 0.87, 1.12], "ctrl")) is None
    assert classify_group(_vec([1234.567, 1180.221, 1302.998, 1250.004])) is None


def test_excess_precision_qpcr_like_values():
    f = classify_group(_vec([0.873452, 1.204518, 0.955301, 1.087264]))
    assert f is not None and f.kind == "excess_precision"


def test_warning_without_disclosure_suggests_methods_or_rounding():
    vec = _vec([0.3333, 0.6667, 1.3333, 0.1429, 0.5714])
    warns = warnings_from_derived_precision([vec], ["Cells were imaged at 20x."])
    assert len(warns) == 1
    w = warns[0]
    assert w.metadata["pattern_id"] == PATTERN_ID
    assert w.metadata["kinds"] == ["repeating_fraction"]
    assert not w.is_demoted()
    assert "Methods" in w.reason and "丸め" in w.reason
    assert "1/3" in w.reason


def test_disclosed_ratio_is_demoted():
    vec = _vec([0.3333, 0.6667, 1.3333, 0.1429, 0.5714])
    texts = ["Methods. Expression was normalized to Rpl32 using the 2^-ΔΔCt method."]
    warns = warnings_from_derived_precision([vec], texts)
    assert len(warns) == 1
    w = warns[0]
    assert w.is_demoted()
    assert w.metadata["methods_disclosed"] is True
    assert "normalized to" in w.metadata["disclosure_span"]


def test_one_warning_per_source_table():
    a = _vec([0.3333, 0.6667, 1.3333, 0.1429], "g1")
    b = _vec([0.2857, 0.4286, 0.8571, 1.1429], "g2")
    warns = warnings_from_derived_precision([a, b], [])
    assert len(warns) == 1
    assert {g["group"] for g in warns[0].metadata["groups"]} == {"g1", "g2"}


def test_non_plot_tables_skipped_by_default():
    raw = GroupVector(Path("/x/raw/measurements.csv"), "t", (0.3333, 0.6667, 1.3333, 0.1429), 4)
    assert warnings_from_derived_precision([raw], []) == []
