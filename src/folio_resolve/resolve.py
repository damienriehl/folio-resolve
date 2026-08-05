"""Shared label -> IRI resolution: decompose-first, calibrated bar, branch-carrying.

This module centralizes the label-resolution policy so every consumer resolves identically.
It exists because the Ch02 proving run (v6) measured a net-negative precision regression whose
two root causes both live in *how a resolved concept is produced*:

1. **Whole-string acceptance short-circuited decomposition.** A conjoined heading such as
   *"Proposed Findings of Fact and Conclusions of Law"* fuzzy-matched the whole string to a
   single wrong partial (*Proposed Findings of Fact* at 90) instead of resolving its two sibling
   concepts. Fix: for a multi-head string, resolve each conjunct **before** considering the whole
   string.

2. **The acceptance bar was a no-op and the branch was dropped.** The consumer accepted the top
   match at ``score >= 0.6`` — but FOLIO's ``search_by_label`` returns a **0-100** score, so 0.6
   accepted *everything*, and generic terms latched onto the short place/agency labels rapidfuzz
   over-scores to exactly 90 (``law`` -> *Delaware*, ``effect of answers`` -> *Federal Election
   Commission*). The resolved concept also carried an **empty branch**, so the place/agency gates
   (which key on branch) never saw it. Fix: a calibrated whole-string bar on the real 0-100 scale,
   and a :class:`ResolvedConcept` that **always carries its branch** so gates can veto it.

Calibration (from the v6 proving-run score distribution, measured on the live FOLIO catalogue):

* place/agency mis-maps cluster at **exactly 90.0** — rapidfuzz's short-label ceiling
  (``law``/``Presumptions``/``Decision Maker``/``verdict forms`` all -> a place at 90.0).
* every genuine recovery Damien named scores **>= 96.97**: *Burden of Proof* -> Litigation Burdens
  of Proof (100), *Hearing* (100), *Lawyer* (100), *Closing Arguments* -> Closing Arguments /
  Summation (96.97).

``WHOLE_STRING_THRESHOLD = 92.0`` therefore sits above the 90.0 mis-map band and below every named
recovery: it trims the fuzzy place/agency tail while preserving the recalls. The homonym
``Action`` -> *Auction* (92.3) deliberately clears the bar and is stopped by the alias blocklist
instead — a homonym is a deterministic veto, not a scoring accident.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from .decompose import decompose

logger = logging.getLogger(__name__)

# FOLIO search_by_label returns a 0-100 relevance score. See module docstring for calibration.
WHOLE_STRING_THRESHOLD = 92.0
CONJUNCT_THRESHOLD = 92.0

# search_by_label(label) -> [(concept, score_0_100), ...], best first. ``concept`` is duck-typed:
# it must expose ``.iri`` and ``.branch`` and one of ``.preferred_label`` / ``.label``.
SearchByLabel = Callable[[str], list[tuple[object, float]]]


@dataclass(frozen=True)
class ResolvedConcept:
    """A label resolved to a FOLIO concept, always carrying its branch.

    ``surface`` is the (sub)string that actually resolved — the whole string for a single-head
    label, or a single conjunct for a decomposed multi-head string. Gates compare ``surface``
    against ``label`` (the canonical FOLIO label) to tell a genuine place mention (*Delaware* ->
    Delaware) from a mis-map (*law* -> Delaware).
    """

    iri: str
    label: str
    branch: str
    score: float
    surface: str


def _attr(obj: object, *names: str) -> str:
    for name in names:
        val = getattr(obj, name, "")
        if isinstance(val, str) and val:
            return val
    return ""


def _resolve_one(search: SearchByLabel, term: str, threshold: float) -> ResolvedConcept | None:
    try:
        results = search(term)
    except Exception:
        logger.warning("search_by_label failed for term=%r", term, exc_info=True)
        return None
    if not results:
        return None
    # `search_by_label` is a duck-typed consumer callable, so its top row is untrusted input:
    # unpacking and float() sat outside the guard and could raise straight through a resolver
    # that documents itself as returning [] when nothing clears the bar.
    try:
        obj, raw_score = results[0]
        score = float(raw_score)
    except (TypeError, ValueError):
        logger.warning("search_by_label returned an unusable row for term=%r: %r", term, results[0])
        return None
    if score < threshold:
        return None
    iri = _attr(obj, "iri")
    if not iri:
        return None
    return ResolvedConcept(
        iri=iri,
        label=_attr(obj, "preferred_label", "label"),
        branch=_attr(obj, "branch"),
        score=score,
        surface=term,
    )


@dataclass
class LabelResolver:
    """Resolve a label to one or more FOLIO concepts, decompose-first and branch-carrying.

    A single instance wraps a consumer's ``search_by_label`` callable so the resolution policy is
    identical everywhere. ``resolve`` returns an empty list when nothing clears the bar (the label
    is a genuine proposed class, or a fuzzy-only mis-map that was correctly rejected).
    """

    search_by_label: SearchByLabel
    whole_string_threshold: float = WHOLE_STRING_THRESHOLD
    conjunct_threshold: float = CONJUNCT_THRESHOLD

    def resolve(self, label: str) -> list[ResolvedConcept]:
        parts = decompose(label)
        # Decompose-FIRST: a multi-head string (>= 2 co-heads) resolves each conjunct before the
        # whole string is even considered, so a conjoined heading yields one tag per sibling
        # concept rather than one wrong whole-string partial.
        if len(parts) > 1:
            resolved: list[ResolvedConcept] = []
            seen: set[str] = set()
            for part in parts[1:]:  # parts[0] is the original whole string
                rc = _resolve_one(self.search_by_label, part, self.conjunct_threshold)
                if rc is not None and rc.iri not in seen:
                    seen.add(rc.iri)
                    resolved.append(rc)
            if resolved:
                return resolved
        # Single-head, or no conjunct resolved: whole string at the calibrated bar.
        whole = _resolve_one(self.search_by_label, label, self.whole_string_threshold)
        return [whole] if whole is not None else []
