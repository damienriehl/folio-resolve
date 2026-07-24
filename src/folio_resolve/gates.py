"""Match gates — deterministic guards that demote pathological candidates.

Two Ch02 failures share a root cause: the rapidfuzz label matcher pathologically over-scores
short country/place labels. ``search_concepts("Presumptions")`` returned *Northern Mariana
Islands, Portugal, Spain, Puerto Rico, Réunion* all at 90 — above the genuinely relevant
*Presumption of Innocence* at 86. That single defect explains both:

* finding 003 — "Slovenia" in a heading propagating to 99 units, and
* the recall noise that buries real concepts under place-name hits.

``PlaceNameGate`` demotes geographic concepts unless corroborated. ``ShortLabelGate`` demotes
matches on very short / single-content-word labels unless the evidence is near-exact. Both are
pure functions over a candidate; they return an adjusted score and a reason.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .scoring import content_words

# Branch substrings that mark a concept the place-name gate governs: geographic places AND
# governmental bodies/agencies. Both share the Ch02 failure mode — rapidfuzz over-scores their
# short proper-noun labels, so generic terms mis-map to them ("law" -> Delaware [Location],
# "effect of answers" -> Federal Election Commission [Governmental Body]). Keying the gate on
# these branches is what lets it veto the whole class once the resolved tag carries its branch.
_PLACE_BRANCH_MARKERS = (
    "location",
    "geograph",
    "country",
    "jurisdiction",
    "place",
    "governmental",
    "agency",
)

# A curated set of place-name tokens that notoriously over-score. Extend from verdict data.
_PLACE_NAME_TOKENS = frozenset(
    {
        "slovenia", "portugal", "spain", "reunion", "puerto", "rico", "mariana",
        "islands", "guam", "samoa", "chad", "mali", "togo", "fiji", "oman",
        "qatar", "peru", "cuba", "chile", "kenya", "ghana", "nepal",
    }
)

# Score floor a demoted candidate is pushed to (below the typical weak-band).
_DEMOTED_SCORE = 40.0


@dataclass(frozen=True)
class GateDecision:
    score: float
    demoted: bool
    reason: str


def _is_place_concept(
    label: str,
    branch: str,
    *,
    extra_tokens: frozenset[str] = frozenset(),
    extra_markers: tuple[str, ...] = (),
    extra_branch_markers: tuple[str, ...] = (),
) -> bool:
    branch_l = branch.lower()
    if any(marker in branch_l for marker in _PLACE_BRANCH_MARKERS):
        return True
    if extra_branch_markers and branch_l.strip() and any(m in branch_l for m in extra_branch_markers):
        return True
    label_tokens = {t.lower() for t in label.split()}
    if label_tokens & _PLACE_NAME_TOKENS:
        return True
    if not extra_tokens and not extra_markers:
        return False
    label_l = label.strip().lower()
    if extra_tokens and (label_tokens & extra_tokens or label_l in extra_tokens):
        return True
    return bool(extra_markers) and any(marker in label_l for marker in extra_markers)


def _normalized(values: Iterable[str] | None) -> tuple[str, ...]:
    """Lowercase/strip a caller-supplied token or marker set, dropping blanks and non-strings."""
    if not values:
        return ()
    return tuple(
        sorted({v.strip().lower() for v in values if isinstance(v, str) and v.strip()})
    )


class PlaceNameGate:
    """Demote place-name candidates unless corroborated by ≥ ``min_signals`` signals.

    The built-in place vocabulary is deliberately small (the tokens Ch02 recorded). Consumers
    that have their *own* recorded over-scoring places extend it at construction time instead
    of carrying a parallel local backstop — the fork this library exists to prevent:

    ``extra_tokens``
        Extra place names. Matched against the label's whitespace-split tokens, and (so
        multi-word names work) against the whole normalized label — ``{"macedonia", "rize",
        "north america"}``.
    ``extra_markers``
        Substring phrase markers matched against the whole normalized label, for productive
        place *phrasings* rather than names — ``("city of", "republic of", "province of")``
        catches *City of Exampleton* whatever the city is called.
    ``extra_branch_markers``
        Extra branch substrings governed by the gate, for ontologies whose branch vocabulary
        differs from FOLIO's.

    All three default to empty, so an un-parameterized ``PlaceNameGate()`` behaves exactly as
    it did before v0.3.0. Matching is case-insensitive; blanks and non-strings are dropped.
    """

    def __init__(
        self,
        min_signals: int = 2,
        demoted_score: float = _DEMOTED_SCORE,
        *,
        extra_tokens: Iterable[str] | None = None,
        extra_markers: Iterable[str] | None = None,
        extra_branch_markers: Iterable[str] | None = None,
    ) -> None:
        self._min_signals = min_signals
        self._demoted_score = demoted_score
        self._extra_tokens = frozenset(_normalized(extra_tokens))
        self._extra_markers = _normalized(extra_markers)
        self._extra_branch_markers = _normalized(extra_branch_markers)

    @property
    def place_tokens(self) -> frozenset[str]:
        """The full place-token vocabulary this gate matches on (built-in + ``extra_tokens``)."""
        return _PLACE_NAME_TOKENS | self._extra_tokens

    def evaluate(
        self,
        *,
        query: str,
        label: str,
        branch: str,
        score: float,
        heading_context_match: bool = False,
        corroborating_signals: int = 1,
    ) -> GateDecision:
        if not _is_place_concept(
            label,
            branch,
            extra_tokens=self._extra_tokens,
            extra_markers=self._extra_markers,
            extra_branch_markers=self._extra_branch_markers,
        ):
            return GateDecision(score=score, demoted=False, reason="not-a-place")

        # An exact label match to the query is always allowed — the place is really named.
        if query.strip().lower() == label.strip().lower():
            return GateDecision(score=score, demoted=False, reason="exact-place-name")

        signals = corroborating_signals + (1 if heading_context_match else 0)
        if signals >= self._min_signals:
            return GateDecision(score=score, demoted=False, reason="corroborated-place")

        return GateDecision(
            score=min(score, self._demoted_score),
            demoted=True,
            reason=f"place-name demoted (signals={signals} < {self._min_signals})",
        )


class ShortLabelGate:
    """Demote fuzzy hits on very short or single-content-word labels."""

    def __init__(
        self, min_chars: int = 4, near_exact_threshold: float = 95.0, demoted_score: float = _DEMOTED_SCORE
    ) -> None:
        self._min_chars = min_chars
        self._near_exact = near_exact_threshold
        self._demoted_score = demoted_score

    def evaluate(self, *, query: str, label: str, score: float) -> GateDecision:
        short_by_chars = len(label.strip()) < self._min_chars
        single_content = len(content_words(label)) <= 1
        if not (short_by_chars or single_content):
            return GateDecision(score=score, demoted=False, reason="not-short")

        # Allow if the evidence is near-exact (real, specific match) or an exact string equality.
        if score >= self._near_exact or query.strip().lower() == label.strip().lower():
            return GateDecision(score=score, demoted=False, reason="near-exact-short-label")

        return GateDecision(
            score=min(score, self._demoted_score),
            demoted=True,
            reason="short-label demoted (fuzzy match below near-exact)",
        )
