"""Ontology provider seam.

The matching engines were coupled to ``folio-python`` in the source repos. Here that coupling
becomes a ``Protocol`` so the pure-Python core and its tests never require the (heavy) live
ontology. ``InMemoryOntology`` backs the tests; ``FolioPythonProvider`` is the optional adapter
that wraps the real ``folio-python`` package (install the ``folio`` extra).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .scoring import compute_relevance_score, content_words


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
            label = c.preferred_label or c.label
            out[label.lower()] = LabelInfo(concept=c, label_type="preferred")
            for alt in c.alternative_labels:
                out.setdefault(alt.lower(), LabelInfo(concept=c, label_type="alternative"))
        return out

    def search_by_label(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        qc = content_words(query)
        scored: list[tuple[Concept, float]] = []
        for c in self._concepts:
            score = compute_relevance_score(
                qc,
                query,
                c.label,
                definition=c.definition,
                synonyms=list(c.alternative_labels),
                preferred_label=c.preferred_label,
            )
            if score > 0:
                scored.append((c, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0].iri))
        return scored[:limit]

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
        """Normalize returned score ties; folio-python owns pre-return limit membership."""
        folio = self._get()
        results = folio.search_by_label(query, limit=limit)
        out: list[tuple[Concept, float]] = []
        for item in results[:limit]:
            owl, score = item if isinstance(item, tuple) else (item, 0.0)
            out.append((_owl_to_concept(owl), float(score)))
        return sorted(out, key=lambda pair: (-pair[1], pair[0].iri))

    def get_concept(self, iri: str) -> Concept | None:
        folio = self._get()
        owl = folio[iri] if iri in folio else None  # noqa: SIM401 (folio-python has no .get)
        return _owl_to_concept(owl) if owl is not None else None

    def search_by_prefix(self, prefix: str, *, limit: int = 50) -> list[Concept]:
        results = self._get().search_by_prefix(prefix)
        concepts = (_owl_to_concept(item) for item in results)
        return sorted(concepts, key=lambda concept: concept.iri)[:limit]

    def search_by_definition(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        """Normalize returned score ties; folio-python owns pre-return limit membership."""
        results = self._get().search_by_definition(query, limit=limit)
        out: list[tuple[Concept, float]] = []
        for item in results[:limit]:
            owl, score = item if isinstance(item, tuple) else (item, 0.0)
            out.append((_owl_to_concept(owl), float(score)))
        return sorted(out, key=lambda pair: (-pair[1], pair[0].iri))

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
