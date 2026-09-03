"""Acceptance stories for consumers that use scoring without the full pipeline."""

from __future__ import annotations

import pytest

from folio_resolve import (
    InMemoryOntology,
    MatchPipeline,
    compute_relevance_score,
    content_words,
    generate_search_terms,
)


def test_us_si_01_documented_scores_and_ranking(
    readme_ontology: InMemoryOntology,
) -> None:
    """US-SI-01: README word-order examples keep scores 99.0 and 88.0."""
    exact_query = "arbitration rules"
    reversed_query = "rules of arbitration"
    label = "Arbitration Rules"

    exact_score = compute_relevance_score(content_words(exact_query), exact_query, label)
    reversed_score = compute_relevance_score(
        content_words(reversed_query),
        reversed_query,
        label,
    )

    assert exact_score == 99.0
    assert reversed_score == 88.0

    pipe = MatchPipeline(ontology=readme_ontology)
    for query in (exact_query, reversed_query):
        matches = pipe.match(query)
        assert matches
        assert (matches[0].iri, matches[0].label) == (
            "R-arb-rules",
            "Arbitration Rules",
        )


def test_us_si_02_specificity_penalty_is_weightable() -> None:
    """US-SI-02: full specificity penalty is 67.5 and zero weight raises it."""
    query = "Habitability"
    label = "Breach of Warranty of Habitability"
    query_words = content_words(query)

    full_penalty = compute_relevance_score(
        query_words,
        query,
        label,
        specificity_penalty=1.0,
    )
    no_penalty = compute_relevance_score(
        query_words,
        query,
        label,
        specificity_penalty=0.0,
    )

    assert full_penalty == 67.5
    assert 67.5 < no_penalty <= 100.0


def test_us_si_03_search_terms_are_explicit_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """US-SI-03: litigation expansions are deterministic and do not call the pipeline."""

    def unexpected_pipeline_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("generate_search_terms invoked MatchPipeline.match")

    monkeypatch.setattr(MatchPipeline, "match", unexpected_pipeline_call)

    assert generate_search_terms("litigation") == [
        "litigation",
        "litigation practice",
        "litigation service",
    ]
