"""Ontology provider seam — InMemoryOntology, the folio-python adapter, and OWL normalization.

``FolioPythonProvider`` is exercised against a hand-rolled fake that mimics only the folio-python
surface the adapter touches (``.classes``, ``.search_by_label``, ``in``/``[]``), so the suite
never constructs the real (heavy, network-backed) ``FOLIO`` catalogue. Concepts are synthetic —
this repo is public.
"""

from __future__ import annotations

import sys
import types
from functools import cache
from typing import Any, NoReturn

import pytest

from folio_resolve import Concept, InMemoryOntology, LabelInfo, OntologyProvider
from folio_resolve.ontology import FolioPythonProvider, _owl_to_concept
from folio_resolve.scoring import compute_relevance_score, content_words

FOLIO_IRI = "https://folio.openlegalstandard.org/"


# -- InMemoryOntology ----------------------------------------------------


def test_in_memory_ontology_satisfies_the_protocol(ontology: InMemoryOntology) -> None:
    # OntologyProvider is runtime_checkable; consumers type against it, not the concrete class.
    assert isinstance(ontology, OntologyProvider)


def test_all_labels_prefers_the_preferred_label_over_label() -> None:
    ont = InMemoryOntology(
        [Concept(iri="R1", label="Deposition Transcript", preferred_label="Deposition")]
    )
    labels = ont.all_labels()
    assert labels["deposition"].concept.iri == "R1"
    assert labels["deposition"].label_type == "preferred"
    assert "deposition transcript" not in labels  # the preferred label owns the slot


def test_all_labels_lowercases_and_tags_alternatives() -> None:
    ont = InMemoryOntology(
        [Concept(iri="R1", label="Cross-Examination", alternative_labels=("Cross Exam", "CROSS-EXAM"))]
    )
    labels = ont.all_labels()
    assert labels["cross-examination"].label_type == "preferred"
    assert labels["cross exam"].label_type == "alternative"
    assert labels["cross-exam"].label_type == "alternative"  # lowercased key


def test_a_preferred_label_beats_an_alternative_registered_first() -> None:
    # setdefault for alternatives, plain assignment for preferred: a later concept whose
    # PREFERRED label collides with an earlier concept's ALTERNATIVE takes the slot.
    ont = InMemoryOntology(
        [
            Concept(iri="R-license", label="License", alternative_labels=("Agreement",)),
            Concept(iri="R-agreements", label="Agreement"),
        ]
    )
    assert ont.all_labels()["agreement"].concept.iri == "R-agreements"


def test_the_first_alternative_wins_its_slot() -> None:
    ont = InMemoryOntology(
        [
            Concept(iri="R-a", label="A", alternative_labels=("Shared",)),
            Concept(iri="R-b", label="B", alternative_labels=("Shared",)),
        ]
    )
    assert ont.all_labels()["shared"].concept.iri == "R-a"


def test_blank_labels_never_become_index_keys() -> None:
    """Regression: a label-less concept used to register an empty-string key.

    An empty key matches nothing a consumer can search for, is skipped by the entity ruler's
    pattern builder anyway, and every later blank-labelled concept overwrote it — so the map
    silently mis-reported its own size. FolioPythonProvider always guarded; this did not.
    """
    ont = InMemoryOntology(
        [
            Concept(iri="R-blank", label="", alternative_labels=("", "Real Alt")),
            Concept(iri="R-other", label=""),
        ]
    )
    labels = ont.all_labels()
    assert "" not in labels
    assert set(labels) == {"real alt"}


def test_search_by_label_is_word_order_invariant_and_ranked(ontology: InMemoryOntology) -> None:
    results = ontology.search_by_label("rules of arbitration")
    assert results[0][0].iri == "R-arb-rules"
    # Ranked best-first.
    assert [s for _, s in results] == sorted((s for _, s in results), reverse=True)


def test_search_by_label_drops_zero_scores(ontology: InMemoryOntology) -> None:
    assert all(score > 0 for _, score in ontology.search_by_label("arbitration"))
    assert ontology.search_by_label("zzzzqqqq") == []


def test_search_by_label_honors_limit(ontology: InMemoryOntology) -> None:
    assert len(ontology.search_by_label("litigation", limit=1)) <= 1
    unlimited = ontology.search_by_label("litigation", limit=100)
    assert len(ontology.search_by_label("litigation", limit=2)) == min(2, len(unlimited))


def test_search_by_label_uses_definitions_and_synonyms() -> None:
    ont = InMemoryOntology(
        [
            Concept(
                iri="R-burdens",
                label="Litigation Burdens of Proof",
                definition="Allocation of the burden of proof, including presumptions.",
                alternative_labels=("Evidentiary Burdens",),
            )
        ]
    )
    # No shared label token, but the definition carries "presumptions".
    assert ont.search_by_label("presumptions")
    assert ont.search_by_label("evidentiary burdens")


