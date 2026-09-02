"""Acceptance stories for consumers that reconcile candidates from several paths."""

from __future__ import annotations

from folio_resolve import (
    Concept,
    ConceptMatch,
    FOLIOEntityRuler,
    InMemoryOntology,
    LabelResolver,
    MatchPipeline,
    PlaceNameGate,
    Reconciler,
    decompose,
    load_seed_blocklist,
)

_AUCTION_IRI = "https://folio.openlegalstandard.org/R8kOvHwkY6TrQmB7RnYiWNO"


def test_us_ri_01_action_is_blocked_before_reconciliation() -> None:
    """US-RI-01: the seed veto keeps Action from becoming Auction."""
    ontology = InMemoryOntology(
        [
            Concept(iri=_AUCTION_IRI, label="Auction", branch="Events"),
            Concept(iri="R-action", label="Cause of Action", branch="Objectives"),
        ]
    )
    blocklist = load_seed_blocklist()
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(ontology.all_labels())

    assert blocklist.is_blocked("Action", _AUCTION_IRI)
    assert blocklist.filter_candidates("Action", [(_AUCTION_IRI, 92.3)]) == []
    assert all(
        match.iri != _AUCTION_IRI
        for match in MatchPipeline(
            ontology=ontology,
            entity_ruler=ruler,
            blocklist=blocklist,
        ).match("Action")
    )

    passage = "The Cause of Action was pleaded in the complaint."
    ruler_candidates: list[ConceptMatch] = []
    for hit in ruler.find_matches(passage):
        concept = ontology.get_concept(hit.entity_id)
        assert concept is not None
        ruler_candidates.append(
            ConceptMatch(
                concept_text=hit.text,
                folio_iri=hit.entity_id,
                folio_label=concept.label,
                confidence=hit.confidence,
                branch=concept.branch,
                source="entity_ruler",
            )
        )

    llm_candidates = [
        ConceptMatch(
            concept_text="Cause of Action",
            folio_iri="R-action",
            folio_label="Cause of Action",
            confidence=0.80,
            branch="Objectives",
            source="llm",
        ),
        ConceptMatch(
            concept_text="Action",
            folio_iri=_AUCTION_IRI,
            folio_label="Auction",
            confidence=0.923,
            branch="Events",
            source="llm",
        ),
    ]
    accepted_llm = [
        candidate
        for candidate in llm_candidates
        if not blocklist.is_blocked(candidate.concept_text, candidate.folio_iri)
    ]

    reconciled = Reconciler().reconcile(ruler_candidates, accepted_llm)

    assert all(item.concept.folio_iri != _AUCTION_IRI for item in reconciled)
    assert [
        (item.category, item.concept.folio_iri, item.concept.source) for item in reconciled
    ] == [("both_agree", "R-action", "reconciled")]


def test_us_ri_02_place_name_traps_are_demoted(
    readme_ontology: InMemoryOntology,
) -> None:
    """US-RI-02: default gates demote incidental places but retain exact place names."""
    observable_pipe = MatchPipeline(ontology=readme_ontology, score_floor=0.0)

    slovenian = next(
        match for match in observable_pipe.match("Slovenian law") if match.iri == "R-slovenia"
    )
    exact = next(match for match in observable_pipe.match("Slovenia") if match.iri == "R-slovenia")
    mariana = PlaceNameGate().evaluate(
        query="Presumptions",
        label="Northern Mariana Islands",
        branch="Location",
        score=90.0,
    )

    assert slovenian.gated
    assert "place-name demoted" in slovenian.gate_reason
    assert not exact.gated
    assert "exact-place-name" in exact.gate_reason
    assert mariana.demoted
    assert mariana.score < 90.0
    assert "place-name demoted" in mariana.reason
    assert all(
        match.iri != "R-mariana"
        for match in MatchPipeline(ontology=readme_ontology).match("Presumptions")
    )


def test_us_ri_03_law_does_not_resolve_to_delaware(
    readme_ontology: InMemoryOntology,
) -> None:
    """US-RI-03: resolver policy rejects law to Delaware and carries survivor metadata."""
    delaware = readme_ontology.get_concept("R-delaware")
    assert delaware is not None

    def search(label: str) -> list[tuple[object, float]]:
        if label == "law":
            return [(delaware, 90.0)]
        return list(readme_ontology.search_by_label(label))

    resolver = LabelResolver(search_by_label=search)

    assert resolver.resolve("law") == []
    survivor = resolver.resolve("Arbitration Rules")
    assert len(survivor) == 1
    assert survivor[0].branch == "Service"
    assert 0.0 <= survivor[0].score <= 100.0

    proposed_heading = "Proposed Findings of Fact and Conclusions of Law"
    proposed = resolver.resolve(proposed_heading)
    assert {(item.iri, item.surface) for item in proposed} == {
        ("R-findings", "Proposed Findings of Fact"),
        ("R-conclusions", "Proposed Conclusions of Law"),
    }

    noisy_heading = "Findings of Fact and Conclusions of Law"
    assert decompose(noisy_heading) == [
        "Findings of Fact and Conclusions of Law",
        "Findings of Fact Law",
        "Conclusions of Law",
    ]
    assert resolver.resolve("Findings of Fact Law") == []

    noise_matches = MatchPipeline(ontology=readme_ontology).match("Findings of Fact Law")
    observed = [(item.iri, item.label, item.score) for item in noise_matches]
    assert noise_matches == [], (
        f"README promises the shared-tail noise string produces no tag; observed {observed!r}"
    )
