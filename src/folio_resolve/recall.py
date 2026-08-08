"""Deterministic multi-strategy candidate recall."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TypeVar

from .ontology import Concept, RecallOntology
from .scoring import (
    LEGAL_TERM_EXPANSIONS,
    compute_relevance_score,
    content_words,
    generate_search_terms,
    tokenize,
)

_K = TypeVar("_K")
_V = TypeVar("_V")


@dataclass(frozen=True)
class RecallResult:
    """A recalled ontology concept and its relevance score on the 0-100 scale."""

    concept: Concept
    score: float


@dataclass
class MultiStrategyRecall:
    """Gather, rescore, and hierarchically expand ontology candidates."""

    ontology: RecallOntology
    top_n: int = 10
    threshold: float = 30.0
    ancestor_depth: int = 3
    ancestor_decay: float = 0.85
    search_cache_capacity: int = 256
    score_cache_capacity: int = 4096
    _label_cache: OrderedDict[tuple[str, int], tuple[tuple[Concept, float], ...]] = field(
        default_factory=OrderedDict, init=False, repr=False, compare=False
    )
    _prefix_cache: OrderedDict[tuple[str, int], tuple[Concept, ...]] = field(
        default_factory=OrderedDict, init=False, repr=False, compare=False
    )
    _definition_cache: OrderedDict[tuple[str, int], tuple[tuple[Concept, float], ...]] = field(
        default_factory=OrderedDict, init=False, repr=False, compare=False
    )
    _score_cache: OrderedDict[
        tuple[frozenset[str], str, str, str | None, tuple[str, ...], str | None], float
    ] = field(default_factory=OrderedDict, init=False, repr=False, compare=False)
    _cache_ontology: RecallOntology | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def recall(self, text: str) -> list[RecallResult]:
        self._ensure_cache_ontology()
        query_content = content_words(text) or set(tokenize(text))
        if not query_content:
            return []

        raw: dict[str, Concept] = {}
        search_terms = sorted(
            generate_search_terms(text),
            key=lambda term: (term.casefold() != text.casefold(), term.casefold()),
        )
        for term in search_terms:
            label_batch = sorted(
                self._search_by_label(term, limit=25),
                key=lambda pair: pair[0].iri,
            )
            for concept, _score in label_batch:
                raw.setdefault(concept.iri, concept)
            if len(term) >= 3:
                prefix_batch = sorted(
                    self._search_by_prefix(term, limit=50), key=lambda concept: concept.iri
                )
                for concept in prefix_batch:
                    raw.setdefault(concept.iri, concept)

        for word in sorted(query_content):
            if len(word) >= 6:
                prefix_batch = sorted(
                    self._search_by_prefix(word[:-2], limit=50),
                    key=lambda concept: concept.iri,
                )
                for concept in prefix_batch:
                    raw.setdefault(concept.iri, concept)

        definition_terms = [text]
        content_phrase = " ".join(sorted(query_content))
        if content_phrase.casefold() != text.casefold():
            definition_terms.append(content_phrase)
        for term in definition_terms:
            if len(term) >= 3:
                definition_batch = sorted(
                    self._search_by_definition(term, limit=20),
                    key=lambda pair: pair[0].iri,
                )
                for concept, _score in definition_batch:
                    raw.setdefault(concept.iri, concept)

        scores: dict[str, float] = {}
        for iri, concept in raw.items():
            score = self._score(query_content, text, concept)
            if score >= self.threshold:
                scores[iri] = score

        expansion_queries = [
            (content_words(expanded), expanded)
            for word in sorted(query_content)
            for suffix in LEGAL_TERM_EXPANSIONS.get(word, ())
            if (expanded := f"{word} {suffix}")
        ]
        for iri, concept in raw.items():
            for expanded_content, expanded in expansion_queries:
                score = self._score(expanded_content, expanded, concept)
                if score >= self.threshold and score > scores.get(iri, 0.0):
                    scores[iri] = score

        direct_scores = dict(scores)
        for iri, direct_score in sorted(direct_scores.items()):
            if direct_score < 50.0:
                continue
            frontier = [raw[iri]]
            visited = {iri}
            for depth in range(1, self.ancestor_depth + 1):
                next_frontier: list[Concept] = []
                ancestor_score = round(direct_score * (self.ancestor_decay**depth), 1)
                for child in frontier:
                    for parent in self.ontology.parents_of(child.iri):
                        if parent.iri in visited:
                            continue
                        visited.add(parent.iri)
                        next_frontier.append(parent)
                        raw.setdefault(parent.iri, parent)
                        if ancestor_score >= self.threshold:
                            scores[parent.iri] = max(scores.get(parent.iri, 0.0), ancestor_score)
                frontier = sorted(next_frontier, key=lambda concept: concept.iri)
                if not frontier:
                    break

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [RecallResult(raw[iri], score) for iri, score in ranked[: self.top_n]]

    def clear_caches(self) -> None:
        """Discard cached searches and scores after an in-place ontology refresh.

        Replacing :attr:`ontology` is detected automatically on the next public
        :meth:`recall` call. Providers refreshed in place must call this method so
        subsequent recall uses the provider's new state.
        """
        self._label_cache.clear()
        self._prefix_cache.clear()
        self._definition_cache.clear()
        self._score_cache.clear()
        self._cache_ontology = self.ontology

    def _ensure_cache_ontology(self) -> None:
        if self._cache_ontology is not self.ontology:
            self.clear_caches()

    def _search_by_label(self, query: str, *, limit: int) -> list[tuple[Concept, float]]:
        key = (query, limit)
        cached = self._label_cache.get(key)
        if cached is None:
            cached = tuple(self.ontology.search_by_label(query, limit=limit))
            self._store(self._label_cache, key, cached)
        else:
            self._label_cache.move_to_end(key)
        return list(cached)

    def _search_by_prefix(self, query: str, *, limit: int) -> list[Concept]:
        key = (query, limit)
        cached = self._prefix_cache.get(key)
        if cached is None:
            cached = tuple(self.ontology.search_by_prefix(query, limit=limit))
            self._store(self._prefix_cache, key, cached)
        else:
            self._prefix_cache.move_to_end(key)
        return list(cached)

    def _search_by_definition(self, query: str, *, limit: int) -> list[tuple[Concept, float]]:
        key = (query, limit)
        cached = self._definition_cache.get(key)
        if cached is None:
            cached = tuple(self.ontology.search_by_definition(query, limit=limit))
            self._store(self._definition_cache, key, cached)
        else:
            self._definition_cache.move_to_end(key)
        return list(cached)

    def _store(self, cache: OrderedDict[_K, _V], key: _K, value: _V) -> None:
        if self.search_cache_capacity <= 0:
            return
        cache[key] = value
        cache.move_to_end(key)
        if len(cache) > self.search_cache_capacity:
            cache.popitem(last=False)

    def _score(self, query_content: set[str], query: str, concept: Concept) -> float:
        key = (
            frozenset(query_content),
            query,
            concept.label,
            concept.definition,
            concept.alternative_labels,
            concept.preferred_label,
        )
        cached = self._score_cache.get(key)
        if cached is not None:
            self._score_cache.move_to_end(key)
            return cached

        score = compute_relevance_score(
            query_content,
            query,
            concept.label,
            definition=concept.definition,
            synonyms=list(concept.alternative_labels),
            preferred_label=concept.preferred_label,
        )
        if self.score_cache_capacity > 0:
            self._score_cache[key] = score
            self._score_cache.move_to_end(key)
            if len(self._score_cache) > self.score_cache_capacity:
                self._score_cache.popitem(last=False)
        return score
