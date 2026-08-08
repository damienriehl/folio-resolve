"""Score calibration — the isotonic fit, the calibrated bands, and the weak-band bounds.

Ch02 finding 004: raw match scores are not probabilities and the 45-60 "weak" band is mis-drawn.
Samples here are synthetic (score, verdict) pairs shaped like recorded per-tag verdicts.
"""

from __future__ import annotations

import pytest

from folio_resolve import CalibrationSample, ScoreCalibration
from folio_resolve.calibration import _isotonic_fit

# -- CalibrationSample ---------------------------------------------------


def test_verdict_targets() -> None:
    assert CalibrationSample(80, "correct").target == 1.0
    assert CalibrationSample(80, "weak").target == 0.5  # partial credit
    assert CalibrationSample(80, "wrong").target == 0.0


def test_an_unknown_verdict_is_rejected_loudly() -> None:
    # Better a KeyError naming the verdict than a silent 0.0 that skews the whole fit.
    sample = CalibrationSample(80, "penalized")
    with pytest.raises(KeyError):
        _ = sample.target


# -- the pool-adjacent-violators fit -------------------------------------


def test_isotonic_fit_leaves_already_monotone_points_alone() -> None:
    assert _isotonic_fit([(10.0, 0.0), (50.0, 0.5), (90.0, 1.0)]) == [
        (10.0, 0.0),
        (50.0, 0.5),
        (90.0, 1.0),
    ]


def test_isotonic_fit_pools_violating_pairs_to_their_mean() -> None:
    # A high-scoring "wrong" next to a low-scoring "correct" is a violation; PAVA pools them.
    steps = _isotonic_fit([(10.0, 1.0), (90.0, 0.0)])
    assert steps == [(90.0, 0.5)]


def test_isotonic_fit_output_is_monotone_nondecreasing() -> None:
    steps = _isotonic_fit([(10.0, 1.0), (20.0, 0.0), (30.0, 1.0), (40.0, 0.0), (50.0, 1.0)])
    probs = [p for _, p in steps]
    assert probs == sorted(probs)


def test_isotonic_fit_of_nothing_is_nothing() -> None:
    assert _isotonic_fit([]) == []


# -- ScoreCalibration.fit / probability ----------------------------------


@pytest.fixture
def cal() -> ScoreCalibration:
    return ScoreCalibration.fit(
        [
            CalibrationSample(30, "wrong"),
            CalibrationSample(50, "wrong"),
            CalibrationSample(60, "weak"),
            CalibrationSample(75, "weak"),
            CalibrationSample(85, "correct"),
            CalibrationSample(95, "correct"),
        ]
    )


def test_fit_is_order_independent() -> None:
    samples = [
        CalibrationSample(85, "correct"),
        CalibrationSample(30, "wrong"),
        CalibrationSample(60, "weak"),
    ]
    forward = ScoreCalibration.fit(samples)
    backward = ScoreCalibration.fit(list(reversed(samples)))
    assert [forward.probability(s) for s in (0, 30, 60, 85, 100)] == [
        backward.probability(s) for s in (0, 30, 60, 85, 100)
    ]


def test_probability_is_monotone_across_the_scale(cal: ScoreCalibration) -> None:
    probs = [cal.probability(s) for s in range(0, 101, 5)]
    assert probs == sorted(probs)
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_probability_is_piecewise_constant_at_the_fitted_steps(cal: ScoreCalibration) -> None:
    assert cal.probability(60) == cal.probability(74)  # inside the 60..75 step
    assert cal.probability(85) == 1.0
    assert cal.probability(1000) == 1.0  # above the top step, clamped by the fit


def test_probability_below_the_lowest_step_uses_that_step(cal: ScoreCalibration) -> None:
    assert cal.probability(-50) == cal.probability(0) == cal.probability(30)


