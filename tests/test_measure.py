"""The statistics of docs/MEASURE-SPEC.md, section 7: Wilson intervals, Newcombe differences,
medians and the printed forms, against the spec's fixed test vectors."""

import math

import pytest

from polarizer import measure

# (k, n, low, high, printed), section 7's first table.
WILSON = [
    (0, 5, 0.000000, 0.434482, "0% (0% to 44%)"),
    (4, 5, 0.375535, 0.963776, "80% (37% to 97%)"),
    (5, 5, 0.565518, 1.000000, "100% (56% to 100%)"),
    (5, 6, 0.436497, 0.969947, "83% (43% to 97%)"),
    (1, 14, 0.012722, 0.314687, "7% (1% to 32%)"),
    (13, 17, 0.527382, 0.904450, "76% (52% to 91%)"),
    (15, 17, 0.656636, 0.967120, "88% (65% to 97%)"),
    (3, 42, 0.024590, 0.190094, "7% (2% to 20%)"),
    (2, 40, 0.013821, 0.165039, "5% (1% to 17%)"),
    (28, 33, 0.690801, 0.933495, "85% (69% to 94%)"),
    (20, 20, 0.838875, 1.000000, "100% (83% to 100%)"),
    (36, 40, 0.769482, 0.960420, "90% (76% to 97%)"),
    (37, 40, 0.801358, 0.974164, "93% (80% to 98%)"),
    (20, 40, 0.351995, 0.648005, "50% (35% to 65%)"),
]

# (k1, n1, k2, n2, low, high, printed, phrase), section 7's second table.
NEWCOMBE = [
    (15, 17, 13, 17, -0.147826, 0.369655, "+12 points (-15 to +37)", measure.NOT_DISTINGUISHABLE),
    (2, 40, 3, 42, -0.145487, 0.102780, "-2 points (-15 to +11)", measure.NOT_DISTINGUISHABLE),
    (19, 20, 10, 20, 0.176274, 0.654871, "+45 points (+17 to +66)", measure.DISTINGUISHABLE),
    (37, 40, 36, 40, -0.112616, 0.164470, "+3 points (-12 to +17)", measure.NOT_DISTINGUISHABLE),
    (20, 40, 38, 40, -0.602363, -0.262545, "-45 points (-61 to -26)", measure.DISTINGUISHABLE),
]


@pytest.mark.parametrize("k, n, low, high, printed", WILSON)
def test_wilson_vectors(k, n, low, high, printed):
    got = measure.wilson(k, n)
    assert abs(got[0] - low) < 1e-6 and abs(got[1] - high) < 1e-6, got
    assert measure.rate_text(k, n) == printed


def test_wilson_edges():
    """n = 0 has no interval; k = 0 and k = n clamp to 0 and 1; the printed interval always
    contains the exact one, for every k and n up to 60."""
    with pytest.raises(ValueError):
        measure.wilson(0, 0)
    assert measure.wilson(0, 7)[0] == 0.0
    assert measure.wilson(7, 7)[1] == 1.0
    for n in range(1, 61):
        for k in range(n + 1):
            low, high = measure.wilson(k, n)
            assert 0.0 <= low <= k / n <= high <= 1.0
            if n < measure.MIN_N:
                continue
            percent, low_p, high_p = measure.rate_parts(k, n)
            assert low_p <= 100 * low + 1e-9 and high_p >= 100 * high - 1e-9
            assert low_p <= percent <= high_p


def test_too_few_to_say():
    """Below 5, the "too few" text with the counts, and no percent; 0 of 0 says so."""
    assert measure.MIN_N == 5
    assert measure.rate_text(3, 4) == "too few to say a rate (5 or more needed)"
    assert "%" not in measure.rate_text(4, 4)
    assert measure.rate_parts(3, 4) is None
    assert measure.rate_parts(0, 0) is None


@pytest.mark.parametrize("k1, n1, k2, n2, low, high, printed, phrase", NEWCOMBE)
def test_newcombe_vectors(k1, n1, k2, n2, low, high, printed, phrase):
    got = measure.newcombe(k1, n1, k2, n2)
    assert abs(got[0] - low) < 1e-6 and abs(got[1] - high) < 1e-6, got
    assert measure.difference_text(k1, n1, k2, n2) == (printed, phrase)


def test_newcombe_too_few():
    assert measure.difference_text(3, 4, 5, 6) == (None, measure.TOO_FEW_TO_COMPARE)
    assert measure.difference_parts(5, 6, 3, 4) is None


def test_points_are_integer_exact():
    """Equal differences print equally: 38/40 - 37/40 and 37/40 - 36/40 are both +3 points,
    which floating point would round differently."""
    assert measure.points(38, 40, 37, 40) == 3
    assert measure.points(37, 40, 36, 40) == 3
    assert measure.points(36, 40, 37, 40) == -3
    # A half rounds away from zero: 1/8 - 0/8 is 12.5 points.
    assert measure.points(1, 8, 0, 8) == 13
    assert measure.points(0, 8, 1, 8) == -13


def test_percent_rounds_half_up():
    assert measure.percent(1, 8) == 13  # 12.5
    assert measure.percent(1, 200) == 1  # 0.5
    assert measure.percent(5, 6) == 83


def test_median_low_and_time_format():
    assert measure.median_low([12400, 9100, 30200, 11000]) == 11000
    assert measure.median_low([0, 5, 10, 20, 30, 41]) == 10
    assert measure.median_low([7]) == 7
    # Drill times: elapsed_ms as seconds to one decimal, a half rounding up.
    assert measure.drill_seconds(12400) == "12.4 s"
    assert measure.drill_seconds(12449) == "12.4 s"
    assert measure.drill_seconds(12450) == "12.5 s"
    assert measure.drill_seconds(0) == "0.0 s"
    assert measure.drill_seconds(59_999) == "60.0 s"
    # Stats times: whole seconds, as holds prints an age.
    assert measure.stats_seconds(59) == "59 s"
    assert measure.stats_seconds(60) == "1m00s"
    assert measure.stats_seconds(3599) == "59m59s"
    assert measure.stats_seconds(3600) == "1h00m"
    assert measure.stats_seconds(0) == "0 s"


def test_z_is_the_normal_quantile():
    """z is the 0.975 quantile of the standard normal, to the precision given."""
    assert math.isclose(0.5 * math.erfc(-measure.Z / math.sqrt(2)), 0.975, abs_tol=1e-12)
