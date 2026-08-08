"""Multi-tag domain prior — suggest / validate / invalidate / add, and the judge context.

Ch02 finding 002 as Damien amended it: a corpus carries MULTIPLE subject tags; the library
suggests them with confidence, a human validates or adds, and only *active* tags reach the judge.
Corpus names and concepts here are synthetic.
"""

from __future__ import annotations

from folio_resolve import Concept, DomainPrior, DomainPriorSuggester, InMemoryOntology, TaxonomyNode
from folio_resolve.domain_prior import SubjectTag, TagStatus

# -- SubjectTag ----------------------------------------------------------


def test_only_validated_and_added_tags_are_active() -> None:
    active = {TagStatus.VALIDATED, TagStatus.ADDED}
    for status in TagStatus:
        tag = SubjectTag(iri="R1", label="Litigation", status=status)
        assert tag.is_active is (status in active), status


def test_tag_status_wire_values_are_stable() -> None:
    # Persisted in corpus manifests by consumers.
    assert [s.value for s in TagStatus] == ["suggested", "validated", "invalidated", "added"]


# -- DomainPrior lifecycle -----------------------------------------------


def test_a_fresh_prior_is_empty() -> None:
    prior = DomainPrior(corpus_name="c")
    assert prior.tags == []
    assert prior.active_tags() == []
    assert prior.as_judge_context() == ""


def test_add_marks_a_tag_added_and_active() -> None:
    prior = DomainPrior(corpus_name="c")
    tag = prior.add("R-pi", "Personal Injury")
    assert tag.status == TagStatus.ADDED
    assert tag.source == "human"
    assert prior.active_tags() == [tag]


def test_adding_an_existing_iri_promotes_it_in_place() -> None:
    prior = DomainPrior(corpus_name="c")
    prior.merge_suggestions([SubjectTag(iri="R-x", label="Contract Law", confidence=0.9)])
    promoted = prior.add("R-x", "Contract Law", source="human")
    assert len(prior.tags) == 1  # no duplicate row
    assert promoted is prior.tags[0]
    assert promoted.status == TagStatus.ADDED
    assert promoted.confidence == 0.9  # the suggester's confidence is preserved


def test_a_human_can_re_add_a_tag_they_previously_invalidated() -> None:
    prior = DomainPrior(corpus_name="c")
    prior.add("R-x", "Contract Law")
    prior.invalidate("R-x")
    assert prior.active_tags() == []
    prior.add("R-x", "Contract Law")
    assert [t.label for t in prior.active_tags()] == ["Contract Law"]


def test_validate_and_invalidate_flip_activity() -> None:
    prior = DomainPrior(corpus_name="c")
    prior.merge_suggestions([SubjectTag(iri="R-x", label="Contract Law", confidence=0.9)])
    assert prior.active_tags() == []  # a suggestion does not flow until a human says so
    assert prior.validate("R-x") is prior.tags[0]
    assert prior.active_tags()[0].label == "Contract Law"
    assert prior.invalidate("R-x") is prior.tags[0]
    assert prior.active_tags() == []


def test_validating_an_unknown_iri_is_a_no_op_returning_none() -> None:
    prior = DomainPrior(corpus_name="c")
    assert prior.validate("R-nope") is None
    assert prior.invalidate("R-nope") is None
    assert prior.tags == []


def test_merge_suggestions_never_overwrites_a_human_decision() -> None:
    prior = DomainPrior(corpus_name="c")
    prior.add("R-x", "Contract Law")
    prior.invalidate("R-x")
    prior.merge_suggestions([SubjectTag(iri="R-x", label="Contract Law", confidence=0.95)])
    assert len(prior.tags) == 1
    assert prior.tags[0].status == TagStatus.INVALIDATED  # the rejection stands


def test_merge_suggestions_appends_only_new_iris() -> None:
    prior = DomainPrior(corpus_name="c")
    prior.merge_suggestions([SubjectTag(iri="R-a", label="A"), SubjectTag(iri="R-b", label="B")])
    prior.merge_suggestions([SubjectTag(iri="R-b", label="B"), SubjectTag(iri="R-c", label="C")])
    assert [t.iri for t in prior.tags] == ["R-a", "R-b", "R-c"]


# -- judge context -------------------------------------------------------


def test_the_judge_context_joins_multiple_active_tags() -> None:
    prior = DomainPrior(corpus_name="Personal Injury Depositions")
    prior.add("R-pi", "Personal Injury")
    prior.add("R-depo", "Deposition")
    assert prior.as_judge_context() == "Personal Injury / Deposition"


def test_a_single_active_tag_is_rendered_bare() -> None:
    prior = DomainPrior(corpus_name="c")
    prior.add("R-lit", "Litigation")
    assert prior.as_judge_context() == "Litigation"


def test_inactive_tags_never_reach_the_judge() -> None:
    prior = DomainPrior(corpus_name="c")
    prior.merge_suggestions([SubjectTag(iri="R-x", label="Bankruptcy")])
    prior.add("R-lit", "Litigation")
    prior.invalidate("R-lit")
    assert prior.as_judge_context() == ""


def test_from_manifest_subjects_marks_everything_active() -> None:
    prior = DomainPrior.from_manifest_subjects(
        "treatise", [("R-lit", "Litigation"), ("R-trial", "Trial Advocacy")]
    )
    assert prior.corpus_name == "treatise"
    assert all(t.status == TagStatus.ADDED and t.source == "manifest" for t in prior.tags)
    assert prior.as_judge_context() == "Litigation / Trial Advocacy"


