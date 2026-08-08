"""Regression tests for the new v0 capabilities — each pins a recorded Ch02 failure."""

from __future__ import annotations

from folio_resolve import (
    AliasBlocklist,
    BlockedAlias,
    CalibrationSample,
    DomainPrior,
    DomainPriorSuggester,
    PlaceNameGate,
    ScoreCalibration,
    ShortLabelGate,
    SourceClassifier,
    SourceType,
    decompose,
)
from folio_resolve.domain_prior import TagStatus

# -- decompose (unit 12b5e434 / finding 005) -----------------------------


def test_decompose_conjoined_compound_with_shared_head() -> None:
    parts = decompose("Proposed Findings of Fact and Conclusions of Law")
    assert "Proposed Findings of Fact" in parts
    assert "Proposed Conclusions of Law" in parts  # shared head "Proposed" restored


def test_decompose_simple_conjunction() -> None:
    assert decompose("Arbitration and Mediation") == [
        "Arbitration and Mediation",
        "Arbitration",
        "Mediation",
    ]


def test_decompose_no_conjunction_returns_original() -> None:
    assert decompose("Cross-Examination") == ["Cross-Examination"]


# -- place-name gate (finding 003) ---------------------------------------


def test_place_name_demoted_without_corroboration() -> None:
    gate = PlaceNameGate(min_signals=2)
    d = gate.evaluate(query="Presumptions", label="Northern Mariana Islands", branch="Location", score=90.0)
    assert d.demoted
    assert d.score <= 40.0


def test_agency_homonym_demoted_without_corroboration() -> None:
    # Ch02 agency homonym: "effect of answers" -> Federal Election Commission (Governmental Body).
    # The gate now governs the governmental-body branch too, so it vetoes the mis-map.
    gate = PlaceNameGate(min_signals=2)
    d = gate.evaluate(
        query="effect of answers",
        label="Federal Election Commission",
        branch="Governmental Body",
        score=90.0,
    )
    assert d.demoted
    assert d.score <= 40.0


def test_place_name_allowed_when_exact() -> None:
    gate = PlaceNameGate()
    d = gate.evaluate(query="Slovenia", label="Slovenia", branch="Location", score=99.0)
    assert not d.demoted


def test_place_name_allowed_with_heading_context() -> None:
    gate = PlaceNameGate(min_signals=2)
    d = gate.evaluate(
        query="Slovenia jurisdiction",
        label="Slovenia",
        branch="Location",
        score=88.0,
        heading_context_match=True,
        corroborating_signals=1,
    )
    assert not d.demoted


# -- place-name gate: consumer-supplied vocabulary (v0.3.0) --------------


def test_consumer_place_is_invisible_to_the_default_gate() -> None:
    # The built-in token set is deliberately small. A consumer's own recorded over-scoring
    # place (alea-intake's BUG-21 log carries these) is NOT demoted by an un-parameterized gate.
    gate = PlaceNameGate(min_signals=2)
    assert not gate.evaluate(
        query="wrongful termination", label="Macedonia", branch="", score=90.0
    ).demoted


def test_extra_tokens_demote_a_consumer_place() -> None:
    gate = PlaceNameGate(min_signals=2, extra_tokens={"macedonia", "rize"})
    for label in ("Macedonia", "Rize"):
        d = gate.evaluate(query="unauthorized employment", label=label, branch="", score=90.0)
        assert d.demoted, label
        assert d.score <= 40.0


def test_extra_tokens_match_multi_word_names() -> None:
    gate = PlaceNameGate(min_signals=2, extra_tokens={"north america", "europe"})
    assert gate.evaluate(query="patent claim", label="North America", branch="", score=88.0).demoted
    assert gate.evaluate(query="patent claim", label="Europe", branch="", score=88.0).demoted


def test_extra_markers_catch_productive_place_phrasings() -> None:
    # "City of X" / "Republic of X" arrive with no branch metadata from embedding backends.
    gate = PlaceNameGate(min_signals=2, extra_markers=("city of", "republic of"))
    assert gate.evaluate(query="habitability", label="City of Exampleton", branch="", score=90.0).demoted
    assert gate.evaluate(query="habitability", label="Republic of Example", branch="", score=90.0).demoted
    assert not gate.evaluate(query="habitability", label="Warranty of Habitability", branch="Objectives", score=90.0).demoted


def test_extra_branch_markers_extend_the_governed_branches() -> None:
    gate = PlaceNameGate(min_signals=2, extra_branch_markers=("municipality",))
    assert gate.evaluate(query="retaliation", label="Exampleton", branch="Municipality", score=90.0).demoted
    assert not PlaceNameGate(min_signals=2).evaluate(
        query="retaliation", label="Exampleton", branch="Municipality", score=90.0
    ).demoted


