"""Acceptance stories for consumers of the composed matching pipeline."""

from __future__ import annotations

from folio_resolve import (
    Concept,
    DomainPrior,
    InMemoryOntology,
    LabelResolver,
    MatchPipeline,
    SourceClassifier,
    SourceType,
    decompose,
)


def test_us_pi_01_quick_start_matches(readme_ontology: InMemoryOntology) -> None:
    """US-PI-01: the README quick start resolves Arbitration Rules first."""
    ontology = InMemoryOntology(
        [
            Concept(iri="R-defenses", label="Litigation Defenses", branch="Objectives"),
            Concept(iri="R-arb", label="Arbitration Rules", branch="Service"),
        ]
    )
    pipe = MatchPipeline(ontology=ontology)

    matches = pipe.match("rules of arbitration")

    assert matches
    assert (matches[0].iri, matches[0].label) == ("R-arb", "Arbitration Rules")

    heading = "Proposed Findings of Fact and Conclusions of Law"
    assert decompose(heading) == [
        "Proposed Findings of Fact and Conclusions of Law",
        "Proposed Findings of Fact",
        "Proposed Conclusions of Law",
    ]

    def search(label: str) -> list[tuple[object, float]]:
        return [(concept, score) for concept, score in readme_ontology.search_by_label(label)]

    resolved = LabelResolver(search).resolve(heading)
    assert {(item.iri, item.surface) for item in resolved} == {
        ("R-findings", "Proposed Findings of Fact"),
        ("R-conclusions", "Proposed Conclusions of Law"),
    }


def test_us_pi_02_domain_prior_threads_without_a_judge(
    readme_ontology: InMemoryOntology,
) -> None:
    """US-PI-02: a Litigation domain prior threads through the offline pipeline."""
    prior = DomainPrior.from_manifest_subjects("treatise", [("R-lit", "Litigation")])
    pipe = MatchPipeline(ontology=readme_ontology)

    matches = pipe.match("Defenses", domain_prior=prior)

    assert prior.as_judge_context() == "Litigation"
    assert [(tag.iri, tag.label) for tag in prior.active_tags()] == [("R-lit", "Litigation")]
    assert matches
    assert (matches[0].iri, matches[0].label) == (
        "R-defenses",
        "Litigation Defenses",
    )


def test_us_pi_03_copyright_source_is_excluded(
    readme_ontology: InMemoryOntology,
) -> None:
    """US-PI-03: a copyright-page source is non-substantive and never matched."""
    classifier = SourceClassifier()
    section_label = "Copyright Page"
    source_text = "Arbitration Rules"

    assert classifier.source_type(section_label, source_text) is SourceType.FRONT_MATTER
    assert not classifier.is_taggable(section_label, source_text)
    assert (
        MatchPipeline(ontology=readme_ontology).match(
            source_text,
            section_label=section_label,
        )
        == []
    )
