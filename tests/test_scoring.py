"""Scorer behavior — word-order invariance, expansions, specificity penalty."""

from __future__ import annotations

import pytest

from folio_resolve.scoring import (
    SEARCH_STOPWORDS,
    compute_relevance_score,
    content_words,
    definition_context_score,
    generate_search_terms,
    tokenize,
    word_overlap,
)


def test_definition_context_score_prefers_the_definition_near_the_matched_anchor() -> None:
    passage = (
        "A maritime shipping vessel is discussed under Alpha Doctrine. "
        + "neutral filler " * 80
        + "The evidentiary burden controls under Zulu Doctrine."
    )

    relevant = definition_context_score(
        passage, "evidentiary burden", anchor="Zulu Doctrine"
    )
    distant = definition_context_score(
        passage, "maritime shipping vessel", anchor="Zulu Doctrine"
    )

    assert relevant > distant


def test_word_order_invariant() -> None:
    # Ch02 finding 004: "arbitration rules" == "rules of arbitration".
    a = content_words("arbitration rules")
    b = content_words("rules of arbitration")
    assert a == b == {"arbitration", "rules"}
    assert word_overlap(a, b) == 1.0


def test_exact_match_scores_99() -> None:
    q = content_words("Antitrust and Competition Law")
    assert compute_relevance_score(q, "Antitrust and Competition Law", "Antitrust and Competition Law") == 99.0


def test_rules_of_arbitration_matches_arbitration_rules() -> None:
    q = content_words("rules of arbitration")
    score = compute_relevance_score(q, "rules of arbitration", "Arbitration Rules")
    assert score >= 85.0


def test_specificity_penalty() -> None:
    q = content_words("Antitrust")
    close = compute_relevance_score(q, "Antitrust", "Antitrust Claims")
    specific = compute_relevance_score(q, "Antitrust", "Antitrust - Bundled Pricing Claims")
    assert specific < close


def test_generate_search_terms_includes_legal_expansions() -> None:
    terms = [t.lower() for t in generate_search_terms("Commercial Litigation")]
    assert "litigation practice" in terms
    assert "litigation service" in terms


def test_generate_search_terms_dedups_and_keeps_full_phrase_first() -> None:
    terms = generate_search_terms("Arbitration")
    assert terms[0] == "Arbitration"
    assert len(terms) == len(set(t.lower() for t in terms))


# -- v0.3.0: golden no-drift table ---------------------------------------
#
# Every consumer-facing gap closed in v0.3.0 (type coercion, the specificity-penalty weight)
# is additive by construction, so DEFAULT scoring must not move by a single tenth. These are
# the values produced by v0.2.1 on the pre-change code; folio-mapper's and folio-enrich's
# committed golden captures depend on them.
GOLDEN_DEFAULT_SCORES: dict[tuple[str, str], float] = {
    ("Antitrust and Competition Law", "Antitrust and Competition Law"): 99.0,
    ("Antitrust", "Antitrust - Bundled Pricing Claims"): 64.4,
    ("Antitrust", "Antitrust Claims"): 73.6,
    ("Habitability", "Breach of Warranty of Habitability"): 67.5,
    ("arbitration", "Alternative Dispute Resolution"): 49.2,
    ("commercial litigation", "Commercial Litigation Practice"): 86.0,
    ("custody", "Child Custody Determination"): 67.5,
    ("employment discrimination", "Employment Law"): 44.0,
    ("findings of fact", "Proposed Findings of Fact"): 79.7,
    ("hearing", "Markman Hearing"): 73.6,
    ("law", "Delaware"): 0.0,
    ("rules of arbitration", "Arbitration Rules"): 88.0,
    ("tax", "U.S. Tax Court"): 70.4,
    ("wrongful termination", "Macedonia"): 0.0,
}

# The three rows above that carry extra context, kept out of the tuple key.
_GOLDEN_EXTRAS: dict[tuple[str, str], dict[str, object]] = {
    ("arbitration", "Alternative Dispute Resolution"): {"synonyms": ["Arbitration Practice"]},
    ("employment discrimination", "Employment Law"): {"preferred_label": "Employment Law Practice"},
    ("commercial litigation", "Commercial Litigation Practice"): {
        "definition": "Practice of commercial litigation",
        "synonyms": ["Business Litigation"],
        "preferred_label": "Commercial Litigation Practice",
    },
}


