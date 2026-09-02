from __future__ import annotations

from pathlib import Path

import pytest

from folio_resolve import (
    CalibrationSample,
    Concept,
    InMemoryOntology,
    LabelResolver,
    MatchPipeline,
    OntologyProvider,
    OntologySpec,
    ScoreCalibration,
    SpacyNotInstalledError,
    augment_labels,
    decompose,
    save_lemma_cache,
)
from folio_resolve.spec import OntologyBehavior, OntologyCoords


def test_us_om_01_decompose_the_proposed_heading_example() -> None:
    """US-OM-01 builds a spec-backed ontology and resolves the proposed siblings."""
    expected = [
        "Proposed Findings of Fact and Conclusions of Law",
        "Proposed Findings of Fact",
        "Proposed Conclusions of Law",
    ]
    spec = OntologySpec(
        id="synthetic-uat",
        display_name="Synthetic UAT Ontology",
        base_iri="https://example.test/uat/",
        coords=OntologyCoords(source_type="http", owl_url="https://example.test/uat.owl"),
        behavior=OntologyBehavior(concept_exclude_prefixes=("Retired:",)),
    )
    authored_concepts = [
        Concept(
            iri=f"{spec.base_iri}findings",
            label="Proposed Findings of Fact",
            branch="Document Artifacts",
        ),
        Concept(
            iri=f"{spec.base_iri}conclusions",
            label="Proposed Conclusions of Law",
            branch="Document Artifacts",
        ),
        Concept(
            iri=f"{spec.base_iri}retired",
            label="Retired: Old Heading",
            branch="Document Artifacts",
        ),
    ]
    ontology = InMemoryOntology(
        [
            concept
            for concept in authored_concepts
            if not spec.behavior.excludes_concept_label(concept.label)
        ]
    )

    decomposed = decompose("Proposed Findings of Fact and Conclusions of Law")

    def search(label: str) -> list[tuple[object, float]]:
        return list(ontology.search_by_label(label))

    resolved = LabelResolver(search).resolve(decomposed[0])

    calibration = ScoreCalibration.fit(
        [
            CalibrationSample(25.0, "wrong"),
            CalibrationSample(60.0, "weak"),
            CalibrationSample(95.0, "correct"),
        ]
    )
    scores = [25.0, 60.0, 95.0]
    probabilities = [calibration.probability(score) for score in scores]

    assert decomposed == expected
    assert [result.iri for result in resolved] == [
        f"{spec.base_iri}findings",
        f"{spec.base_iri}conclusions",
    ]
    assert len({result.iri for result in resolved}) == 2
    assert probabilities == sorted(probabilities)
    assert [calibration.band(score) for score in scores] == ["wrong", "weak", "strong"]


def test_us_om_02_expand_a_genuine_shared_tail(
    readme_ontology: InMemoryOntology,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """US-OM-02 resolves a shared tail and exercises skipped and cached lemmas."""
    expected = [
        "Antitrust and Securities Law",
        "Antitrust Law",
        "Securities Law",
    ]
    labels = readme_ontology.all_labels()

    def missing_spacy(*_args: object, **_kwargs: object) -> object:
        raise SpacyNotInstalledError("synthetic missing spaCy extra")

    monkeypatch.setattr("folio_resolve.lemma.spacy_lemmatizer", missing_spacy)
    unchanged = augment_labels(labels, on_missing_spacy="skip")

    save_lemma_cache(tmp_path, "synthetic-ontology", {"agreements": "agreement"})
    cached = augment_labels(
        labels,
        cache_dir=tmp_path,
        ontology_hash="synthetic-ontology",
        on_missing_spacy="skip",
    )

    decomposed = decompose("Antitrust and Securities Law")
    resolved = [
        readme_ontology.search_by_label(label, limit=1)[0][0].label for label in decomposed[1:]
    ]

    assert decomposed == expected
    assert resolved == ["Antitrust Law", "Securities Law"]
    assert unchanged == labels
    assert cached["agreement"].concept.label == "Agreements"
    assert cached["agreement"].label_type == "lemma_preferred"


def test_us_om_03_expose_the_documented_shared_tail_over_fire(
    readme_ontology: InMemoryOntology,
    request: pytest.FixtureRequest,
) -> None:
    """US-OM-03 pins the over-fire and gates the same check on the real ontology."""
    expected = [
        "Findings of Fact and Conclusions of Law",
        "Findings of Fact Law",
        "Conclusions of Law",
    ]
    decomposed = decompose("Findings of Fact and Conclusions of Law")

    assert decomposed == expected

    pipeline = MatchPipeline(ontology=readme_ontology)
    sibling_matches = pipeline.match(decomposed[0])
    sibling_iris = {item.iri for item in sibling_matches if item.extraction_path == "decomposition"}
    conclusions_iris = {item.iri for item in sibling_matches if "Conclusions of Law" in item.label}
    noise_matches = pipeline.match("Findings of Fact Law")
    noise_iris = {item.iri for item in noise_matches}

    assert noise_iris <= sibling_iris
    assert noise_iris.isdisjoint(conclusions_iris)
    assert all(not item.gated for item in noise_matches)

    real_ontology = request.getfixturevalue("real_ontology")
    assert isinstance(real_ontology, OntologyProvider)
    real_labels = real_ontology.all_labels()
    assert set(real_labels) <= set(augment_labels(real_labels, on_missing_spacy="skip"))