def test_from_manifest_subjects_with_no_subjects() -> None:
    prior = DomainPrior.from_manifest_subjects("treatise", [])
    assert prior.tags == []
    assert prior.as_judge_context() == ""


# -- TaxonomyNode (the type-ahead picker's tree model) -------------------


def test_taxonomy_node_serializes_recursively() -> None:
    tree = TaxonomyNode(
        iri="R-root",
        label="Objectives",
        children=[
            TaxonomyNode(iri="R-a", label="Defenses", parent_iri="R-root"),
            TaxonomyNode(
                iri="R-b",
                label="Claims",
                definition="A demand for relief.",
                parent_iri="R-root",
                children=[TaxonomyNode(iri="R-b1", label="Antitrust Claims", parent_iri="R-b")],
            ),
        ],
    )
    assert tree.to_dict() == {
        "iri": "R-root",
        "label": "Objectives",
        "definition": None,
        "parent_iri": None,
        "children": [
            {"iri": "R-a", "label": "Defenses", "definition": None, "parent_iri": "R-root", "children": []},
            {
                "iri": "R-b",
                "label": "Claims",
                "definition": "A demand for relief.",
                "parent_iri": "R-root",
                "children": [
                    {
                        "iri": "R-b1",
                        "label": "Antitrust Claims",
                        "definition": None,
                        "parent_iri": "R-b",
                        "children": [],
                    }
                ],
            },
        ],
    }


def test_taxonomy_nodes_do_not_share_a_children_list() -> None:
    a, b = TaxonomyNode(iri="R-a", label="A"), TaxonomyNode(iri="R-b", label="B")
    a.children.append(TaxonomyNode(iri="R-c", label="C"))
    assert b.children == []


# -- DomainPriorSuggester ------------------------------------------------


def test_suggester_finds_the_corpus_subject_from_a_title(ontology: InMemoryOntology) -> None:
    suggester = DomainPriorSuggester(ontology, min_score=70.0)
    suggestions = suggester.suggest(title="Litigation Defenses treatise")
    assert any(s.label == "Litigation Defenses" for s in suggestions)
    assert all(s.status == TagStatus.SUGGESTED and s.source == "suggester" for s in suggestions)


def test_suggestions_are_ranked_by_confidence_and_capped(ontology: InMemoryOntology) -> None:
    suggester = DomainPriorSuggester(ontology, max_suggestions=2, min_score=0.0)
    suggestions = suggester.suggest(
        title="Litigation Defenses", headings=["Arbitration Rules", "Cross-Examination"]
    )
    assert len(suggestions) <= 2
    assert [s.confidence for s in suggestions] == sorted(
        (s.confidence for s in suggestions), reverse=True
    )


def test_confidence_is_the_score_on_a_0_1_scale(ontology: InMemoryOntology) -> None:
    suggester = DomainPriorSuggester(ontology, min_score=70.0)
    for s in suggester.suggest(title="Arbitration Rules"):
        assert 0.0 <= s.confidence <= 1.0


def test_min_score_gates_weak_matches(ontology: InMemoryOntology) -> None:
    assert DomainPriorSuggester(ontology, min_score=100.0).suggest(title="Arbitration Rules") == []


def test_each_concept_is_suggested_once_at_its_best_confidence(ontology: InMemoryOntology) -> None:
    suggester = DomainPriorSuggester(ontology, min_score=0.0)
    suggestions = suggester.suggest(
        title="Arbitration Rules", headings=["arbitration", "Arbitration Rules"]
    )
    iris = [s.iri for s in suggestions]
    assert len(iris) == len(set(iris))
    best = next(s for s in suggestions if s.iri == "R-arb-rules")
    assert best.confidence == 0.99  # the whole-title exact match, not the weaker heading


def test_headings_and_sample_text_are_accepted(ontology: InMemoryOntology) -> None:
    suggester = DomainPriorSuggester(ontology, min_score=70.0)
    from_headings = suggester.suggest(headings=["Litigation Defenses"])
    assert any(s.iri == "R-defenses" for s in from_headings)
    # sample_text is part of the signature but contributes no phrases on its own.
    assert suggester.suggest(sample_text="Litigation Defenses") == []


def test_nothing_in_means_nothing_out(ontology: InMemoryOntology) -> None:
    suggester = DomainPriorSuggester(ontology)
    assert suggester.suggest() == []
    assert suggester.suggest(title="   ", headings=["", "  "]) == []


def test_title_bigrams_reach_concepts_the_whole_title_misses() -> None:
    # "Personal Injury Depositions" -> Personal Injury + Deposition, Damien's worked example.
    ont = InMemoryOntology(
        [
            Concept(iri="R-pi", label="Personal Injury", branch="Area of Law"),
            Concept(iri="R-depo", label="Depositions", branch="Service"),
        ]
    )
    suggestions = DomainPriorSuggester(ont, min_score=70.0).suggest(
        title="Personal Injury Depositions"
    )
    assert {s.iri for s in suggestions} == {"R-pi", "R-depo"}


def test_stopword_only_titles_produce_no_phrases(ontology: InMemoryOntology) -> None:
    assert DomainPriorSuggester(ontology, min_score=0.0).suggest(title="of the and") == []


def test_suggestions_feed_straight_into_a_prior(ontology: InMemoryOntology) -> None:
    prior = DomainPrior(corpus_name="treatise")
    prior.merge_suggestions(
        DomainPriorSuggester(ontology, min_score=70.0).suggest(title="Litigation Defenses")
    )
    assert prior.active_tags() == []
    prior.validate("R-defenses")
    assert prior.as_judge_context() == "Litigation Defenses"
