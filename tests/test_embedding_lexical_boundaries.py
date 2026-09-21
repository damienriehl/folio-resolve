"""Characterize the real scorer before the isolated boundary experiment."""
import importlib.util
from pathlib import Path

import pytest

from folio_resolve.ontology import Concept, InMemoryOntology
from folio_resolve.scoring import compute_relevance_score, content_words

_SPEC = importlib.util.spec_from_file_location(
    "lexical_boundaries", Path(__file__).parents[1] / "benchmarks/embedding_lexical_boundaries.py"
)
assert _SPEC is not None and _SPEC.loader is not None
lexical = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lexical)


@pytest.mark.parametrize("label,expected", [
    ("Riga", 99.0), ("Riga Stock Exchange", 67.5),
    ("Surigao del Norte", 55.2), ("Surigao del Sur", 55.2),
    ("Water Supply and Irrigation Systems", 55.2),
])
def test_characterization_real_scorer(label, expected):
    assert compute_relevance_score(content_words("Riga"), "Riga", label) == expected


def test_characterization_real_provider():
    concepts = [Concept(iri=str(i), label=label) for i, label in enumerate([
        "Riga", "Riga Stock Exchange", "Surigao del Norte", "Surigao del Sur",
        "Water Supply and Irrigation Systems",
    ])]
    assert [(c.iri, s) for c, s in InMemoryOntology(concepts).search_by_label("Riga")] == [
        ("0", 99.0), ("1", 67.5), ("2", 55.2), ("3", 55.2), ("4", 55.2),
    ]


def test_boundary_contract_removes_embedded_riga_bonus():
    assert lexical.boundary_relevance_score(content_words("Riga"), "Riga", "Surigao del Norte") == 0.0


@pytest.mark.parametrize("text,expected", [
    ("riga", 0), ("RIGA", None), ("(riga)", 1), ("riga-stock", 0),
    ("riga's", 0), ("'riga'", 1), ("1riga", None), ("riga2", None),
    ("_riga", None), ("riga_", None), ("ériga", None), ("riga界", None),
    ("\u0301riga", None), ("riga\u0301", None), ("rigaⅣ", None),
    ("riga²", None), ("surigao riga", 8), ("rigariga riga", 9),
    ("riga\tstock", 0), ("", None),
])
def test_literal_unicode_boundaries(text, expected):
    assert lexical.containment_start(text, "riga") == expected


@pytest.mark.parametrize("text,needle,expected", [
    ("riga stock exchange", "riga stock", 0),
    ("riga  stock exchange", "riga stock", None),
    ("riga-stock exchange", "riga stock", None),
    ("riga-stock exchange", "riga-stock", 0),
    ("é", "e\u0301", None), ("anything", "", None),
    ("riga stockyards", "riga stock", None),
    ("a-a-a", "a-a", 0),
])
def test_phrase_spacing_and_punctuation_are_literal(text, needle, expected):
    assert lexical.containment_start(text, needle) == expected


@pytest.mark.parametrize("field,query,label,kwargs,baseline,treatment", [
    ("label", "Riga", "Surigao", {}, 92.0, 0.0),
    ("reverse_label", "Surigao", "Riga", {}, 88.0, 0.0),
    ("preferred_label", "Riga", "Unknown", {"preferred_label": "Surigao"}, 84.0, 0.0),
    ("definition", "Riga", "Unknown", {"definition": "Surigao"}, 60.0, 0.0),
])
def test_each_containment_branch_and_trace(field, query, label, kwargs, baseline, treatment):
    trace = []
    assert compute_relevance_score(set(), query, label, **kwargs) == baseline
    assert lexical.boundary_relevance_score(set(), query, label, policy="substring", trace=trace, **kwargs) == baseline
    assert len(trace) == 1 and trace[0]["field"] == field
    trace = []
    assert lexical.boundary_relevance_score(set(), query, label, trace=trace, **kwargs) == treatment
    assert trace == []


@pytest.mark.parametrize("field,query,label,kwargs,expected,start", [
    ("label", " RIGA ", "Surigao Riga", {}, 92.0, 8),
    ("reverse_label", "Surigao Riga", "Riga", {}, 88.0, 8),
    ("preferred_label", "RIGA", "Unknown", {"preferred_label": "Surigao Riga"}, 84.0, 8),
    ("definition", "RIGA", "Unknown", {"definition": "Surigao Riga"}, 60.0, 8),
])
def test_scoring_records_later_qualifying_occurrence(field, query, label, kwargs, expected, start):
    trace = []
    assert lexical.boundary_relevance_score(set(), query, label, trace=trace, **kwargs) == expected
    assert trace == [{"field": field, "text": "surigao riga", "needle": "riga",
                      "start": start, "end": start + 4, "bonus": expected}]


