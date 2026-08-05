"""Entity-ruler pattern building — the stopword / minimum-length guards and the id encoding.

Ports folio-enrich's pattern builder onto the pure-Python Aho-Corasick engine. The guards are
what stop an 18k-label ontology from tagging every "the" and "of" in a document, and the
``iri|label_type`` id encoding is what carries the confidence tier through the automaton.
Concepts are synthetic.
"""

from __future__ import annotations

import pytest

from folio_resolve import Concept, FOLIOEntityRuler, InMemoryOntology, LabelInfo
from folio_resolve.entity_ruler import (
    MIN_PATTERN_LENGTH,
    build_patterns,
    decode_pattern_id,
    encode_pattern_id,
)


def _labels(**by_label: tuple[str, str]) -> dict[str, LabelInfo]:
    """``label -> (iri, label_type)`` as a LabelInfo map."""
    return {
        label: LabelInfo(concept=Concept(iri=iri, label=label), label_type=label_type)
        for label, (iri, label_type) in by_label.items()
    }


# -- pattern id encoding -------------------------------------------------


def test_pattern_ids_round_trip() -> None:
    encoded = encode_pattern_id("https://folio.openlegalstandard.org/R1", "preferred")
    assert decode_pattern_id(encoded) == ("https://folio.openlegalstandard.org/R1", "preferred")


def test_decoding_splits_on_the_last_separator_so_iris_may_contain_it() -> None:
    assert decode_pattern_id("R1|weird|alternative") == ("R1|weird", "alternative")


def test_an_unencoded_id_decodes_to_an_unknown_label_type() -> None:
    assert decode_pattern_id("R1") == ("R1", "unknown")


# -- build_patterns guards -----------------------------------------------


def test_patterns_carry_the_encoded_id_and_the_label_type() -> None:
    patterns = build_patterns(_labels(deposition=("R-depo", "preferred")))
    assert patterns["deposition"] == {"id": "R-depo|preferred", "label_type": "preferred"}


def test_labels_shorter_than_the_minimum_are_dropped() -> None:
    assert MIN_PATTERN_LENGTH == 3
    patterns = build_patterns(
        _labels(ab=("R-ab", "preferred"), abc=("R-abc", "preferred"), abcd=("R-abcd", "preferred"))
    )
    assert set(patterns) == {"abc", "abcd"}


def test_stopword_labels_are_dropped() -> None:
    # An ontology really does label concepts "Act", "Use", "Set" — matching those everywhere
    # would bury every real hit.
    patterns = build_patterns(
        _labels(
            **{
                "the": ("R-the", "alternative"),
                "may": ("R-may", "alternative"),
                "etc": ("R-etc", "alternative"),
                "deposition": ("R-depo", "preferred"),
            }
        )
    )
    assert set(patterns) == {"deposition"}


def test_blank_labels_are_dropped() -> None:
    assert build_patterns({"": LabelInfo(concept=Concept(iri="R1", label=""), label_type="preferred")}) == {}


def test_the_first_entry_for_a_label_wins() -> None:
    # dict keys are unique, so this pins the "already present" guard against a future
    # case-folding or normalization step that could collide two keys.
    patterns = build_patterns(_labels(hearing=("R-hearing", "preferred")))
    assert patterns["hearing"]["id"] == "R-hearing|preferred"


def test_building_from_no_labels() -> None:
    assert build_patterns({}) == {}


# -- FOLIOEntityRuler ----------------------------------------------------


def test_an_unloaded_ruler_matches_nothing_rather_than_raising() -> None:
    ruler = FOLIOEntityRuler()
    assert ruler.pattern_count == 0
    assert ruler.find_matches("The deposition was taken.") == []


def test_pattern_count_reflects_the_guards() -> None:
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(
        _labels(
            deposition=("R-depo", "preferred"),
            **{"the": ("R-the", "alternative"), "ab": ("R-ab", "preferred")},
        )
    )
    assert ruler.pattern_count == 1  # stopword and too-short both dropped


def test_reloading_replaces_the_previous_patterns() -> None:
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(_labels(deposition=("R-depo", "preferred")))
    ruler.load_patterns(_labels(hearing=("R-hearing", "preferred")))
    assert ruler.pattern_count == 1
    assert ruler.find_matches("The deposition was taken.") == []
    assert [m.entity_id for m in ruler.find_matches("The hearing began.")] == ["R-hearing"]


def test_matches_carry_offsets_that_index_the_original_text() -> None:
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(_labels(**{"cross-examination": ("R-cross", "preferred")}))
    text = "The CROSS-EXAMINATION was brutal."
    match = ruler.find_matches(text)[0]
    assert text[match.start_char : match.end_char] == "CROSS-EXAMINATION"
    assert match.text == "CROSS-EXAMINATION"  # as written, not as registered
    assert match.entity_id == "R-cross"


@pytest.mark.parametrize(
    ("label_type", "confidence"),
    [
        ("preferred", 0.72),
        ("lemma_preferred", 0.72),  # a preferred label's lemma is preferred-grade evidence
        ("alternative", 0.55),
        ("lemma_alternative", 0.55),
        ("hidden", 0.55),
        ("unknown", 0.55),
    ],
)
def test_confidence_tiers_by_label_type(label_type: str, confidence: float) -> None:
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(_labels(deposition=("R-depo", label_type)))
    assert ruler.find_matches("A deposition today.")[0].confidence == pytest.approx(confidence)
    assert ruler.find_matches("A deposition today.")[0].match_type == label_type


def test_the_ruler_consumes_an_ontology_label_map_directly() -> None:
    ont = InMemoryOntology(
        [Concept(iri="R-cross", label="Cross-Examination", alternative_labels=("Cross Exam",))]
    )
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(ont.all_labels())
    matches = ruler.find_matches("cross exam then cross-examination")
    # Both surface forms fire, tagged with the tier of the label that matched, at one concept.
    assert [(m.text, m.match_type) for m in matches] == [
        ("cross exam", "alternative"),
        ("cross-examination", "preferred"),
    ]
    assert {m.entity_id for m in matches} == {"R-cross"}
