"""Public benchmark accounting and real lightweight pipeline coverage."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from folio_resolve import Concept

_SPEC = importlib.util.spec_from_file_location(
    "embedding_recall_benchmark",
    Path(__file__).parents[1] / "benchmarks/embedding_recall.py",
)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)
build_pipeline = benchmark.build_pipeline
corpus_digest = benchmark.corpus_digest
load_corpus = benchmark.load_corpus
load_fixtures = benchmark.load_fixtures
recall_metrics = benchmark.recall_metrics
stable_digest = benchmark.stable_digest


def test_recall_counts_queries_not_number_of_acceptable_answers():
    cases = [
        {"id": "a", "acceptable_iris": ["x", "y"]},
        {"id": "b", "acceptable_iris": ["z"]},
        {"id": "c", "acceptable_iris": ["missing"]},
        {"id": "negative", "acceptable_iris": []},
    ]
    ranked = {"a": ["y", "x"], "b": ["x", "z"], "c": [], "negative": ["x"]}
    result = recall_metrics(cases, ranked)
    assert result == {
        "positive_queries": 3,
        "negative_queries": 1,
        "recall_at_1": 1 / 3,
        "recall_at_5": 2 / 3,
    }
    assert recall_metrics([cases[-1]], ranked)["recall_at_1"] is None


def test_corpus_preserves_label_collisions_and_merges_duplicate_iris(tmp_path):
    xml = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:owl="http://www.w3.org/2002/07/owl#"
        xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
        xmlns:skos="http://www.w3.org/2004/02/skos/core#">
      <owl:Class rdf:about="b"><rdfs:label>Shared</rdfs:label></owl:Class>
      <owl:Class rdf:about="a"><rdfs:label>Shared</rdfs:label></owl:Class>
      <owl:Class rdf:about="a"><skos:altLabel>Alias</skos:altLabel>
        <skos:definition>Definition</skos:definition></owl:Class>
      <owl:Class rdf:about="c"/>
    </rdf:RDF>"""
    path = tmp_path / "public.owl"
    xml = xml.replace(b'rdf:about="', b'rdf:about="https://folio.openlegalstandard.org/')
    path.write_bytes(xml)
    digest = hashlib.sha256(xml).hexdigest()
    concepts = load_corpus(path, digest)
    assert [c.iri for c in concepts] == [
        "https://folio.openlegalstandard.org/" + suffix for suffix in ("a", "b", "c")
    ]
    assert [c.label for c in concepts] == [
        "Shared",
        "Shared",
        "https://folio.openlegalstandard.org/c",
    ]
    assert concepts[0].alternative_labels == ("Alias",)
    assert concepts[0].definition == "Definition"
    assert corpus_digest(concepts) == corpus_digest(load_corpus(path, digest))
    with pytest.raises(ValueError, match="SHA-256"):
        load_corpus(path, "0" * 64)


def test_stable_provenance_is_key_order_independent_but_content_sensitive():
    assert stable_digest({"model": "pin", "fixture": [1, 2]}) == stable_digest(
        {"fixture": [1, 2], "model": "pin"}
    )
    assert stable_digest({"fixture": [1, 2]}) != stable_digest({"fixture": [2, 1]})


def test_disabled_and_hashing_use_real_pipeline_and_keep_provenance():
    concepts = [
        Concept(
            iri="test:relief", label="Astronomical Phenomenon", definition="quasar nebula galaxy"
        )
    ]
    disabled = build_pipeline(concepts, "disabled")
    hashing = build_pipeline(concepts, "hashing")
    assert disabled.semantic_index is None
    assert all(c.extraction_path != "semantic" for c in disabled.match("quasar nebula galaxy"))
    results = hashing.match("quasar nebula galaxy")
    assert results and results[0].iri == "test:relief"
    assert results[0].extraction_path == "semantic"
    assert results[0].surface_term == "quasar nebula galaxy"
    assert disabled.score_floor == hashing.score_floor == 45.0


def test_frozen_fixture_contains_only_owner_approved_cases():
    path = Path(__file__).parents[1] / "benchmarks/fixtures/embedding_recall.json"
    fixture = load_fixtures(path)
    assert [c["id"] for c in fixture["cases"]] == ["E1", "E2", "P1", "P3", "P4", "P6", "N1", "N2"]
    assert sum(bool(c["acceptable_iris"]) for c in fixture["cases"]) == 6
    assert fixture["model"] == json.loads(
        (Path(__file__).parent / "embedding_model.json").read_text()
    )


def test_recall_at_five_includes_fifth_but_not_sixth_candidate():
    cases = [
        {"id": "fifth", "acceptable_iris": ["target"]},
        {"id": "sixth", "acceptable_iris": ["target"]},
    ]
    ranked = {"fifth": ["a", "b", "c", "d", "target"], "sixth": ["a", "b", "c", "d", "e", "target"]}
    assert recall_metrics(cases, ranked)["recall_at_5"] == 0.5


def test_percentile_uses_documented_linear_interpolation():
    assert benchmark.percentile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.5
    assert benchmark.percentile([4.0, 1.0, 3.0, 2.0], 0.95) == pytest.approx(3.85)


def test_missing_targets_and_paraphrase_token_overlap_fail():
    concept = Concept(iri="x", label="Term", definition="shared word")
    fixture = {
        "cases": [
            {
                "id": "p",
                "kind": "paraphrase",
                "query": "shared",
                "acceptable_iris": ["x"],
                "expected_labels": {"x": "Term"},
            }
        ]
    }
    with pytest.raises(ValueError, match="Missing fixture target"):
        benchmark.validate_answers(fixture, [])
    with pytest.raises(ValueError, match="literal token overlap"):
        benchmark.validate_answers(fixture, [concept])


def test_local_variant_requires_pinned_existing_offline_model(tmp_path):
    with pytest.raises(ValueError, match="pinned snapshot"):
        build_pipeline([], "local", tmp_path, {"revision": "absent", "dimension": 384})
