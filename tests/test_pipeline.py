"""End-to-end pipeline — the Ch02 regression cases running through filter->expand->rank->judge."""

from __future__ import annotations

import pytest

from folio_resolve import (
    AliasBlocklist,
    BlockedAlias,
    Concept,
    DomainPrior,
    FOLIOEntityRuler,
    InMemoryOntology,
    LabelInfo,
    MatchCandidate,
    MatchPipeline,
    ScoreCalibration,
)
from folio_resolve.embedding import BruteForceIndex, HashingEmbeddingProvider


def test_word_order_invariant_match(ontology: InMemoryOntology) -> None:
    pipe = MatchPipeline(ontology=ontology)
    results = pipe.match("rules of arbitration")
    assert results
    assert results[0].iri == "R-arb-rules"


def test_conjoined_heading_resolves_both_siblings(ontology: InMemoryOntology) -> None:
    # Ch02 unit 12b5e434: the compound heading must yield BOTH sibling concepts.
    pipe = MatchPipeline(ontology=ontology)
    results = pipe.match("Proposed Findings of Fact and Conclusions of Law")
    iris = {r.iri for r in results}
    assert "R-findings" in iris
    assert "R-conclusions" in iris


def test_action_not_auction_blocked(ontology: InMemoryOntology) -> None:
    # Ch02 unit 4b06a90c.
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction")])
    pipe = MatchPipeline(ontology=ontology, blocklist=bl)
    results = pipe.match("Action")
    assert all(r.iri != "R-auction" for r in results)


def test_place_name_not_propagated(ontology: InMemoryOntology) -> None:
    # Ch02 finding 003 / Presumptions -> Northern Mariana Islands @90.
    pipe = MatchPipeline(ontology=ontology)
    results = pipe.match("Presumptions")
    # The place-name gate must keep Mariana Islands from topping the list.
    if results:
        assert results[0].iri != "R-mariana"


def test_metadata_source_excluded(ontology: InMemoryOntology) -> None:
    # Ch02 unit d3c44e2a.
    pipe = MatchPipeline(ontology=ontology)
    assert pipe.match("Cross-Examination", section_label="Copyright Page") == []
    assert pipe.match("Cross-Examination", section_label="Chapter 2") != []


def test_semantic_path_recovers_no_shared_token_map() -> None:
    # Ch02 finding 005: "Presumptions" -> "Litigation Burdens of Proof" (no shared label token).
    ont = InMemoryOntology.__new__(InMemoryOntology)
    from folio_resolve import Concept

    ont = InMemoryOntology(
        [
            Concept(
                iri="R-burdens",
                label="Litigation Burdens of Proof",
                definition="How presumptions allocate the burden of proof at trial.",
                branch="Objectives",
            ),
        ]
    )
    index = BruteForceIndex(HashingEmbeddingProvider())
    index.build(["R-burdens"], ["Litigation Burdens of Proof"], ["How presumptions allocate the burden of proof at trial."])
    pipe = MatchPipeline(ontology=ont, semantic_index=index, score_floor=0.0)
    results = pipe.match("presumptions burden of proof")
    assert any(r.iri == "R-burdens" and r.extraction_path == "semantic" for r in results)


def test_when_the_ruler_and_the_label_search_agree_the_stronger_evidence_wins(
    ontology: InMemoryOntology,
) -> None:
    # Both paths find R-cross-exam; _rank keeps one candidate per IRI, the best-scoring one.
    # A ruler hit is worth 0.72 * 100; an exact label match scores 99.
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(ontology.all_labels())
    results = MatchPipeline(ontology=ontology, entity_ruler=ruler).match("Cross-Examination")
    hits = [r for r in results if r.iri == "R-cross-exam"]
    assert len(hits) == 1
    assert hits[0].extraction_path == "label_search"
    assert hits[0].score == 99.0
    assert hits[0].branch == "Service"  # the concept's branch rides along so gates can see it


def test_the_ruler_reaches_a_concept_the_label_search_scores_too_low(
    ontology: InMemoryOntology,
) -> None:
    # A bare mention inside a long sentence: whole-string label overlap is diluted by every
    # other word, but the ruler's exact-label span is trusted evidence and outranks it.
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(ontology.all_labels())
    text = "Counsel objected during the cross-examination of the plaintiff's treating physician."
    with_ruler = next(
        r
        for r in MatchPipeline(ontology=ontology, entity_ruler=ruler).match(text)
        if r.iri == "R-cross-exam"
    )
    without_ruler = next(
        r for r in MatchPipeline(ontology=ontology).match(text) if r.iri == "R-cross-exam"
    )
    assert without_ruler.extraction_path == "label_search"
    assert with_ruler.extraction_path == "entity_ruler"
    assert with_ruler.score == pytest.approx(72.0)  # the preferred-label confidence tier
    assert with_ruler.score > without_ruler.score


def test_a_ruler_hit_for_an_iri_the_ontology_cannot_resolve_still_reports() -> None:
    # The ruler's patterns and the concept store can drift; the span must not be lost.
    ont = InMemoryOntology([])
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(
        {"deposition": LabelInfo(concept=Concept(iri="R-gone", label="Deposition"), label_type="preferred")}
    )
    results = MatchPipeline(ontology=ont, entity_ruler=ruler, score_floor=0.0).match("deposition")
    assert [(r.iri, r.label, r.branch) for r in results] == [("R-gone", "deposition", "")]


