"""Match-score calibration.

Ch02 finding 004 asks for "weak-band recalibration": the raw match scores from the fuzzy/overlap
scorer are not probabilities, and the 45-60 "weak" band is mis-drawn. Per-tag verdicts
(``correct`` / ``weak`` / ``wrong`` at their match scores) form a labeled dataset we can fit a
monotone ``score -> P(correct)`` curve to, and use to redraw the weak/strong band boundaries.

The fit here is a dependency-free pool-adjacent-violators (isotonic) regression, so no numpy /
scikit is required in the core. ``weak`` verdicts count as 0.5 (partial credit); ``correct`` = 1,
``wrong`` = 0.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_VERDICT_TARGET = {"correct": 1.0, "weak": 0.5, "wrong": 0.0}


@dataclass(frozen=True)
class CalibrationSample:
    score: float  # raw match score (0-100)
    verdict: str  # "correct" | "weak" | "wrong"

    @property
    def target(self) -> float:
        return _VERDICT_TARGET[self.verdict]


def _isotonic_fit(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Pool-adjacent-violators: returns monotone-nondecreasing ``(x, y)`` step points."""
    # points sorted by x; pools carry (sum_y, count, x_right)
    pools: list[list[float]] = []  # [sum_y, count, x]
    for x, y in points:
        pools.append([y, 1.0, x])
        while len(pools) >= 2 and pools[-2][0] / pools[-2][1] > pools[-1][0] / pools[-1][1]:
            sy, c, xr = pools.pop()
            pools[-1][0] += sy
            pools[-1][1] += c
            pools[-1][2] = xr
        # sy/c and xr referenced only inside loop; keep ruff happy
    return [(x, sy / c) for sy, c, x in pools]


class ScoreCalibration:
    """Monotone calibration mapping raw match scores to P(correct)."""

    def __init__(self, steps: list[tuple[float, float]] | None = None) -> None:
        # steps: sorted list of (score, probability), monotone nondecreasing in probability
        self._steps = steps or []

    @classmethod
    def fit(cls, samples: Iterable[CalibrationSample]) -> ScoreCalibration:
        pts = sorted(((s.score, s.target) for s in samples), key=lambda p: p[0])
        if not pts:
            return cls([])
        return cls(_isotonic_fit(pts))

    def probability(self, score: float) -> float:
        """P(correct) for a raw score via piecewise-constant interpolation of the fit."""
        if not self._steps:
            # No data: fall back to a linear score/100 prior.
            return max(0.0, min(1.0, score / 100.0))
        # Find the last step whose x <= score.
        prob = self._steps[0][1]
        for x, p in self._steps:
            if x <= score:
                prob = p
            else:
                break
        return prob

    def band(self, score: float, *, weak_below: float = 0.5, strong_at: float = 0.8) -> str:
        """Classify a score into wrong / weak / strong using calibrated probability."""
        p = self.probability(score)
        if p < weak_below:
            return "wrong"
        if p < strong_at:
            return "weak"
        return "strong"

    def weak_band_bounds(self, *, weak_below: float = 0.5, strong_at: float = 0.8) -> tuple[float, float]:
        """The raw-score range that maps to the 'weak' calibrated band, as ``(low, high)``.

        ``low`` is the lowest fitted score whose calibrated probability reaches ``weak_below``;
        ``high`` the lowest that reaches ``strong_at``. When the fit never reaches a threshold
        the band collapses at the top of the fitted range: the previous fallback returned
        ``self._steps[0][0]`` for ``low``, i.e. the *lowest* observed score, which reported the
        entire range as weak precisely when nothing in it was even weak.
        """
        if not self._steps:
            return (weak_below * 100.0, strong_at * 100.0)
        top = self._steps[-1][0]
        low = next((x for x, p in self._steps if p >= weak_below), top)
        high = next((x for x, p in self._steps if p >= strong_at), top)
        return (low, max(low, high))
