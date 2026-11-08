import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from folio_resolve.ontology import Concept, FolioPythonProvider, InMemoryOntology
from folio_resolve.spans import SpanTagger, TagResult, load_crosswalk, load_lexicon

FIXTURES = Path(__file__).parent / "fixtures"
BREACH = "https://folio.openlegalstandard.org/BreachOfContract"
SKOS = "http://www.w3.org/2004/02/skos/core#"


@pytest.fixture
def lexicon():
    return load_lexicon(
        FIXTURES / "opengloss-mini.jsonl.gz", FIXTURES / "opengloss-mini.manifest.json"
    )


@pytest.fixture
def crosswalk():
    return load_crosswalk(FIXTURES / "folio-opengloss-mini.sssom.tsv")


@pytest.fixture
def ontology():
    return InMemoryOntology(
        [
            Concept(
                iri=BREACH, label="Breach of Contract", alternative_labels=("Contract breach",)
            ),
        ]
    )


def by_surface(result):
    return {span.text: span for span in result.spans}


def test_good_faith_phrase_ranks_above_all_word_senses(lexicon):
    text = "The tenant acted in good faith."
    result = SpanTagger(lexicon=lexicon).tag(text)
    spans = by_surface(result)
    phrase = spans["good faith"]
    assert phrase.id == f"s{text.index('good faith')}-{text.index('good faith') + 10}"
    assert all(a.rank == "primary" and a.outranked_by is None for a in phrase.opengloss)
    assert len(phrase.opengloss) == len(lexicon.senses_for("good faith"))
    for word in ("good", "faith"):
        assert all(
            a.rank == "fallback" and a.outranked_by == phrase.id for a in spans[word].opengloss
        )
        assert spans[word].id in phrase.contains
        assert phrase.id in spans[word].contained_by
    assert all(text[span.start : span.end] == span.text for span in result.spans)
    assert all(a.source and a.tier and a.gloss for a in phrase.opengloss)


def test_crosswalk_joins_same_span_and_contained_word(ontology, lexicon, crosswalk):
    result = SpanTagger(ontology, lexicon, crosswalk, folio_release="folio-test-1").tag(
        "breach of contract"
    )
    spans = by_surface(result)
    links = result.links
    assert len(links) == 2
    broad = next(link for link in links if link.predicate_id == SKOS + "broadMatch")
    assert broad.subject_id == BREACH and broad.object_id == "breach:noun:0"
    assert broad.span == spans["breach of contract"].id
    assert broad.object_span == spans["breach"].id
    assert broad.confidence == 0.85
    assert broad.mapping_justification == "https://w3id.org/semapv/vocab/ManualMappingCuration"
    assert broad.mapping_set_id == crosswalk.metadata["mapping_set_id"]
    assert broad.mapping_set_version == "0.1.0"
    exact = next(link for link in links if link.predicate_id == SKOS + "exactMatch")
    assert exact.object_span == exact.span
    # A mapping to breach cannot join a sibling word outside the FOLIO phrase.
    extended = SpanTagger(ontology, lexicon, crosswalk, folio_release="x").tag(
        "breach. breach of contract"
    )
    assert all(link.object_span != extended.spans[0].id for link in extended.links)


def test_ranking_is_per_layer_and_uses_nearest_anchored_container(lexicon):
    ontology = InMemoryOntology(
        [
            Concept(iri="phrase", label="acted in good faith"),
            Concept(iri="middle", label="in good faith"),
            Concept(iri="word", label="faith"),
        ]
    )
    spans = by_surface(SpanTagger(ontology, lexicon, folio_release="x").tag("acted in good faith"))
    assert all(anchor.rank == "primary" for anchor in spans["good faith"].opengloss)
    assert spans["faith"].folio[0].outranked_by == spans["in good faith"].id
    assert spans["faith"].opengloss[0].outranked_by == spans["good faith"].id
    assert spans["faith"].id in spans["acted in good faith"].contains
    assert len(spans["faith"].contained_by) == 3