def test_default_scoring_has_not_drifted() -> None:
    for (query, label), expected in GOLDEN_DEFAULT_SCORES.items():
        extras = _GOLDEN_EXTRAS.get((query, label), {})
        actual = compute_relevance_score(
            content_words(query),
            query,
            label,
            extras.get("definition"),  # type: ignore[arg-type]
            extras.get("synonyms"),  # type: ignore[arg-type]
            extras.get("preferred_label"),  # type: ignore[arg-type]
        )
        assert actual == expected, f"{query!r} -> {label!r} drifted: {actual} != {expected}"


def test_explicit_default_specificity_penalty_is_the_default() -> None:
    for (query, label), expected in GOLDEN_DEFAULT_SCORES.items():
        extras = _GOLDEN_EXTRAS.get((query, label), {})
        assert (
            compute_relevance_score(
                content_words(query),
                query,
                label,
                extras.get("definition"),  # type: ignore[arg-type]
                extras.get("synonyms"),  # type: ignore[arg-type]
                extras.get("preferred_label"),  # type: ignore[arg-type]
                specificity_penalty=1.0,
            )
            == expected
        )


# -- v0.3.0 gap (a): type-defensive scoring ------------------------------


def test_none_preferred_label_is_absent_not_a_crash() -> None:
    # folio-python genuinely returns None for a concept with no preferred label.
    q = content_words("rules of arbitration")
    assert compute_relevance_score(q, "rules of arbitration", "Arbitration Rules", None, None, None) == 88.0


def test_non_str_text_arguments_read_as_absent() -> None:
    q = content_words("rules of arbitration")
    # A test double (or any non-str) in any text slot must be ignored, never a TypeError.
    double = object()
    assert compute_relevance_score(q, "rules of arbitration", double) == 0.0  # type: ignore[arg-type]
    with_junk = compute_relevance_score(
        q,
        "rules of arbitration",
        "Arbitration Rules",
        double,  # type: ignore[arg-type]
        [double, "Rules of Arbitration Practice"],  # type: ignore[list-item]
        double,  # type: ignore[arg-type]
    )
    assert with_junk == 88.0


def test_mock_style_double_does_not_raise() -> None:
    from unittest.mock import MagicMock

    q = content_words("habitability")
    score = compute_relevance_score(q, "habitability", MagicMock(), preferred_label=MagicMock())  # type: ignore[arg-type]
    assert score == 0.0


def test_none_query_arguments_are_tolerated() -> None:
    assert compute_relevance_score(None, None, "Arbitration Rules") == 0.0
    assert compute_relevance_score(set(), "", "Arbitration Rules") == 0.0
    # A non-str inside the content-word set is dropped rather than exploding word_overlap.
    assert compute_relevance_score({"arbitration", 7}, "arbitration", "Arbitration Rules") > 0  # type: ignore[arg-type]


# -- v0.3.0 gap (c): specificity-penalty weight --------------------------


def test_specificity_penalty_zero_removes_the_haircut() -> None:
    q = content_words("Habitability")
    full = compute_relevance_score(
        q, "Habitability", "Breach of Warranty of Habitability", specificity_penalty=0.0
    )
    assert full > 67.5
    # With no penalty the score is the raw substring-containment score, not a haircut of it.
    assert full == 92.0


def test_specificity_penalty_damps_proportionally() -> None:
    q = content_words("Habitability")
    default = compute_relevance_score(q, "Habitability", "Breach of Warranty of Habitability")
    damped = compute_relevance_score(
        q, "Habitability", "Breach of Warranty of Habitability", specificity_penalty=0.5
    )
    none_at_all = compute_relevance_score(
        q, "Habitability", "Breach of Warranty of Habitability", specificity_penalty=0.0
    )
    assert default < damped < none_at_all


def test_specificity_penalty_above_one_sharpens_without_going_negative() -> None:
    q = content_words("custody")
    sharp = compute_relevance_score(
        q, "custody", "Child Custody Determination", specificity_penalty=5.0
    )
    assert 0.0 <= sharp < compute_relevance_score(q, "custody", "Child Custody Determination")