def test_traces_only_awards_not_failed_ratio_or_exact_matches():
    for query, label in [("Riga", "Riga"), ("Riga and many other words", "Riga")]:
        trace = []
        lexical.boundary_relevance_score(set(), query, label, trace=trace)
        assert trace == []


def test_surviving_overlap_synonyms_and_definition_weight():
    assert lexical.boundary_relevance_score({"riga"}, "Riga", "Rigatoni") == 70.4
    assert lexical.boundary_relevance_score({"riga"}, "Riga", "Surigao", synonyms=[None, 7, "Riga"]) == 82.0
    assert lexical.boundary_relevance_score({"riga"}, "Riga", "Surigao", definition="Riga", synonyms=["Riga"]) == 89.2
    # Removing pref containment allows the existing overlap branch to run (86 > 84).
    assert lexical.boundary_relevance_score({"riga"}, "Riga", "Unknown", preferred_label="Riga's") == 84.0
    assert lexical.boundary_relevance_score({"riga"}, "Riga", "Unknown", preferred_label="Rigatoni") == 68.8


@pytest.mark.parametrize("label", ["Riga", "Riga Stock Exchange", "Surigao del Norte", "Surigao del Sur", "Water Supply and Irrigation Systems"])
def test_riga_treatment_scores(label):
    expected = {"Riga": 99.0, "Riga Stock Exchange": 67.5}.get(label, 0.0)
    assert lexical.boundary_relevance_score({"riga"}, "Riga", label) == expected


@pytest.mark.parametrize("penalty", [0.0, 0.3, 1.0, 4.0, -1.0])
@pytest.mark.parametrize("query,label,kwargs", [
    ("RIGA", "riga", {}), (" Riga ", "Riga Stock Exchange", {}),
    ("rules of arbitration", "Arbitration Rules", {}),
    ("Habitability", "Breach of Warranty of Habitability", {}),
    ("lease", "tenancy", {"synonyms": ["lease", None, 17], "definition": "A lease"}),
    ("Riga", "Unknown", {"preferred_label": "RIGA"}),
    ("Riga", "Unknown", {"definition": None, "preferred_label": 12}),
    (None, None, {}), (42, 17, {}), (None, "Unrelated", {}),
    ("", "", {}), (" ", " ", {}),
])
def test_unaffected_scores_match_production_exactly(query, label, kwargs, penalty):
    args = (content_words(query), query, label)
    expected = compute_relevance_score(*args, specificity_penalty=penalty, **kwargs)
    assert lexical.boundary_relevance_score(*args, specificity_penalty=penalty, **kwargs) == expected


@pytest.mark.parametrize("query", ["Riga", "Surigao", "", None, 17, "rules of arbitration"])
@pytest.mark.parametrize("label", ["Riga", "Surigao del Norte", "Riga Stock Exchange", "Arbitration Rules", None, 17])
def test_substring_copy_matches_real_scorer_with_all_channels(query, label):
    kwargs = {"definition": "Surigao Riga", "preferred_label": "Surigao", "synonyms": [None, 17, "Riga"]}
    args = (content_words(query), query, label)
    assert lexical.boundary_relevance_score(*args, policy="substring", **kwargs) == compute_relevance_score(*args, **kwargs)


@pytest.mark.parametrize("query", [None, 17, "", "   "])
def test_empty_needle_loses_only_definition_containment(query):
    assert compute_relevance_score(set(), query, "Unknown", definition="Text") == 60.0
    assert lexical.boundary_relevance_score(set(), query, "Unknown", definition="Text") == 0.0


def test_vector_and_invalid_content_entries_preserve_arithmetic():
    for policy in ["substring", "boundary"]:
        kwargs = {"use_vectors": True, "word_similarity": lambda a, b: 0.4,
                  "definition": "Other text", "synonyms": [False, "Different"]}
        args = ({"riga", None, 17}, "Riga", "Unrelated")
        assert lexical.boundary_relevance_score(*args, policy=policy, **kwargs) == compute_relevance_score(*args, **kwargs)


def test_preferred_overlap_can_outscore_removed_containment():
    args = ({"riga"}, "Riga", "Unknown")
    assert compute_relevance_score(*args, preferred_label="Riga_Riga") == 84.0
    trace = []
    assert lexical.boundary_relevance_score(*args, preferred_label="Riga_Riga", trace=trace) == 86.0
    assert trace == []


def test_baseline_export_is_the_real_production_function():
    assert lexical.baseline_relevance_score is compute_relevance_score


@pytest.mark.parametrize("query,label,kwargs", [
    ("cat", "cat person", {}), ("cat person", "cat", {}),
    ("cat", "Unknown", {"preferred_label": "cat person"}),
])
def test_original_minimum_lengths_do_not_award_containment(query, label, kwargs):
    trace = []
    assert lexical.boundary_relevance_score(set(), query, label, trace=trace, **kwargs) == 0.0
    assert trace == []
