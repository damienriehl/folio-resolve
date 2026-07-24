"""Scorer behavior — word-order invariance, expansions, specificity penalty."""

from __future__ import annotations

from folio_resolve.scoring import (
    compute_relevance_score,
    content_words,
    generate_search_terms,
    word_overlap,
)


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
