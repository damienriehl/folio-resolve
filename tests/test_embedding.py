"""Embedding seam — the dependency-free hashing provider and brute-force cosine index.

Ch02 finding 005: semantic recall is mandatory for "no shared label token" maps (Presumptions ->
Burdens of Proof). ``HashingEmbeddingProvider`` + ``BruteForceIndex`` are the pure-Python default
that makes that path exercisable with no model download and no network. ``LocalEmbeddingProvider``
needs the ``embedding`` extra and is therefore only checked for its lazy-import contract.
"""

from __future__ import annotations

import math

import pytest

from folio_resolve.embedding import (
    BruteForceIndex,
    EmbeddingProvider,
    HashingEmbeddingProvider,
    _cosine,
)

# -- cosine --------------------------------------------------------------


def test_cosine_of_identical_and_orthogonal_vectors() -> None:
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_is_scale_invariant() -> None:
    assert _cosine([3.0, 4.0], [30.0, 40.0]) == pytest.approx(1.0)


def test_cosine_of_a_zero_vector_is_zero_not_a_division_error() -> None:
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0, 1.0], [0.0, 0.0]) == 0.0


def test_cosine_rejects_mismatched_dimensions() -> None:
    # strict=True: a dimension mismatch is a wiring bug, not something to silently truncate.
    with pytest.raises(ValueError):
        _cosine([1.0, 0.0], [1.0, 0.0, 0.0])


# -- HashingEmbeddingProvider --------------------------------------------


def test_provider_satisfies_the_protocol() -> None:
    assert isinstance(HashingEmbeddingProvider(), EmbeddingProvider)


def test_embeddings_are_the_declared_dimension_and_unit_length() -> None:
    provider = HashingEmbeddingProvider(dim=64)
    vec = provider.embed("arbitration rules")
    assert provider.dimension() == 64
    assert len(vec) == 64
    assert math.sqrt(sum(v * v for v in vec)) == pytest.approx(1.0)


def test_embedding_is_deterministic_across_calls_and_instances() -> None:
    a = HashingEmbeddingProvider(dim=32).embed("burden of proof")
    b = HashingEmbeddingProvider(dim=32).embed("burden of proof")
    assert a == b


def test_embedding_is_case_insensitive_and_word_order_invariant() -> None:
    provider = HashingEmbeddingProvider(dim=64)
    assert provider.embed("Arbitration Rules") == provider.embed("arbitration rules")
    assert provider.embed("arbitration rules") == provider.embed("rules arbitration")


def test_punctuation_and_digits_are_not_tokens() -> None:
    provider = HashingEmbeddingProvider(dim=64)
    assert provider.embed("arbitration, rules!") == provider.embed("arbitration rules")
    assert provider.embed("rule 26") == provider.embed("rule")


def test_single_character_tokens_are_dropped() -> None:
    provider = HashingEmbeddingProvider(dim=64)
    assert provider.embed("a b c arbitration") == provider.embed("arbitration")


def test_an_empty_or_tokenless_text_embeds_to_the_zero_vector() -> None:
    provider = HashingEmbeddingProvider(dim=16)
    for text in ("", "   ", "123 !!!"):
        assert provider.embed(text) == [0.0] * 16
    # ...and a zero vector is similar to nothing, rather than raising.
    assert _cosine(provider.embed(""), provider.embed("arbitration")) == 0.0


def test_related_texts_are_more_similar_than_unrelated_ones() -> None:
    provider = HashingEmbeddingProvider(dim=512)
    shared = _cosine(
        provider.embed("burden of proof at trial"), provider.embed("allocation of the burden of proof")
    )
    unrelated = _cosine(provider.embed("burden of proof at trial"), provider.embed("zoning variance"))
    assert shared > unrelated


def test_embed_batch_matches_embed_elementwise() -> None:
    provider = HashingEmbeddingProvider(dim=32)
    texts = ["arbitration", "mediation", ""]
    assert provider.embed_batch(texts) == [provider.embed(t) for t in texts]
    assert provider.embed_batch([]) == []


def test_a_nonsense_dimension_is_rejected_at_construction() -> None:
    # dim=0 previously produced a ZeroDivisionError deep inside embed().
    for bad in (0, -1):
        with pytest.raises(ValueError, match="dim must be"):
            HashingEmbeddingProvider(dim=bad)


# -- BruteForceIndex -----------------------------------------------------


@pytest.fixture
def index() -> BruteForceIndex:
    idx = BruteForceIndex(HashingEmbeddingProvider(dim=512))
    idx.build(
        ["R-burdens", "R-arb", "R-slovenia"],
        ["Litigation Burdens of Proof", "Arbitration Rules", "Slovenia"],
        [
            "How presumptions allocate the burden of proof at trial.",
            "Rules governing arbitration proceedings.",
            None,
        ],
    )
    return idx


def test_an_unbuilt_index_answers_empty_rather_than_raising() -> None:
    idx = BruteForceIndex(HashingEmbeddingProvider(dim=16))
    assert idx.num_concepts == 0
    assert idx.query("anything") == []
    assert idx.score_candidates("anything", ["R1"]) == {}