def test_extra_vocabulary_is_case_insensitive_and_blank_safe() -> None:
    gate = PlaceNameGate(min_signals=2, extra_tokens=["  MACEDONIA ", "", "  "], extra_markers=["  City Of  "])
    assert gate.evaluate(query="claim", label="macedonia", branch="", score=90.0).demoted
    assert gate.evaluate(query="claim", label="CITY OF EXAMPLETON", branch="", score=90.0).demoted


def test_extras_do_not_disturb_the_built_in_behavior() -> None:
    gate = PlaceNameGate(min_signals=2, extra_tokens={"macedonia"})
    # built-in token still governed
    assert gate.evaluate(query="Presumptions", label="Slovenia", branch="Location", score=90.0).demoted
    # non-place still passes through untouched
    d = gate.evaluate(query="Defenses", label="Litigation Defenses", branch="Objectives", score=90.0)
    assert not d.demoted and d.reason == "not-a-place" and d.score == 90.0
    # the exact-name escape hatch still fires for an extra token
    assert not gate.evaluate(query="Macedonia", label="Macedonia", branch="", score=99.0).demoted


def test_claim_fitness_usage_ignores_the_exact_name_escape() -> None:
    # alea-intake's shape: empty query + zero corroboration answers the pure question
    # "is this concept in the place class?" — a claim named exactly "Macedonia" is still not a claim.
    gate = PlaceNameGate(min_signals=2, extra_tokens={"macedonia"})
    assert gate.evaluate(query="", label="Macedonia", branch="", score=100.0, corroborating_signals=0).demoted


def test_place_tokens_property_exposes_the_merged_vocabulary() -> None:
    gate = PlaceNameGate(extra_tokens={"macedonia"})
    assert "macedonia" in gate.place_tokens
    assert "slovenia" in gate.place_tokens
    assert "macedonia" not in PlaceNameGate().place_tokens


# -- short-label gate ----------------------------------------------------


def test_short_label_fuzzy_demoted() -> None:
    gate = ShortLabelGate()
    d = gate.evaluate(query="law of the sea", label="law", score=88.0)
    assert d.demoted


def test_short_label_near_exact_allowed() -> None:
    gate = ShortLabelGate()
    d = gate.evaluate(query="tax", label="Tax", score=99.0)
    assert not d.demoted


# -- alias blocklist (unit 4b06a90c: Action != Auction) ------------------


def test_blocklist_blocks_action_auction() -> None:
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction", reason="Action != Auction")])
    assert bl.is_blocked("Action", "R-auction")
    assert bl.is_blocked("action", "R-auction")  # case-insensitive
    assert not bl.is_blocked("Action", "R-cause-of-action")


def test_blocklist_filters_candidates() -> None:
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction")])
    survivors = bl.filter_candidates("Action", [("R-auction", 90.0), ("R-cause", 85.0)])
    assert survivors == [("R-cause", 85.0)]


def test_blocklist_domain_scoped() -> None:
    bl = AliasBlocklist([BlockedAlias("charge", "R-encumbrance", domain="criminal")])
    assert bl.is_blocked("charge", "R-encumbrance", domains=["criminal"])
    assert not bl.is_blocked("charge", "R-encumbrance", domains=["property"])


def test_blocklist_roundtrip(tmp_path: object) -> None:
    from pathlib import Path

    p = Path(str(tmp_path)) / "bl.json"
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction", reason="x")])
    bl.save(p)
    loaded = AliasBlocklist.load(p)
    assert loaded.is_blocked("Action", "R-auction")


# -- source exclusion (unit d3c44e2a) ------------------------------------


def test_metadata_source_excluded() -> None:
    sc = SourceClassifier()
    assert sc.source_type("Copyright", "ISBN 978-0-13-468599-1") == SourceType.FRONT_MATTER
    assert not sc.is_taggable("Copyright Page")
    assert not sc.is_taggable("Document Metadata")


def test_body_source_taggable() -> None:
    sc = SourceClassifier()
    assert sc.is_taggable("Chapter 2: Cross-Examination")


# -- multi-tag domain prior (Damien's amended design) --------------------


def test_domain_prior_multi_tag_lifecycle() -> None:
    prior = DomainPrior(corpus_name="Personal Injury Depositions")
    prior.add("R-pi", "Personal Injury")
    prior.add("R-depo", "Deposition")
    assert {t.label for t in prior.active_tags()} == {"Personal Injury", "Deposition"}
    assert "Personal Injury" in prior.as_judge_context()
    assert "Deposition" in prior.as_judge_context()


def test_domain_prior_validate_invalidate() -> None:
    prior = DomainPrior(corpus_name="c")
    from folio_resolve.domain_prior import SubjectTag

    prior.merge_suggestions([SubjectTag(iri="R-x", label="Contract Law", confidence=0.9)])
    assert prior.tags[0].status == TagStatus.SUGGESTED
    assert prior.active_tags() == []  # suggestions do not flow until validated
    prior.validate("R-x")
    assert prior.active_tags()[0].label == "Contract Law"
    prior.invalidate("R-x")
    assert prior.active_tags() == []