def test_get_concept_roundtrip_and_miss(ontology: InMemoryOntology) -> None:
    assert ontology.get_concept("R-arb-rules") is not None
    assert ontology.get_concept("R-nope") is None


def test_the_concept_list_is_copied_not_aliased() -> None:
    concepts = [Concept(iri="R1", label="Arbitration")]
    ont = InMemoryOntology(concepts)
    concepts.append(Concept(iri="R2", label="Mediation"))
    assert ont.get_concept("R2") is None  # mutating the caller's list must not mutate the index


def test_empty_ontology_is_usable() -> None:
    ont = InMemoryOntology([])
    assert ont.all_labels() == {}
    assert ont.search_by_label("anything") == []
    assert ont.get_concept("R1") is None


# -- OWL -> Concept normalization ----------------------------------------


class _Owl:
    """A minimal stand-in for a folio-python OWL class object."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def test_owl_to_concept_normalizes_the_happy_path() -> None:
    c = _owl_to_concept(
        _Owl(
            iri=f"{FOLIO_IRI}R1",
            label="Arbitration Rules",
            preferred_label="Arbitration Rules",
            definition="Rules governing arbitration.",
            alternative_labels=["Rules of Arbitration"],
            branch="Service",
            parent_iris=[f"{FOLIO_IRI}R0"],
        )
    )
    assert c == Concept(
        iri=f"{FOLIO_IRI}R1",
        label="Arbitration Rules",
        definition="Rules governing arbitration.",
        alternative_labels=("Rules of Arbitration",),
        preferred_label="Arbitration Rules",
        branch="Service",
        parent_iris=(f"{FOLIO_IRI}R0",),
    )


def test_owl_to_concept_falls_back_to_preferred_label_for_label() -> None:
    c = _owl_to_concept(_Owl(iri="R1", label=None, preferred_label="Deposition"))
    assert c.label == "Deposition"


def test_owl_to_concept_falls_back_to_sub_class_of_for_parents() -> None:
    c = _owl_to_concept(_Owl(iri="R1", label="X", sub_class_of=["R0", "R00"]))
    assert c.parent_iris == ("R0", "R00")


def test_owl_to_concept_survives_missing_and_wrongly_typed_attributes() -> None:
    # folio-python hands back None for absent fields, and OWL rows are not schema-checked.
    c = _owl_to_concept(_Owl(iri="R1", label=42, definition=None, alternative_labels=None, branch=None))
    assert c.label == ""
    assert c.definition is None
    assert c.alternative_labels == ()
    assert c.branch == ""
    assert c.parent_iris == ()
    # A bare object with no attributes at all normalizes rather than raising.
    assert _owl_to_concept(object()).iri == ""


def test_owl_to_concept_filters_non_string_list_entries() -> None:
    c = _owl_to_concept(_Owl(iri="R1", label="X", alternative_labels=["Good", None, 7, "Also"]))
    assert c.alternative_labels == ("Good", "Also")


# -- FolioPythonProvider adapter -----------------------------------------


class _FakeFolio:
    """Only the folio-python surface FolioPythonProvider actually touches."""

    def __init__(self, classes: list[_Owl]) -> None:
        self.classes = classes
        self._by_iri = {c.iri: c for c in classes}
        self.searched: list[str] = []

    def search_by_label(self, query: str, **kwargs: Any) -> list[Any]:
        self.searched.append(query)
        return [(owl, 90.0) for owl in self.classes]

    def __contains__(self, iri: str) -> bool:
        return iri in self._by_iri

    def __getitem__(self, iri: str) -> _Owl:
        return self._by_iri[iri]


@pytest.fixture
def fake_folio() -> _FakeFolio:
    return _FakeFolio(
        [
            _Owl(
                iri=f"{FOLIO_IRI}R-arb",
                label="Arbitration Rules",
                preferred_label="Arbitration Rules",
                alternative_labels=["Rules of Arbitration"],
                branch="Service",
            ),
            # A non-FOLIO IRI (OWL imports bring in foreign vocabularies) — must be skipped.
            _Owl(iri="http://www.w3.org/2002/07/owl#Thing", label="Thing", branch=""),
        ]
    )


class _FolioSpy:
    def __init__(self, result_count: int, *, over_return_by: int = 0) -> None:
        self.result_count = result_count
        self.over_return_by = over_return_by
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search_by_label(self, label: str, **kwargs: Any) -> list[tuple[_Owl, float]]:
        self.calls.append((label, kwargs))
        requested_limit = int(kwargs.get("limit", 10))
        count = min(self.result_count, requested_limit + self.over_return_by)
        return [
            (_Owl(iri=f"iri:{index}", label=f"Contract {index}"), 100.0) for index in range(count)
        ]


class _CachingFolio(_FakeFolio):
    """Mimic the private unbounded caches used by folio-python's search paths."""

    def __init__(self, classes: list[_Owl]) -> None:
        super().__init__(classes)
        self._prefix_cache: dict[str, list[_Owl]] = {}
        self._ci_prefix_cache: dict[str, list[_Owl]] = {}
        self.search_calls = {"label": 0, "definition": 0, "prefix": 0}

    @cache  # noqa: B019 - deliberately reproduce folio-python's unbounded method cache
    def _basic_search(self, query: str) -> tuple[tuple[_Owl, float], ...]:
        return tuple((owl, 90.0) for owl in self.classes)

    def search_by_label(self, query: str, **kwargs: Any) -> list[tuple[_Owl, float]]:
        self.search_calls["label"] += 1
        return list(self._basic_search(f"label:{query}"))

    def search_by_definition(self, query: str, **kwargs: Any) -> list[tuple[_Owl, float]]:
        self.search_calls["definition"] += 1
        return list(self._basic_search(f"definition:{query}"))

    def search_by_prefix(self, prefix: str) -> list[_Owl]:
        self.search_calls["prefix"] += 1
        self._prefix_cache[prefix] = list(self.classes)
        self._ci_prefix_cache[prefix.casefold()] = list(self.classes)
        return list(self.classes)