def test_specificity_penalty_leaves_unpenalized_pairs_alone() -> None:
    # An exact match has no extra label words, so the weight cannot move it.
    q = content_words("Arbitration Rules")
    for weight in (0.0, 0.5, 1.0, 3.0):
        assert (
            compute_relevance_score(
                q, "Arbitration Rules", "Arbitration Rules", specificity_penalty=weight
            )
            == 99.0
        )


# -- tokenization --------------------------------------------------------


def test_tokenize_keeps_alphabetic_tokens_of_two_or_more_characters() -> None:
    assert tokenize("Rule 26(b)(1): Proportionality!") == ["rule", "proportionality"]
    assert tokenize("a I of") == ["of"]  # single characters dropped, stopwords kept here


def test_tokenize_of_non_strings_is_empty_not_a_typeerror() -> None:
    for value in (None, 7, 3.5, object(), ["arbitration"]):
        assert tokenize(value) == []  # type: ignore[arg-type]
    assert content_words(None) == set()  # type: ignore[arg-type]


def test_content_words_drops_the_search_stopwords() -> None:
    # "law", "legal", "general" are stopwords here: they are too common in a legal ontology
    # to discriminate between concepts.
    assert content_words("the general law of the case") == {"case"}
    assert "law" in SEARCH_STOPWORDS


# -- word_overlap --------------------------------------------------------


def test_overlap_of_an_empty_side_is_zero() -> None:
    assert word_overlap(set(), {"arbitration"}) == 0.0
    assert word_overlap({"arbitration"}, set()) == 0.0


def test_prefix_matches_earn_partial_credit() -> None:
    exact = word_overlap({"arbitration"}, {"arbitration"})
    prefix = word_overlap({"arbitration"}, {"arbitrations"})  # one is a prefix of the other
    none = word_overlap({"arbitration"}, {"zoning"})
    assert exact == 1.0
    assert 0.0 < prefix < exact
    assert none == 0.0


def test_a_shared_stem_below_the_prefix_rule_still_earns_credit() -> None:
    # Neither string prefixes the other, but they share >= 4 leading characters covering
    # >= 70% of the shorter one.
    assert word_overlap({"deposit"}, {"deposed"}) == pytest.approx(0.7)


def test_short_words_get_no_fuzzy_credit() -> None:
    # Below 3 characters the prefix rules would fire on almost anything.
    assert word_overlap({"ab"}, {"abc"}) == 0.0


def test_reverse_overlap_helps_a_multi_concept_query_reach_a_narrow_target() -> None:
    # The target's words are all in the query, but not vice versa; reverse overlap (x0.75)
    # is what keeps this above zero.
    query = {"arbitration", "mediation", "negotiation", "settlement"}
    assert word_overlap(query, {"arbitration", "mediation"}) == pytest.approx(0.75)


def test_a_single_word_target_gets_no_reverse_credit() -> None:
    # Reverse overlap needs >= 2 target words, or every one-word label would match everything.
    assert word_overlap({"arbitration", "mediation", "negotiation", "settlement"}, {"arbitration"}) == 0.25


def test_the_vector_fallback_is_off_by_default() -> None:
    def always_similar(_a: str, _b: str) -> float:
        raise AssertionError("the vector path must not run unless use_vectors=True")

    assert word_overlap({"presumption"}, {"burden"}, word_similarity=always_similar) == 0.0


def test_the_vector_fallback_rescues_words_with_no_character_overlap() -> None:
    # Ch02's "no shared label token" shape, at the word level: an injected vectorizer is the
    # only way "presumption" reaches "burden".
    def similar(a: str, b: str) -> float:
        return 0.9 if {a, b} == {"presumption", "burden"} else 0.0

    assert word_overlap({"presumption"}, {"burden"}, use_vectors=True, word_similarity=similar) == 0.5


def test_the_vector_fallback_is_capped_below_a_character_match() -> None:
    # A vector guess must never outrank real character evidence.
    assert word_overlap({"presumption"}, {"burden"}, use_vectors=True, word_similarity=lambda a, b: 1.0) == 0.5


def test_the_vector_fallback_ignores_weak_similarities() -> None:
    assert word_overlap({"presumption"}, {"burden"}, use_vectors=True, word_similarity=lambda a, b: 0.25) == 0.0