def test_single_layer_provenance_and_no_links(ontology, lexicon, crosswalk):
    folio_only = SpanTagger(ontology, crosswalk=crosswalk, folio_release="x").tag(
        "breach of contract"
    )
    assert folio_only.links == []
    assert all(not span.opengloss for span in folio_only.spans)
    dumped = json.loads(folio_only.to_json())["provenance"]
    assert "opengloss" not in dumped and "crosswalk" not in dumped
    og_only = SpanTagger(lexicon=lexicon, crosswalk=crosswalk).tag("breach of contract")
    assert og_only.links == []
    assert all(not span.folio for span in og_only.spans)
    dumped = json.loads(og_only.to_json())["provenance"]
    assert "folio" not in dumped and "crosswalk" not in dumped


def test_deterministic_serialization_provenance_and_caller_date(ontology, lexicon, crosswalk):
    tagger = SpanTagger(ontology, lexicon, crosswalk, folio_release="folio-commit")
    result = tagger.tag("breach of contract")
    assert isinstance(result, TagResult)
    assert result.to_json() == tagger.tag("breach of contract").to_json()
    payload = json.loads(result.to_json())
    assert "tagged_on" not in payload
    assert (
        tagger.tag("breach", tagged_on="caller supplies this verbatim").tagged_on
        == "caller supplies this verbatim"
    )
    assert (
        "caller supplies this verbatim"
        in tagger.tag("breach", tagged_on="caller supplies this verbatim").to_json()
    )
    provenance = result.provenance
    assert provenance.opengloss.revision == "c64e480015b8c84f9d08469d4dfd64a7a8fb89bd"
    assert provenance.opengloss.lexicon_digest == lexicon.manifest.content_sha256
    assert provenance.folio.release == "folio-commit"
    assert len(provenance.folio.label_index_digest) == 64
    assert provenance.crosswalk.mapping_set_version == "0.1.0"
    assert provenance.folio_resolve_version and provenance.tagger_version
    assert [(s.start, -s.end) for s in result.spans] == sorted(
        (s.start, -s.end) for s in result.spans
    )
    assert all(
        [a.id for a in s.opengloss] == sorted(a.id for a in s.opengloss) for s in result.spans
    )
    assert [
        (link.span, link.subject_id, link.predicate_id, link.object_id) for link in result.links
    ] == sorted(
        (link.span, link.subject_id, link.predicate_id, link.object_id) for link in result.links
    )


def test_label_digest_and_results_are_independent_of_provider_iteration_order():
    concepts = [Concept(iri="b", label="Appeal"), Concept(iri="a", label="Appeal")]
    forward = SpanTagger(InMemoryOntology(concepts), folio_release="x").tag("appeal")
    reverse = SpanTagger(InMemoryOntology(concepts[::-1]), folio_release="x").tag("appeal")
    assert forward.to_json() == reverse.to_json()
    changed = SpanTagger(
        InMemoryOntology([Concept(iri="a", label="Appeals")]), folio_release="x"
    ).tag("appeal")
    assert (
        changed.provenance.folio.label_index_digest != forward.provenance.folio.label_index_digest
    )
    assert forward.provenance.folio.label_index_digest != hashlib.sha256(b"").hexdigest()


def test_no_layer_and_folio_release_are_required(ontology):
    with pytest.raises(ValueError, match="layer"):
        SpanTagger()
    with pytest.raises(ValueError, match="folio_release"):
        SpanTagger(ontology)


