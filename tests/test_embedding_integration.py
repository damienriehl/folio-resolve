"""Real-model smoke tests, explicitly selected with -m embedding_integration.

No optional dependency imports occur during collection. Model acquisition is a
separate step; an explicit run without its offline snapshot is an error.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from folio_resolve import Concept, InMemoryOntology, MatchPipeline
from folio_resolve.embedding import BruteForceIndex, LocalEmbeddingProvider, _cosine

pytestmark = pytest.mark.embedding_integration
MODEL = json.loads(Path(__file__).with_name("embedding_model.json").read_text())


@pytest.fixture(scope="module")
def provider() -> LocalEmbeddingProvider:
    configured = os.environ.get("FOLIO_RESOLVE_EMBEDDING_MODEL_PATH")
    if not configured:
        pytest.fail("Set FOLIO_RESOLVE_EMBEDDING_MODEL_PATH to the pinned local snapshot directory")
    snapshot = Path(configured).resolve()
    if not snapshot.is_dir() or snapshot.name != MODEL["revision"]:
        pytest.fail(f"Required snapshot directory must exist and be named {MODEL['revision']}")
    if any(os.environ.get(key) != "1" for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
        pytest.fail("Set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 before model tests")
    return LocalEmbeddingProvider(str(snapshot))


def test_real_single_batch_embeddings_and_cosine(provider: LocalEmbeddingProvider) -> None:
    texts = ["A lawyer represents a client in court.", "Legal counsel advocates for a defendant."]
    single = provider.embed(texts[0])
    batch = provider.embed_batch(texts)
    assert provider.dimension() == MODEL["dimension"]
    assert len(batch) == len(texts)
    for vector in [single, *batch]:
        assert len(vector) == MODEL["dimension"]
        assert all(math.isfinite(value) for value in vector)
        assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0, abs=1e-5)
    assert single == pytest.approx(batch[0], abs=1e-5)
    idx = BruteForceIndex(provider)
    similarities = idx.similarity_batch([(texts[0], texts[0]), tuple(texts)])
    assert similarities[0] == pytest.approx(1.0, abs=1e-5)
    assert similarities[1] == pytest.approx(_cosine(*batch), abs=1e-5)
    assert all(math.isfinite(value) and -1.00001 <= value <= 1.00001 for value in similarities)


def test_real_model_index_pipeline_semantic_provenance(provider: LocalEmbeddingProvider) -> None:
    # Public, hand-written smoke fixtures; these are not a retrieval-quality benchmark.
    concepts = [
        Concept(iri="test:legal-counsel", label="Legal Counsel", branch="Actors",
                definition="A lawyer who represents clients and provides legal advice."),
        Concept(iri="test:building-permit", label="Building Permit", branch="Documents",
                definition="Official authorization to construct a structure."),
    ]
    ontology = InMemoryOntology(concepts)
    idx = BruteForceIndex(provider)
    idx.build([c.iri for c in concepts], [c.label for c in concepts], [c.definition for c in concepts])
    query = "An attorney represents defendants."
    ranked = idx.query(query)
    assert len(ranked) == len(concepts)
    assert idx.score_candidates(query, [c.iri for c in concepts]) == pytest.approx(
        {iri: score for iri, _, score in ranked}
    )
    # Weak fuzzy label evidence cannot pass the existing default floor.
    assert MatchPipeline(ontology=ontology).match(query) == []
    # Lower only this smoke fixture's floor to inspect provenance, not model quality.
    results = MatchPipeline(ontology=ontology, semantic_index=idx, score_floor=0.0).match(query)
    assert results
    assert any(result.iri == "test:legal-counsel" for result in results)
    for result in results:
        concept = ontology.get_concept(result.iri)
        assert concept is not None
        assert result.extraction_path == "semantic"
        assert result.surface_term == query
        assert result.label == concept.label
        assert result.branch == concept.branch
        assert math.isfinite(result.score)
