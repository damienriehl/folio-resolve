"""Ontology provider seam.

The matching engines were coupled to ``folio-python`` in the source repos. Here that coupling
becomes a ``Protocol`` so the pure-Python core and its tests never require the (heavy) live
ontology. ``InMemoryOntology`` backs the tests; ``FolioPythonProvider`` is the optional adapter
that wraps the real ``folio-python`` package (install the ``folio`` extra).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from .scoring import compute_relevance_score, content_words

_PROVIDER_SEARCH_CACHE_SIZE = 256
_LABEL_SEARCH_RECALL_MULTIPLIER = 5
_LABEL_SEARCH_RECALL_MINIMUM = 50
_LABEL_SEARCH_RECALL_MAXIMUM = 200
_CacheKey = TypeVar("_CacheKey")
_CacheValue = TypeVar("_CacheValue")


@dataclass(frozen=True)
class Concept:
    """A single ontology concept, normalized across providers."""

    iri: str
    label: str
    definition: str | None = None
    alternative_labels: tuple[str, ...] = ()
    preferred_label: str | None = None
    branch: str = ""
    parent_iris: tuple[str, ...] = ()


def _score_label_candidates(
    query: str, concepts: Iterable[Concept], limit: int
) -> list[tuple[Concept, float]]:
    """Score normalized label candidates with the provider-independent policy."""
    query_content = content_words(query)
    scored: list[tuple[Concept, float]] = []
    for concept in concepts:
        score = compute_relevance_score(
            query_content,
            query,
            concept.label,
            definition=concept.definition,
            synonyms=list(concept.alternative_labels),
            preferred_label=concept.preferred_label,
        )
        if score > 0:
            scored.append((concept, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0].iri))
    return scored[:limit]


@dataclass(frozen=True)
class LabelInfo:
    """A label -> concept association, tagged with whether it is the preferred label.

    Mirrors folio-enrich's ``LabelInfo``; the entity-ruler pattern builder consumes it.
    """

    concept: Concept
    label_type: str  # "preferred" | "alternative"


@runtime_checkable
class OntologyProvider(Protocol):
    """The minimal ontology surface the matching engines need."""

    def all_labels(self) -> dict[str, LabelInfo]:
        """All labels (lowercased) -> LabelInfo, for entity-ruler pattern building."""
        ...

    def search_by_label(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        """Fuzzy/overlap label search returning ``(concept, score_0_100)`` pairs, best first."""
        ...

    def get_concept(self, iri: str) -> Concept | None:
        """Resolve a concept by IRI, or None."""
        ...


@runtime_checkable
class RecallOntology(OntologyProvider, Protocol):
    """Ontology capabilities required by multi-strategy recall.

    Implementations return deterministic IRI order for unscored ties and deterministic IRI
    order within equal-score groups.  A provider whose underlying API applies ``limit`` before
    returning results cannot guarantee deterministic membership at that boundary; adapters can
    only normalize the batch they receive.
    """

    def search_by_prefix(self, prefix: str, *, limit: int = 50) -> list[Concept]:
        """Return concepts whose label, preferred label, or alternative starts with prefix."""
        ...

    def search_by_definition(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        """Search definition text, returning best matches first on a 0-100 scale."""
        ...

    def parents_of(self, iri: str) -> list[Concept]:
        """Return the immediate ontology parents of ``iri``."""
        ...


class InMemoryOntology:
    """A dependency-free ontology backed by a list of concepts.

    Uses the ported word-overlap scorer for ``search_by_label`` so tests exercise real scoring
    behavior without ``folio-python``.
    """

    def __init__(self, concepts: list[Concept]) -> None:
        self._concepts = list(concepts)
        self._by_iri = {c.iri: c for c in self._concepts}

    def all_labels(self) -> dict[str, LabelInfo]:
        out: dict[str, LabelInfo] = {}
        for c in self._concepts:
            # Skip blank labels, as FolioPythonProvider does: a concept with no label would
            # otherwise register an empty-string key that no consumer can match on and that
            # every later blank-labelled concept would overwrite.
            label = c.preferred_label or c.label
            if label:
                out[label.lower()] = LabelInfo(concept=c, label_type="preferred")
            for alt in c.alternative_labels:
                if alt:
                    out.setdefault(alt.lower(), LabelInfo(concept=c, label_type="alternative"))
        return out

    def search_by_label(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        return _score_label_candidates(query, self._concepts, limit)

    def get_concept(self, iri: str) -> Concept | None:
        return self._by_iri.get(iri)

    def search_by_prefix(self, prefix: str, *, limit: int = 50) -> list[Concept]:
        normalized = prefix.casefold().strip()
        if not normalized:
            return []
        matches = [
            concept
            for concept in self._concepts
            if any(
                label.casefold().startswith(normalized)
                for label in (
                    concept.label,
                    concept.preferred_label,
                    *concept.alternative_labels,
                )
                if label
            )
        ]
        return sorted(matches, key=lambda concept: concept.iri)[:limit]

    def search_by_definition(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        query_content = content_words(query)
        scored = [
            (
                concept,
                compute_relevance_score(
                    query_content,
                    query,
                    concept.definition,
                ),
            )
            for concept in self._concepts
            if concept.definition
        ]
        return sorted(
            ((concept, score) for concept, score in scored if score > 0),
            key=lambda pair: (-pair[1], pair[0].iri),
        )[:limit]

    def parents_of(self, iri: str) -> list[Concept]:
        concept = self.get_concept(iri)
        if concept is None:
            return []
        return sorted(
            (
                parent
                for parent_iri in concept.parent_iris
                if (parent := self.get_concept(parent_iri))
            ),
            key=lambda parent: parent.iri,
        )


@dataclass
class FolioPythonProvider:
    """Optional adapter over the ``folio-python`` package.

    Install with the ``folio`` extra. Imports are deferred so the core stays dependency-light.
    """

    _folio: Any = field(default=None)
    _label_search_cache: OrderedDict[
        tuple[str, int], tuple[tuple[Concept, float], ...]
    ] = field(default_factory=OrderedDict, init=False, repr=False, compare=False)
    _definition_search_cache: OrderedDict[
        tuple[str, int], tuple[tuple[Concept, float], ...]
    ] = field(default_factory=OrderedDict, init=False, repr=False, compare=False)
    _prefix_search_cache: OrderedDict[tuple[str, int], tuple[Concept, ...]] = field(
        default_factory=OrderedDict, init=False, repr=False, compare=False
    )

    def _get(self) -> Any:
        if self._folio is None:
            from folio import FOLIO

            self._folio = FOLIO()
        return self._folio

    def all_labels(self) -> dict[str, LabelInfo]:
        folio = self._get()
        out: dict[str, LabelInfo] = {}
        for owl in getattr(folio, "classes", []):
            iri = getattr(owl, "iri", "") or ""
            if "folio.openlegalstandard.org" not in iri:
                continue
            concept = _owl_to_concept(owl)
            pref = concept.preferred_label or concept.label
            if pref:
                out[pref.lower()] = LabelInfo(concept=concept, label_type="preferred")
            for alt in concept.alternative_labels:
                out.setdefault(alt.lower(), LabelInfo(concept=concept, label_type="alternative"))
        return out

    def search_by_label(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        """Use folio-python for recall, then apply the library's label scorer.

        The upstream window is ``max(limit, min(max(limit * 5, 50), 200))``: extra recall
        widening is capped at 200 rows, but the window is never smaller than the caller's limit.
        Only candidates inside that window are re-scored, so later upstream rows remain unavailable.
        """
        cache_key = (query, limit)
        if (cached := _cache_get(self._label_search_cache, cache_key)) is not None:
            return list(cached)

        folio = self._get()
        try:
            upstream_limit = max(
                limit,
                min(
                    max(limit * _LABEL_SEARCH_RECALL_MULTIPLIER, _LABEL_SEARCH_RECALL_MINIMUM),
                    _LABEL_SEARCH_RECALL_MAXIMUM,
                ),
            )
            results = folio.search_by_label(query, limit=upstream_limit)
            concepts = (
                _owl_to_concept(item[0] if isinstance(item, tuple) else item)
                for item in results[:upstream_limit]
            )
            normalized = tuple(_score_label_candidates(query, concepts, limit))
            _cache_put(self._label_search_cache, cache_key, normalized)
            return list(normalized)
        finally:
            _release_upstream_search_caches(folio)

    def get_concept(self, iri: str) -> Concept | None:
        folio = self._get()
        owl = folio[iri] if iri in folio else None  # noqa: SIM401 (folio-python has no .get)
        return _owl_to_concept(owl) if owl is not None else None

    def search_by_prefix(self, prefix: str, *, limit: int = 50) -> list[Concept]:
        cache_key = (prefix, limit)
        if (cached := _cache_get(self._prefix_search_cache, cache_key)) is not None:
            return list(cached)

        folio = self._get()
        try:
            results = folio.search_by_prefix(prefix)
            concepts = (_owl_to_concept(item) for item in results)
            normalized = tuple(sorted(concepts, key=lambda concept: concept.iri)[:limit])
            _cache_put(self._prefix_search_cache, cache_key, normalized)
            return list(normalized)
        finally:
            _release_upstream_search_caches(folio)

    def search_by_definition(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        """Normalize returned score ties; folio-python owns pre-return limit membership.

        Scores are folio-python's definition-search scores, not the library's label scores.
        """
        cache_key = (query, limit)
        if (cached := _cache_get(self._definition_search_cache, cache_key)) is not None:
            return list(cached)

        folio = self._get()
        try:
            results = folio.search_by_definition(query, limit=limit)
            out: list[tuple[Concept, float]] = []
            for item in results[:limit]:
                owl, score = item if isinstance(item, tuple) else (item, 0.0)
                out.append((_owl_to_concept(owl), float(score)))
            normalized = tuple(sorted(out, key=lambda pair: (-pair[1], pair[0].iri)))
            _cache_put(self._definition_search_cache, cache_key, normalized)
            return list(normalized)
        finally:
            _release_upstream_search_caches(folio)

    def parents_of(self, iri: str) -> list[Concept]:
        concept = self.get_concept(iri)
        if concept is None:
            return []
        return sorted(
            (
                parent
                for parent_iri in concept.parent_iris
                if (parent := self.get_concept(parent_iri))
            ),
            key=lambda parent: parent.iri,
        )


def _cache_get(
    cache: OrderedDict[_CacheKey, _CacheValue], key: _CacheKey
) -> _CacheValue | None:
    try:
        value = cache.pop(key)
    except KeyError:
        return None
    cache[key] = value
    return value


def _cache_put(
    cache: OrderedDict[_CacheKey, _CacheValue], key: _CacheKey, value: _CacheValue
) -> None:
    cache[key] = value
    cache.move_to_end(key)
    if len(cache) > _PROVIDER_SEARCH_CACHE_SIZE:
        cache.popitem(last=False)


def _release_upstream_search_caches(folio: object) -> None:
    """Drop folio-python's unbounded inner caches after results have been copied.

    MultiStrategyRecall owns bounded cross-call reuse.  These private caches otherwise retain
    every unique synthetic query and its full search corpus for the lifetime of the process.
    Attribute probing keeps the adapter compatible with folio-python releases that do not expose
    one or more of the affected caches.
    """
    basic_search = getattr(folio, "_basic_search", None)
    cache_clear = getattr(basic_search, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()

    for cache_name in ("_prefix_cache", "_ci_prefix_cache"):
        clear = getattr(getattr(folio, cache_name, None), "clear", None)
        if callable(clear):
            clear()


def _owl_to_concept(owl: object) -> Concept:
    def _s(attr: str) -> str:
        val = getattr(owl, attr, "") or ""
        return val if isinstance(val, str) else ""

    def _list(attr: str) -> tuple[str, ...]:
        val = getattr(owl, attr, None) or []
        return tuple(v for v in val if isinstance(v, str))

    return Concept(
        iri=_s("iri"),
        label=_s("label") or _s("preferred_label"),
        definition=_s("definition") or None,
        alternative_labels=_list("alternative_labels"),
        preferred_label=_s("preferred_label") or None,
        branch=_s("branch"),
        parent_iris=_list("parent_iris") or _list("sub_class_of"),
    )