def test_shared_labels_all_concepts_and_legacy_fallback():
    concepts = [Concept(iri="b", label="Appeal"), Concept(iri="a", label="Appeal")]
    ontology = InMemoryOntology(concepts)
    assert [c.iri for c in ontology.all_concepts()] == ["a", "b"]
    result = SpanTagger(ontology, folio_release="x").tag("appeal")
    assert [a.id for a in result.spans[0].folio] == ["a", "b"]
    assert result.provenance.folio.label_source == "all_concepts"

    class LegacyProvider:
        def all_labels(self):
            return ontology.all_labels()

        def search_by_label(self, query, *, limit=20):
            return ontology.search_by_label(query, limit=limit)

        def get_concept(self, iri):
            return ontology.get_concept(iri)

    fallback = SpanTagger(LegacyProvider(), folio_release="x").tag("appeal")
    assert fallback.provenance.folio.label_source == "all_labels"
    assert len(fallback.spans[0].folio) == 1


def test_folio_python_all_concepts_preserves_shared_labels_and_filters_foreign_iris():
    provider = FolioPythonProvider(
        _folio=SimpleNamespace(
            classes=[
                SimpleNamespace(iri="https://folio.openlegalstandard.org/B", label="Appeal"),
                SimpleNamespace(iri="https://foreign.example/C", label="Appeal"),
                SimpleNamespace(iri="https://folio.openlegalstandard.org/A", label="Appeal"),
            ]
        )
    )
    assert [c.iri.rsplit("/", 1)[1] for c in provider.all_concepts()] == ["A", "B"]
    assert len(SpanTagger(provider, folio_release="x").tag("appeal").spans[0].folio) == 2


def test_folio_all_labels_confidences_and_no_plural(lexicon):
    concept = Concept(
        iri="tenant",
        label="Occupier",
        preferred_label="Tenant",
        alternative_labels=("Renter", "the", "ab", "with with"),
    )
    tagger = SpanTagger(InMemoryOntology([concept]), lexicon, folio_release="x")
    spans = by_surface(tagger.tag("Tenant Renter Occupier tenants the ab with with"))
    assert spans["Tenant"].folio[0].confidence == 0.72
    assert spans["Renter"].folio[0].confidence == 0.55
    assert spans["Occupier"].folio[0].id == "tenant"
    assert not spans["tenants"].folio
    assert all(a.match_method == "plural" for a in spans["tenants"].opengloss)
    assert all(a.pos == "noun" for a in spans["tenants"].opengloss)
    assert spans["tenants"].opengloss[0].confidence < spans["Tenant"].opengloss[0].confidence
    assert "the" not in spans and "ab" not in spans and "with with" not in spans


def test_function_words_regions_and_empty_results(ontology, lexicon):
    tagger = SpanTagger(ontology, lexicon, folio_release="x")
    assert tagger.tag("of the").spans == []
    assert tagger.tag("with with").spans == []
    assert tagger.tag("").spans == []
    assert tagger.tag("zzzzzzz").spans == []
    text = "good faith. breach of contract"
    spans = by_surface(tagger.tag(text, regions=[(12, len(text))]))
    assert "good faith" not in spans and "breach of contract" in spans
    assert tagger.tag(text, regions=[]).spans == []


def test_cross_process_determinism_and_tagging_without_pyarrow():
    script = """
import sys
sys.modules['pyarrow'] = None
from pathlib import Path
from folio_resolve.spans import SpanTagger, load_lexicon, load_crosswalk
from folio_resolve.ontology import Concept, InMemoryOntology
p = Path(sys.argv[1])
lex = load_lexicon(p / 'opengloss-mini.jsonl.gz', p / 'opengloss-mini.manifest.json')
crosswalk = load_crosswalk(p / 'folio-opengloss-mini.sssom.tsv')
ontology = InMemoryOntology([Concept(iri='https://folio.openlegalstandard.org/BreachOfContract', label='Breach of Contract')])
print(SpanTagger(ontology, lex, crosswalk, folio_release='x').tag('breach of contract').to_json())
"""
    results = [
        subprocess.run(
            [sys.executable, "-c", script, str(FIXTURES)], capture_output=True, text=True
        )
        for _ in range(2)
    ]
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    assert results[0].stdout == results[1].stdout
    assert json.loads(results[0].stdout)["links"]