class _FailingCachingFolio(_CachingFolio):
    def _raise_after_caching(self, query: str) -> NoReturn:
        self._basic_search(query)
        self._prefix_cache[query] = list(self.classes)
        self._ci_prefix_cache[query.casefold()] = list(self.classes)
        raise RuntimeError(f"upstream failure: {query}")

    def search_by_label(self, query: str, **kwargs: Any) -> list[tuple[_Owl, float]]:
        self._raise_after_caching(f"label:{query}")

    def search_by_definition(self, query: str, **kwargs: Any) -> list[tuple[_Owl, float]]:
        self._raise_after_caching(f"definition:{query}")

    def search_by_prefix(self, prefix: str) -> list[_Owl]:
        self._raise_after_caching(f"prefix:{prefix}")


def test_provider_all_labels_skips_foreign_iris(fake_folio: _FakeFolio) -> None:
    labels = FolioPythonProvider(fake_folio).all_labels()
    assert set(labels) == {"arbitration rules", "rules of arbitration"}
    assert labels["arbitration rules"].label_type == "preferred"
    assert labels["rules of arbitration"].label_type == "alternative"


def test_provider_all_labels_skips_blank_preferred_labels() -> None:
    folio = _FakeFolio([_Owl(iri=f"{FOLIO_IRI}R-x", label="", preferred_label="")])
    assert FolioPythonProvider(folio).all_labels() == {}


def test_provider_search_by_label_normalizes_and_limits(fake_folio: _FakeFolio) -> None:
    out = FolioPythonProvider(fake_folio).search_by_label("arbitration", limit=1)
    assert len(out) == 1
    concept, score = out[0]
    assert isinstance(concept, Concept)
    assert concept.iri == f"{FOLIO_IRI}R-arb"
    assert score == compute_relevance_score(
        content_words("arbitration"),
        "arbitration",
        concept.label,
        definition=concept.definition,
        synonyms=list(concept.alternative_labels),
        preferred_label=concept.preferred_label,
    )
    assert fake_folio.searched == ["arbitration"]


def test_provider_search_by_label_rescores_recall_candidates() -> None:
    query = "Findings of Fact"
    unrelated = _Owl(iri="R-unrelated", label="South Georgia and the South Sandwich Islands")
    matching_b = _Owl(iri="R-match-b", label=query)
    matching_a = _Owl(iri="R-match-a", label=query)

    class _RankedFakeFolio(_FakeFolio):
        def search_by_label(self, query: str, **kwargs: Any) -> list[tuple[_Owl, float]]:
            return [(unrelated, 90.0), (matching_b, 40.0), (matching_a, 30.0)]

    provider = FolioPythonProvider(_RankedFakeFolio([unrelated, matching_b, matching_a]))
    expected_score = compute_relevance_score(content_words(query), query, query)

    assert provider.search_by_label(query) == [
        (_owl_to_concept(matching_a), expected_score),
        (_owl_to_concept(matching_b), expected_score),
    ]


def test_provider_search_by_label_rescoring_is_repeatable() -> None:
    folio = _FakeFolio(
        [
            _Owl(iri="R-b", label="Contract"),
            _Owl(iri="R-a", label="Contract"),
            _Owl(iri="R-zero", label="Arbitration"),
        ]
    )
    provider = FolioPythonProvider(folio)

    first = provider.search_by_label("contract")

    assert [concept.iri for concept, _score in first] == ["R-a", "R-b"]
    assert provider.search_by_label("contract") == first