def test_domain_prior_suggester(ontology: object) -> None:
    from folio_resolve import InMemoryOntology

    assert isinstance(ontology, InMemoryOntology)
    suggester = DomainPriorSuggester(ontology, min_score=70.0)
    suggestions = suggester.suggest(title="Litigation Defenses treatise")
    assert any(s.label == "Litigation Defenses" for s in suggestions)
    assert all(s.status == TagStatus.SUGGESTED for s in suggestions)


# -- calibration (finding 004: weak-band recalibration) ------------------


def test_calibration_monotone_and_bands() -> None:
    samples = [
        CalibrationSample(30, "wrong"),
        CalibrationSample(50, "wrong"),
        CalibrationSample(60, "weak"),
        CalibrationSample(75, "weak"),
        CalibrationSample(85, "correct"),
        CalibrationSample(95, "correct"),
    ]
    cal = ScoreCalibration.fit(samples)
    assert cal.probability(30) <= cal.probability(95)
    assert cal.band(95) == "strong"
    assert cal.band(30) == "wrong"


def test_calibration_empty_falls_back_to_linear() -> None:
    cal = ScoreCalibration()
    assert cal.probability(50) == 0.5


def test_a_place_token_is_governed_even_with_no_branch_metadata() -> None:
    # Embedding and label-search backends often return no branch at all; the curated token
    # set is the only signal left, and it has to be enough on its own.
    gate = PlaceNameGate(min_signals=2)
    d = gate.evaluate(query="Presumptions", label="Slovenia", branch="", score=90.0)
    assert d.demoted
    assert d.score <= 40.0


def test_a_multi_word_place_label_is_governed_by_its_tokens() -> None:
    gate = PlaceNameGate(min_signals=2)
    assert gate.evaluate(query="Presumptions", label="Puerto Rico", branch="", score=90.0).demoted


def test_extra_branch_markers_need_a_non_blank_branch() -> None:
    # An empty branch must not match a marker by accident; the token/marker rules cover that case.
    gate = PlaceNameGate(min_signals=2, extra_branch_markers=("municipality",))
    assert not gate.evaluate(query="retaliation", label="Exampleton", branch="", score=90.0).demoted
    assert not gate.evaluate(query="retaliation", label="Exampleton", branch="   ", score=90.0).demoted


def test_corroborating_signals_alone_can_clear_the_place_gate() -> None:
    gate = PlaceNameGate(min_signals=3)
    assert gate.evaluate(
        query="Slovenian contract law", label="Slovenia", branch="Location", score=88.0,
        corroborating_signals=3,
    ).reason == "corroborated-place"


def test_the_demoted_score_is_a_ceiling_not_a_replacement() -> None:
    # A candidate already below the floor must not be *raised* by being demoted.
    gate = PlaceNameGate(min_signals=2, demoted_score=40.0)
    assert gate.evaluate(query="x", label="Slovenia", branch="Location", score=12.0).score == 12.0


def test_the_short_label_gate_governs_single_content_word_labels() -> None:
    # "Auction" is 7 characters but one content word — exactly the homonym shape.
    gate = ShortLabelGate()
    assert gate.evaluate(query="cause of action", label="Auction", score=88.0).demoted
    # Two content words is enough specificity to pass ungated.
    d = gate.evaluate(query="cause of action", label="Cause of Action", score=88.0)
    assert not d.demoted and d.reason == "not-short"


def test_the_short_label_gate_lets_an_exact_string_match_through_at_any_score() -> None:
    gate = ShortLabelGate(near_exact_threshold=95.0)
    d = gate.evaluate(query="  TAX  ", label="Tax", score=60.0)
    assert not d.demoted and d.reason == "near-exact-short-label"


def test_the_short_label_gate_thresholds_are_configurable() -> None:
    lenient = ShortLabelGate(near_exact_threshold=80.0)
    assert not lenient.evaluate(query="law of the sea", label="law", score=88.0).demoted
    assert ShortLabelGate().evaluate(query="law of the sea", label="law", score=88.0).demoted


def test_calibration_bands_redraw_the_weak_band_from_verdicts() -> None:
    # Finding 004 end to end: raw 60 reads "weak" on the raw scale but the recorded verdicts
    # say scores that low are wrong.
    cal = ScoreCalibration.fit(
        [
            CalibrationSample(60, "wrong"),
            CalibrationSample(65, "wrong"),
            CalibrationSample(90, "correct"),
            CalibrationSample(95, "correct"),
        ]
    )
    assert cal.band(60) == "wrong"
    assert cal.band(90) == "strong"
    low, high = cal.weak_band_bounds()
    assert low <= high <= 90.0