def test_an_unfitted_calibration_falls_back_to_a_linear_prior() -> None:
    cal = ScoreCalibration()
    assert cal.probability(0) == 0.0
    assert cal.probability(50) == 0.5
    assert cal.probability(100) == 1.0
    # ...and clamps outside 0-100 rather than returning a nonsense probability.
    assert cal.probability(-10) == 0.0
    assert cal.probability(250) == 1.0


def test_fitting_no_samples_yields_the_linear_fallback() -> None:
    assert ScoreCalibration.fit([]).probability(50) == 0.5


# -- bands ---------------------------------------------------------------


def test_bands_partition_the_scale(cal: ScoreCalibration) -> None:
    assert cal.band(30) == "wrong"
    assert cal.band(60) == "weak"
    assert cal.band(95) == "strong"


def test_band_thresholds_are_overridable(cal: ScoreCalibration) -> None:
    # Demanding near-certainty for "strong" pushes the 0.5-probability band down to weak.
    assert cal.band(60, strong_at=0.99) == "weak"
    assert cal.band(60, weak_below=0.9) == "wrong"


def test_band_is_consistent_with_probability(cal: ScoreCalibration) -> None:
    for score in range(0, 101, 5):
        p = cal.probability(score)
        expected = "wrong" if p < 0.5 else ("weak" if p < 0.8 else "strong")
        assert cal.band(score) == expected, score


# -- weak-band bounds ----------------------------------------------------


def test_weak_band_bounds_bracket_the_weak_band(cal: ScoreCalibration) -> None:
    low, high = cal.weak_band_bounds()
    assert low <= high
    assert cal.band(low) == "weak"
    assert cal.band(high) == "strong"


def test_unfitted_weak_band_bounds_are_the_raw_threshold_scale() -> None:
    assert ScoreCalibration().weak_band_bounds() == (50.0, 80.0)
    assert ScoreCalibration().weak_band_bounds(weak_below=0.45, strong_at=0.9) == (45.0, 90.0)


def test_weak_band_collapses_when_nothing_reaches_the_weak_threshold() -> None:
    """Regression: an all-``wrong`` fit reported the entire observed range as the weak band.

    The fallback for ``low`` was ``self._steps[0][0]`` — the *lowest* observed score — so a
    calibration in which no score is even weak (``band()`` says "wrong" everywhere) advertised
    a weak band starting at the bottom of the scale. A consumer redrawing its acceptance bar
    from these bounds would have accepted the entire range.
    """
    cal = ScoreCalibration.fit(
        [CalibrationSample(50, "wrong"), CalibrationSample(60, "wrong"), CalibrationSample(70, "wrong")]
    )
    assert cal.band(50) == cal.band(70) == "wrong"
    low, high = cal.weak_band_bounds()
    assert low == high == 70.0  # empty band, pinned at the top of the fitted range
    assert low <= high


def test_weak_band_bounds_never_invert() -> None:
    for samples in (
        [CalibrationSample(50, "correct"), CalibrationSample(90, "correct")],
        [CalibrationSample(50, "weak"), CalibrationSample(90, "weak")],
        [CalibrationSample(50, "wrong"), CalibrationSample(90, "correct")],
        [CalibrationSample(50, "correct"), CalibrationSample(90, "wrong")],
    ):
        low, high = ScoreCalibration.fit(samples).weak_band_bounds()
        assert low <= high, samples


def test_an_all_correct_fit_starts_strong_at_the_first_score() -> None:
    cal = ScoreCalibration.fit([CalibrationSample(60, "correct"), CalibrationSample(90, "correct")])
    assert cal.weak_band_bounds() == (60.0, 60.0)  # nothing is weak; strong from the bottom
    assert cal.band(60) == "strong"


# -- explicit steps ------------------------------------------------------


def test_steps_can_be_supplied_directly() -> None:
    # Consumers persist a fitted curve and rehydrate it without the samples.
    cal = ScoreCalibration([(40.0, 0.1), (70.0, 0.6), (92.0, 0.95)])
    assert cal.band(40) == "wrong"
    assert cal.band(70) == "weak"
    assert cal.band(92) == "strong"
    assert cal.weak_band_bounds() == (70.0, 92.0)
