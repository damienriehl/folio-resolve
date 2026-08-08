from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar
from unittest.mock import patch

from folio_resolve import (
    Concept,
    FolioPythonProvider,
    InMemoryOntology,
    MatchPipeline,
    MultiStrategyRecall,
    RecallOntology,
)


class _DefinitionOnlyOntology(InMemoryOntology):
    """Synthetic provider where one concept is reachable only through definition search."""

    def search_by_label(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        return [
            pair
            for pair in super().search_by_label(query, limit=limit)
            if pair[0].iri != "definition-only"
        ]


def _ontology() -> InMemoryOntology:
    return _DefinitionOnlyOntology(
        [
            Concept(iri="root", label="Civil Remedies", branch="Remedies"),
            Concept(
                iri="direct",
                label="Contract Damages",
                definition="Money awarded for breach of an agreement",
                branch="Remedies",
                parent_iris=("root",),
            ),
            Concept(
                iri="definition-only",
                label="Expectation Interest",
                definition="Compensation for benefit of the bargain after contract breach",
                branch="Remedies",
                parent_iris=("root",),
            ),
            Concept(iri="prefix", label="Contractual Capacity", branch="Contracts"),
        ]
    )


def test_definition_strategy_surfaces_candidate_absent_from_plain_label_search() -> None:
    ontology = _ontology()
    assert all(c.iri != "definition-only" for c, _ in ontology.search_by_label("benefit bargain"))

    results = MultiStrategyRecall(ontology, threshold=30.0).recall("benefit bargain")

    assert any(result.concept.iri == "definition-only" for result in results)


def test_parent_is_added_below_higher_scoring_direct_hit() -> None:
    results = MultiStrategyRecall(_ontology(), threshold=30.0).recall("Contract Damages")

    iris = [result.concept.iri for result in results]
    assert iris.index("direct") < iris.index("root")
    direct = next(result for result in results if result.concept.iri == "direct")
    parent = next(result for result in results if result.concept.iri == "root")
    assert parent.score == round(direct.score * 0.85, 1)


def test_ancestor_expansion_handles_multiple_parents_and_cycles_once() -> None:
    ontology = InMemoryOntology(
        [
            Concept(
                iri="child",
                label="Contract Damages",
                parent_iris=("parent-b", "parent-a"),
            ),
            Concept(iri="parent-a", label="Remedies A", parent_iris=("child",)),
            Concept(iri="parent-b", label="Remedies B", parent_iris=("root",)),
            Concept(iri="root", label="Civil Remedies"),
        ]
    )

    results = MultiStrategyRecall(ontology, threshold=30.0, top_n=10).recall("Contract Damages")

    assert [result.concept.iri for result in results] == [
        "child",
        "parent-a",
        "parent-b",
        "root",
    ]


def test_recall_order_and_tie_breaking_are_deterministic() -> None:
    concepts = [
        Concept(iri="z", label="Agreement Remedy"),
        Concept(iri="a", label="Agreement Remedy"),
    ]
    first = MultiStrategyRecall(InMemoryOntology(concepts)).recall("agreement remedy")
    second = MultiStrategyRecall(InMemoryOntology(list(reversed(concepts)))).recall(
        "agreement remedy"
    )

    assert [(r.concept.iri, r.score) for r in first] == [(r.concept.iri, r.score) for r in second]
    assert [r.concept.iri for r in first] == ["a", "z"]


def test_in_memory_prefix_search_checks_every_label_rung() -> None:
    ontology = InMemoryOntology(
        [
            Concept(
                iri="all-rungs",
                label="Legacy Caption",
                preferred_label="Current Heading",
                alternative_labels=("Alias Name",),
            )
        ]
    )

    assert [c.iri for c in ontology.search_by_prefix("legacy")] == ["all-rungs"]
    assert [c.iri for c in ontology.search_by_prefix("current")] == ["all-rungs"]
    assert [c.iri for c in ontology.search_by_prefix("alias")] == ["all-rungs"]


def test_in_memory_searches_truncate_after_deterministic_ordering() -> None:
    concepts = [
        Concept(iri=f"iri-{i:03d}", label="Common Label", definition="common definition")
        for i in reversed(range(75))
    ]
    ontology = InMemoryOntology(concepts)

    assert [c.iri for c, _ in ontology.search_by_label("Common Label", limit=50)] == [
        f"iri-{i:03d}" for i in range(50)
    ]
    assert [c.iri for c in ontology.search_by_prefix("Common", limit=50)] == [
        f"iri-{i:03d}" for i in range(50)
    ]
    assert [c.iri for c, _ in ontology.search_by_definition("common", limit=50)] == [
        f"iri-{i:03d}" for i in range(50)
    ]


_T = TypeVar("_T")


class _OrderVaryingRecallOntology(InMemoryOntology):
    def __init__(self, concepts: list[Concept]) -> None:
        super().__init__(concepts)
        self.reverse = False

    def _vary(self, values: list[_T]) -> list[_T]:
        self.reverse = not self.reverse
        return list(reversed(values)) if self.reverse else values

    def search_by_label(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        return self._vary(super().search_by_label(query, limit=limit))

    def search_by_prefix(self, prefix: str, *, limit: int = 50) -> list[Concept]:
        return self._vary(super().search_by_prefix(prefix, limit=limit))

    def search_by_definition(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        return self._vary(super().search_by_definition(query, limit=limit))


def test_recall_normalizes_order_varying_provider_batches() -> None:
    concepts = [Concept(iri=f"iri-{i:03d}", label="Agreement Remedy") for i in range(60)]
    ontology = _OrderVaryingRecallOntology(concepts)
    engine = MultiStrategyRecall(ontology, top_n=50)

    first = engine.recall("agreement remedy")
    second = engine.recall("agreement remedy")

    assert [(result.concept.iri, result.score) for result in first] == [
        (result.concept.iri, result.score) for result in second
    ]


class _CountingRecallOntology(InMemoryOntology):
    def __init__(self) -> None:
        super().__init__([Concept(iri="hit", label="Agreement Remedy")])
        self.calls: list[tuple[str, str, int]] = []

    def search_by_label(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        self.calls.append(("label", query, limit))
        return super().search_by_label(query, limit=limit)

    def search_by_prefix(self, prefix: str, *, limit: int = 50) -> list[Concept]:
        self.calls.append(("prefix", prefix, limit))
        return super().search_by_prefix(prefix, limit=limit)

    def search_by_definition(self, query: str, *, limit: int = 20) -> list[tuple[Concept, float]]:
        self.calls.append(("definition", query, limit))
        return super().search_by_definition(query, limit=limit)


def test_search_cache_keys_include_method_query_and_limit() -> None:
    ontology = _CountingRecallOntology()
    engine = MultiStrategyRecall(ontology)

    engine._search_by_label("Agreement", limit=1)
    engine._search_by_label("Agreement", limit=1)
    engine._search_by_label("agreement", limit=1)
    engine._search_by_label("Agreement", limit=2)
    engine._search_by_prefix("Agreement", limit=1)
    engine._search_by_definition("Agreement", limit=1)

    assert ontology.calls == [
        ("label", "Agreement", 1),
        ("label", "agreement", 1),
        ("label", "Agreement", 2),
        ("prefix", "Agreement", 1),
        ("definition", "Agreement", 1),
    ]


def test_search_cache_is_lru_bounded() -> None:
    ontology = _CountingRecallOntology()
    engine = MultiStrategyRecall(ontology, search_cache_capacity=2)

    engine._search_by_prefix("first", limit=1)
    engine._search_by_prefix("second", limit=1)
    engine._search_by_prefix("first", limit=1)
    engine._search_by_prefix("third", limit=1)
    engine._search_by_prefix("second", limit=1)

    assert ontology.calls == [
        ("prefix", "first", 1),
        ("prefix", "second", 1),
        ("prefix", "third", 1),
        ("prefix", "second", 1),
    ]


def test_search_cache_returns_fresh_lists() -> None:
    ontology = _CountingRecallOntology()
    engine = MultiStrategyRecall(ontology)

    first = engine._search_by_label("Agreement Remedy", limit=1)
    expected = list(first)
    first.clear()

    assert engine._search_by_label("Agreement Remedy", limit=1) == expected
    assert ontology.calls == [("label", "Agreement Remedy", 1)]


def test_score_cache_key_contains_every_scoring_input() -> None:
    engine = MultiStrategyRecall(_ontology())
    base = Concept(
        iri="same",
        label="Label",
        definition="Definition",
        alternative_labels=("Alias",),
        preferred_label="Preferred",
    )
    variants = [
        base,
        Concept(**{**base.__dict__, "label": "Other label"}),
        Concept(**{**base.__dict__, "definition": "Other definition"}),
        Concept(**{**base.__dict__, "alternative_labels": ("Other alias",)}),
        Concept(**{**base.__dict__, "preferred_label": "Other preferred"}),
    ]

    with patch("folio_resolve.recall.compute_relevance_score", return_value=1.0) as score:
        engine._score({"query"}, "query", base)
        engine._score({"other"}, "query", base)
        engine._score({"query"}, "other query", base)
        for concept in variants:
            engine._score({"query"}, "query", concept)

    assert score.call_count == 7


def test_score_cache_is_lru_bounded() -> None:
    engine = MultiStrategyRecall(_ontology(), score_cache_capacity=2)
    concept = Concept(iri="concept", label="Label")

    with patch("folio_resolve.recall.compute_relevance_score", return_value=1.0) as score:
        engine._score({"first"}, "first", concept)
        engine._score({"second"}, "second", concept)
        engine._score({"first"}, "first", concept)
        engine._score({"third"}, "third", concept)
        engine._score({"second"}, "second", concept)

    assert score.call_count == 4


def test_score_cache_can_be_disabled() -> None:
    engine = MultiStrategyRecall(_ontology(), score_cache_capacity=0)
    concept = Concept(iri="concept", label="Label")

    with patch("folio_resolve.recall.compute_relevance_score", return_value=1.0) as score:
        engine._score({"query"}, "query", concept)
        engine._score({"query"}, "query", concept)

    assert score.call_count == 2


def test_score_cache_does_not_affect_engine_equality() -> None:
    ontology = _ontology()
    first = MultiStrategyRecall(ontology)
    second = MultiStrategyRecall(ontology)

    first._score({"query"}, "query", Concept(iri="concept", label="Label"))

    assert first == second


def test_pipeline_recall_off_is_exact_existing_output_and_on_is_additive() -> None:
    ontology = _ontology()
    baseline = MatchPipeline(ontology=ontology, score_floor=30.0).match("benefit bargain")
    explicit_off = MatchPipeline(ontology=ontology, recall_engine=None, score_floor=30.0).match(
        "benefit bargain"
    )

    assert explicit_off == baseline

    with_recall = MatchPipeline(
        ontology=ontology,
        recall_engine=MultiStrategyRecall(ontology, threshold=30.0),
        score_floor=30.0,
    ).match("benefit bargain")
    baseline_iris = {candidate.iri for candidate in baseline}
    recalled = next(candidate for candidate in with_recall if candidate.iri == "definition-only")
    assert baseline_iris <= {candidate.iri for candidate in with_recall}
    assert recalled.extraction_path == "multi_strategy_recall"


@dataclass
class _Owl:
    iri: str
    label: str
    definition: str | None = None
    sub_class_of: tuple[str, ...] = ()


class _Folio:
    def __init__(self) -> None:
        self.items = {
            "child": _Owl("child", "Contract Damages", sub_class_of=("parent",)),
            "parent": _Owl("parent", "Civil Remedies"),
        }

    def __contains__(self, iri: str) -> bool:
        return iri in self.items

    def __getitem__(self, iri: str) -> _Owl:
        return self.items[iri]

    def search_by_prefix(self, prefix: str) -> list[_Owl]:
        return [self.items["child"]] if prefix == "contra" else []

    def search_by_label(self, query: str, *, limit: int) -> list[object]:
        return [(self.items["child"], 80.0), (self.items["parent"], 80.0)]

    def search_by_definition(self, query: str, *, limit: int) -> list[object]:
        assert limit == 7
        return [(self.items["child"], 80.0), self.items["parent"]]


class _LargePrefixFolio(_Folio):
    def __init__(self) -> None:
        self.items = {
            f"iri-{i:03d}": _Owl(f"iri-{i:03d}", "Common Label") for i in reversed(range(75))
        }

    def search_by_prefix(self, prefix: str) -> list[_Owl]:
        return list(self.items.values())


class _TiedDefinitionFolio(_Folio):
    def search_by_definition(self, query: str, *, limit: int) -> list[object]:
        return [(self.items["parent"], 80.0), (self.items["child"], 80.0)]


def test_folio_adapter_exposes_runtime_recall_capabilities_defensively() -> None:
    provider = FolioPythonProvider(_Folio())

    assert isinstance(provider, RecallOntology)
    assert [concept.iri for concept in provider.search_by_prefix("contra")] == ["child"]
    assert provider.search_by_definition("agreement", limit=7) == [
        (provider.get_concept("child"), 80.0),
        (provider.get_concept("parent"), 0.0),
    ]
    assert [concept.iri for concept in provider.parents_of("child")] == ["parent"]


def test_folio_scored_searches_sort_returned_score_ties_by_iri() -> None:
    provider = FolioPythonProvider(_Folio())

    assert [concept.iri for concept, _ in provider.search_by_label("contract", limit=7)] == [
        "child",
        "parent",
    ]
    assert [
        concept.iri
        for concept, _ in FolioPythonProvider(_TiedDefinitionFolio()).search_by_definition(
            "contract", limit=7
        )
    ] == ["child", "parent"]


def test_folio_prefix_sorts_full_result_before_truncation() -> None:
    assert [
        c.iri for c in FolioPythonProvider(_LargePrefixFolio()).search_by_prefix("common", limit=50)
    ] == [f"iri-{i:03d}" for i in range(50)]