def test_only_the_best_scoring_candidate_per_iri_survives(ontology: InMemoryOntology) -> None:
    pipe = MatchPipeline(ontology=ontology, score_floor=0.0)
    results = pipe.match("Proposed Findings of Fact and Conclusions of Law")
    iris = [r.iri for r in results]
    assert len(iris) == len(set(iris))
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_the_score_floor_drops_weak_candidates(ontology: InMemoryOntology) -> None:
    lenient = MatchPipeline(ontology=ontology, score_floor=0.0).match("arbitration")
    strict = MatchPipeline(ontology=ontology, score_floor=95.0).match("arbitration")
    assert len(strict) < len(lenient)
    assert all(r.score >= 95.0 for r in strict)


def test_gate_decisions_are_reported_on_the_candidate(ontology: InMemoryOntology) -> None:
    pipe = MatchPipeline(ontology=ontology, score_floor=0.0)
    slovenia = next(r for r in pipe.match("Slovenian law") if r.iri == "R-slovenia")
    assert slovenia.gated
    assert "place-name demoted" in slovenia.gate_reason


def test_heading_context_corroborates_the_place_gate(ontology: InMemoryOntology) -> None:
    pipe = MatchPipeline(ontology=ontology, score_floor=0.0)
    without = next(r for r in pipe.match("Slovenian law") if r.iri == "R-slovenia")
    with_heading = next(
        r for r in pipe.match("Slovenian law", heading_terms={"slovenia"}) if r.iri == "R-slovenia"
    )
    assert "place-name demoted" in without.gate_reason
    assert "corroborated-place" in with_heading.gate_reason
    # The short-label gate is independent and still fires on a one-word label, so the
    # candidate stays demoted — two gates, two reasons, both recorded.
    assert with_heading.gated
    assert "short-label demoted" in with_heading.gate_reason


def test_a_candidate_carries_its_calibrated_probability() -> None:
    cal = ScoreCalibration([(50.0, 0.2), (90.0, 0.9)])
    assert MatchCandidate(iri="R1", label="X", score=90.0).as_probability(cal) == 0.9
    assert MatchCandidate(iri="R1", label="X", score=50.0).as_probability(cal) == 0.2


def test_without_a_judge_the_ranked_candidates_pass_through(ontology: InMemoryOntology) -> None:
    pipe = MatchPipeline(ontology=ontology)
    assert pipe.match("rules of arbitration", run_judge=True) == pipe.match("rules of arbitration")


def test_the_judge_only_runs_when_asked(ontology: InMemoryOntology) -> None:
    calls: list[int] = []

    class CountingJudge:
        def complete(self, system: str, user: str) -> str:
            calls.append(1)
            return '{"judged": []}'

    pipe = MatchPipeline(ontology=ontology, judge=CountingJudge())
    pipe.match("Litigation Defenses")
    assert calls == []
    pipe.match("Litigation Defenses", run_judge=True)
    assert calls == [1]


def test_a_rejected_verdict_removes_the_candidate(ontology: InMemoryOntology) -> None:
    class RejectingJudge:
        def complete(self, system: str, user: str) -> str:
            return '{"judged": [{"iri_hash": "R-defenses", "adjusted_score": 0, "verdict": "rejected"}]}'

    pipe = MatchPipeline(ontology=ontology, judge=RejectingJudge())
    assert pipe.match("Litigation Defenses", domain_prior=None, run_judge=True) == []


def test_the_judge_sees_the_full_text_when_one_is_supplied(ontology: InMemoryOntology) -> None:
    captured: dict[str, str] = {}

    class CapturingJudge:
        def complete(self, system: str, user: str) -> str:
            captured["user"] = user
            return '{"judged": []}'

    pipe = MatchPipeline(ontology=ontology, judge=CapturingJudge())
    pipe.match(
        "Defenses",
        full_text="The defenses raised at trial were meritless.",
        run_judge=True,
    )
    assert "meritless" in captured["user"]


def test_best_match_returns_the_top_concept_or_none(ontology: InMemoryOntology) -> None:
    pipe = MatchPipeline(ontology=ontology)
    best = pipe.best_match("rules of arbitration")
    assert best is not None and best.iri == "R-arb-rules"
    assert pipe.best_match("zzzzqqqq nothing here") is None
    # ...and it honors the same keyword arguments as match().
    assert pipe.best_match("Cross-Examination", section_label="Copyright Page") is None


def test_an_unmatchable_term_yields_no_candidates(ontology: InMemoryOntology) -> None:
    assert MatchPipeline(ontology=ontology).match("zzzzqqqq") == []


def test_domain_prior_flows_to_judge(ontology: InMemoryOntology) -> None:
    captured: dict[str, str] = {}

    class FakeJudge:
        def complete(self, system: str, user: str) -> str:
            captured["user"] = user
            return '{"judged": [{"iri_hash": "R-defenses", "adjusted_score": 90, "verdict": "confirmed"}]}'

    prior = DomainPrior.from_manifest_subjects("treatise", [("R-lit", "Litigation")])
    pipe = MatchPipeline(ontology=ontology, judge=FakeJudge())
    results = pipe.match("Litigation Defenses", domain_prior=prior, run_judge=True)
    assert "Litigation" in captured.get("user", "")
    assert results and results[0].iri == "R-defenses"