def test_folio_provider_widens_upstream_recall_window() -> None:
    folio = _FolioSpy(50)

    FolioPythonProvider(folio).search_by_label("contract", limit=27)

    assert folio.calls == [("contract", {"limit": 135})]


def test_folio_provider_can_return_more_than_upstream_default() -> None:
    folio = _FolioSpy(25)

    results = FolioPythonProvider(folio).search_by_label("contract", limit=20)

    assert len(results) == 20


def test_folio_provider_truncates_upstream_over_return() -> None:
    folio = _FolioSpy(25, over_return_by=5)

    results = FolioPythonProvider(folio).search_by_label("contract", limit=12)

    assert len(results) == 12


def test_provider_search_by_label_accepts_bare_rows() -> None:
    """folio-python's search returns bare objects on some paths, (obj, score) on others."""

    class _BareSearch(_FakeFolio):
        def search_by_label(self, query: str, **kwargs: Any) -> list[Any]:
            return list(self.classes)

    out = FolioPythonProvider(_BareSearch([_Owl(iri="R1", label="X")])).search_by_label("x")
    assert out[0][1] == 99.0


def test_provider_releases_upstream_search_caches_after_copying_results() -> None:
    owl = _Owl(iri="R1", label="Contract")
    folio = _CachingFolio([owl])
    provider = FolioPythonProvider(folio)

    assert provider.search_by_label("contract") == [(_owl_to_concept(owl), 99.0)]
    assert folio._basic_search.cache_info().currsize == 0

    assert provider.search_by_definition("agreement") == [(_owl_to_concept(owl), 90.0)]
    assert folio._basic_search.cache_info().currsize == 0

    assert provider.search_by_prefix("Con") == [_owl_to_concept(owl)]
    assert folio._prefix_cache == {}
    assert folio._ci_prefix_cache == {}


def test_provider_reuses_only_bounded_copied_search_results() -> None:
    owl = _Owl(iri="R1", label="Contract")
    folio = _CachingFolio([owl])
    provider = FolioPythonProvider(folio)

    first = provider.search_by_label("contract")
    first.clear()
    assert provider.search_by_label("contract") == [(_owl_to_concept(owl), 99.0)]
    assert folio.search_calls["label"] == 1
    assert folio._basic_search.cache_info().currsize == 0

    for index in range(257):
        provider.search_by_label(f"query-{index}")
    provider.search_by_label("contract")
    assert folio.search_calls["label"] == 259


@pytest.mark.parametrize(
    ("method_name", "query"),
    [
        ("search_by_label", "contract"),
        ("search_by_definition", "agreement"),
        ("search_by_prefix", "Con"),
    ],
)
def test_provider_releases_upstream_caches_when_search_raises(
    method_name: str, query: str
) -> None:
    folio = _FailingCachingFolio([_Owl(iri="R1", label="Contract")])
    provider = FolioPythonProvider(folio)

    with pytest.raises(RuntimeError, match=f"upstream failure: .*{query}"):
        getattr(provider, method_name)(query)

    assert folio._basic_search.cache_info().currsize == 0
    assert folio._prefix_cache == {}
    assert folio._ci_prefix_cache == {}


def test_provider_get_concept_hit_and_miss(fake_folio: _FakeFolio) -> None:
    provider = FolioPythonProvider(fake_folio)
    got = provider.get_concept(f"{FOLIO_IRI}R-arb")
    assert got is not None and got.label == "Arbitration Rules"
    assert provider.get_concept(f"{FOLIO_IRI}R-missing") is None


def test_provider_defers_the_folio_import_until_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """The heavy catalogue is constructed lazily, once, on first call — never at import time."""
    constructed: list[int] = []

    class _Lazy(_FakeFolio):
        def __init__(self) -> None:
            constructed.append(1)
            super().__init__([_Owl(iri=f"{FOLIO_IRI}R1", label="Hearing")])

    stub = types.ModuleType("folio")
    stub.FOLIO = _Lazy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "folio", stub)

    provider = FolioPythonProvider()
    assert constructed == []  # constructing the adapter touches nothing
    assert set(provider.all_labels()) == {"hearing"}
    provider.all_labels()
    assert constructed == [1]  # cached across calls


def test_provider_satisfies_the_protocol(fake_folio: _FakeFolio) -> None:
    assert isinstance(FolioPythonProvider(fake_folio), OntologyProvider)


def test_label_info_is_hashable_and_frozen() -> None:
    info = LabelInfo(concept=Concept(iri="R1", label="X"), label_type="preferred")
    assert hash(info)
    with pytest.raises(AttributeError):
        info.label_type = "alternative"  # type: ignore[misc]