def test_num_concepts_reflects_the_build(index: BruteForceIndex) -> None:
    assert index.num_concepts == 3


def test_query_returns_iri_label_cosine_triples_best_first(index: BruteForceIndex) -> None:
    results = index.query("presumptions and the burden of proof")
    assert results[0][0] == "R-burdens"
    assert results[0][1] == "Litigation Burdens of Proof"
    assert [c for _, _, c in results] == sorted((c for _, _, c in results), reverse=True)


def test_query_honors_top_k(index: BruteForceIndex) -> None:
    assert len(index.query("arbitration", top_k=1)) == 1
    assert len(index.query("arbitration", top_k=99)) == 3


def test_the_definition_is_part_of_the_indexed_text(index: BruteForceIndex) -> None:
    """Ch02's no-shared-token map: "presumptions" reaches Burdens of Proof via the definition."""
    top_iri, _, cosine = index.query("presumptions")[0]
    assert top_iri == "R-burdens"
    assert cosine > 0


def test_a_definitionless_concept_indexes_on_its_label_alone() -> None:
    idx = BruteForceIndex(HashingEmbeddingProvider(dim=256))
    idx.build(["R-a"], ["Arbitration"], [None])
    with_def = BruteForceIndex(HashingEmbeddingProvider(dim=256))
    with_def.build(["R-a"], ["Arbitration"], [""])  # empty definition is falsy -> label only
    assert idx.query("arbitration") == with_def.query("arbitration")


def test_rebuilding_replaces_the_previous_contents(index: BruteForceIndex) -> None:
    index.build(["R-new"], ["Cross-Examination"], [None])
    assert index.num_concepts == 1
    assert [iri for iri, _, _ in index.query("cross examination")] == ["R-new"]


def test_building_an_empty_index_is_allowed() -> None:
    idx = BruteForceIndex(HashingEmbeddingProvider(dim=16))
    idx.build([], [], [])
    assert idx.num_concepts == 0
    assert idx.query("x") == []


def test_mismatched_parallel_sequences_fail_at_build_not_at_query() -> None:
    """Regression: only labels/definitions were length-checked.

    A mismatched `iris` built "successfully" and then failed inside query() with a bare
    "zip() argument 2 is shorter than argument 1" — a message that names neither the argument
    nor the call that was actually wrong.
    """
    idx = BruteForceIndex(HashingEmbeddingProvider(dim=16))
    with pytest.raises(ValueError, match="parallel"):
        idx.build(["R-a", "R-b", "R-c"], ["A", "B"], [None, None])
    with pytest.raises(ValueError, match="parallel"):
        idx.build(["R-a"], ["A"], [None, None])
    assert idx.num_concepts == 0  # a rejected build leaves the index untouched


def test_score_candidates_scores_only_known_iris(index: BruteForceIndex) -> None:
    scores = index.score_candidates("arbitration", ["R-arb", "R-burdens", "R-unknown"])
    assert set(scores) == {"R-arb", "R-burdens"}
    assert scores["R-arb"] > scores["R-burdens"]


def test_score_candidates_agrees_with_query(index: BruteForceIndex) -> None:
    ranked = {iri: cosine for iri, _, cosine in index.query("arbitration rules", top_k=99)}
    assert index.score_candidates("arbitration rules", list(ranked)) == pytest.approx(ranked)


def test_score_candidates_with_no_candidates(index: BruteForceIndex) -> None:
    assert index.score_candidates("arbitration", []) == {}


def test_similarity_batch_scores_each_pair_independently(index: BruteForceIndex) -> None:
    pairs = [("arbitration rules", "Arbitration Rules"), ("arbitration rules", "Slovenia")]
    sims = index.similarity_batch(pairs)
    assert len(sims) == 2
    assert sims[0] > sims[1]
    assert sims[0] == pytest.approx(1.0)


def test_similarity_batch_does_not_need_a_built_index() -> None:
    # The reconciler's triage path calls this on a freshly constructed index.
    idx = BruteForceIndex(HashingEmbeddingProvider(dim=64))
    assert idx.similarity_batch([("charge", "Criminal Charge")])[0] > 0
    assert idx.similarity_batch([]) == []


def test_the_index_accepts_any_provider_satisfying_the_protocol() -> None:
    class ConstantProvider:
        def embed(self, text: str) -> list[float]:
            return [1.0, 0.0]

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [self.embed(t) for t in texts]

        def dimension(self) -> int:
            return 2

    idx = BruteForceIndex(ConstantProvider())
    idx.build(["R-a", "R-b"], ["A", "B"], [None, None])
    assert [c for _, _, c in idx.query("anything")] == pytest.approx([1.0, 1.0])


# -- LocalEmbeddingProvider (optional `embedding` extra) -----------------


def test_the_sentence_transformers_import_is_deferred_to_construction() -> None:
    """Importing the module must never pull in sentence-transformers (the core is dep-light)."""
    import sys

    import folio_resolve.embedding as mod

    assert "sentence_transformers" not in sys.modules
    assert hasattr(mod, "LocalEmbeddingProvider")