def test_the_vector_fallback_never_displaces_a_character_match() -> None:
    def similar(_a: str, _b: str) -> float:
        return 1.0

    both = word_overlap({"arbitration"}, {"arbitration"}, use_vectors=True, word_similarity=similar)
    assert both == 1.0


# -- preferred label and definition contributions ------------------------


def test_an_exact_preferred_label_match_scores_just_below_an_exact_label_match() -> None:
    q = content_words("Employment Law Practice")
    score = compute_relevance_score(
        q, "Employment Law Practice", "Employment Law", preferred_label="Employment Law Practice"
    )
    assert score == 90.0  # the pref-exact tier, under the 99.0 label-exact tier


def test_a_definition_only_hit_scores_in_the_definition_tier() -> None:
    # No shared label token at all; only the definition carries the query.
    score = compute_relevance_score(
        content_words("presumptions"),
        "presumptions",
        "Litigation Burdens of Proof",
        "Allocation of the burden of proof, including presumptions.",
    )
    assert 0 < score <= 60.0


def test_a_definition_only_tops_up_a_label_hit_by_at_most_eight_points() -> None:
    q = content_words("commercial litigation")
    bare = compute_relevance_score(q, "commercial litigation", "Commercial Litigation Practice")
    with_def = compute_relevance_score(
        q,
        "commercial litigation",
        "Commercial Litigation Practice",
        "A practice devoted to commercial litigation disputes between businesses.",
    )
    assert bare < with_def <= bare + 8.0


# -- generate_search_terms -----------------------------------------------


def test_search_terms_include_descending_sub_phrases_for_long_headings() -> None:
    terms = generate_search_terms("Proposed Findings of Fact")
    assert terms[0] == "Proposed Findings of Fact"
    # Sub-phrases are emitted longest-first so the most specific search runs first.
    assert "proposed findings of" in terms
    assert "findings of fact" in terms
    assert "proposed findings" in terms
    lengths = [len(t.split()) for t in terms]
    assert lengths == sorted(lengths, reverse=True)


def test_short_terms_get_no_sub_phrases() -> None:
    # Below three words there is no sub-phrase to take: the full phrase, its content words,
    # then the legal expansions ("arbitration" -> Service branch) and nothing else.
    assert generate_search_terms("Arbitration Rules") == [
        "Arbitration Rules",
        "arbitration",
        "rules",
        "arbitration service",
    ]


def test_search_terms_emit_content_words_longest_first() -> None:
    terms = generate_search_terms("Arbitration of Tax Disputes")
    singles = [t for t in terms if " " not in t and len(t) >= 3]
    assert singles == ["arbitration", "disputes", "tax"]


def test_search_term_order_is_deterministic() -> None:
    """Regression: the content words were sorted by length alone, over a *set*.

    Equal-length words therefore came out in set-iteration order, which varies with
    PYTHONHASHSEED — "Commercial Litigation" emitted commercial/litigation or
    litigation/commercial depending on the process. The expansion loop iterated the set
    outright. Order matters: it is the order a consumer runs its searches in, and with a
    result limit it decides which candidates survive.
    """
    assert generate_search_terms("Commercial Litigation") == [
        "Commercial Litigation",
        "commercial",
        "litigation",
        "litigation practice",
        "litigation service",
    ]
    # Full phrase, then sub-phrases, then content words longest-first, then legal expansions
    # in alphabetical order of the word they expand.
    assert generate_search_terms("Antitrust Tax Estate") == [
        "Antitrust Tax Estate",
        "antitrust tax",
        "tax estate",
        "antitrust",
        "estate",
        "tax",
        "antitrust practice",
        "antitrust law",
        "antitrust compliance",
        "estate planning",
        "estate practice",
        "estate law",
        "tax practice",
        "tax service",
        "tax law",
    ]


def test_search_terms_skip_stopword_only_sub_phrases() -> None:
    # "of the" carries no content, so it never becomes a search.
    assert all(content_words(t) for t in generate_search_terms("Burden of the Proof"))


def test_search_terms_of_a_stopword_only_input_is_just_the_input() -> None:
    assert generate_search_terms("of the") == ["of the"]
