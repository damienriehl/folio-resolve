"""Aho-Corasick matcher + entity ruler contract (ported from enrich's test_aho_corasick)."""

from __future__ import annotations

from folio_resolve import Concept, FOLIOEntityRuler, InMemoryOntology
from folio_resolve.matching import AhoCorasickMatcher


def test_match_offsets_and_word_boundary() -> None:
    m = AhoCorasickMatcher()
    m.add_pattern("court")
    m.build()
    hits = m.search("The court ruled.")
    assert len(hits) == 1
    assert (hits[0].start, hits[0].end) == (4, 9)
    assert "The court ruled."[hits[0].start : hits[0].end] == "court"


def test_word_boundary_rejects_substring() -> None:
    m = AhoCorasickMatcher()
    m.add_pattern("contract")
    m.build()
    # "contractual" must NOT match "contract".
    assert m.search("a contractual clause") == []
    assert len(m.search("the contract terms")) == 1


def test_overlap_longer_wins_partial() -> None:
    m = AhoCorasickMatcher()
    m.add_patterns({"burden of proof": {}, "proof of service": {}})
    m.build()
    # Non-overlapping distinct phrases both survive.
    hits = m.search("burden of proof and proof of service")
    labels = sorted(h.pattern for h in hits)
    assert labels == ["burden of proof", "proof of service"]


def test_contained_spans_both_kept() -> None:
    m = AhoCorasickMatcher()
    m.add_patterns({"cross": {}, "cross-examination": {}})
    m.build()
    hits = m.search("cross-examination begins")
    patterns = {h.pattern for h in hits}
    assert "cross-examination" in patterns
    assert "cross" in patterns  # contained, both survive


def test_entity_ruler_emits_iri_tagged_spans() -> None:
    ont = InMemoryOntology(
        [Concept(iri="R-cross", label="Cross-Examination", alternative_labels=("Cross Exam",))]
    )
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(ont.all_labels())
    matches = ruler.find_matches("The cross-examination was brutal.")
    assert any(mm.entity_id == "R-cross" and mm.match_type == "preferred" for mm in matches)


def test_entity_ruler_confidence_by_label_type() -> None:
    ont = InMemoryOntology(
        [Concept(iri="R-x", label="Deposition", alternative_labels=("Depo",))]
    )
    ruler = FOLIOEntityRuler()
    ruler.load_patterns(ont.all_labels())
    pref = ruler.find_matches("A deposition today.")
    alt = ruler.find_matches("A depo today.")
    assert pref[0].confidence > alt[0].confidence


# -- automaton construction ----------------------------------------------


def _output_total(m: AhoCorasickMatcher) -> int:
    total, stack = 0, [m._root]
    while stack:
        node = stack.pop()
        total += len(node.all_outputs)
        stack.extend(node.children.values())
    return total


def test_build_is_idempotent() -> None:
    """Regression: rebuilding used to re-fold the failure chain into already-folded lists.

    Patterns that are suffixes of other patterns inherit their fail node's outputs. Folding
    that into `outputs` in place meant each extra `build()` re-inherited everything already
    inherited — 10 -> 20 -> 35 -> 56 -> 84 stored outputs over five builds, unbounded in a
    long-lived index over FOLIO's 18k labels. `search` hid it: `_resolve_overlaps` dedups
    identical spans, so the duplicates cost memory and scan time but never showed up.
    """
    m = AhoCorasickMatcher()
    m.add_patterns({"trial": {}, "pretrial": {}, "rial": {}, "al": {}})
    m.build()
    baseline_outputs = _output_total(m)
    baseline_hits = [(h.pattern, h.start, h.end) for h in m.search("a pretrial order")]
    for _ in range(4):
        m.build()
        assert _output_total(m) == baseline_outputs
        assert [(h.pattern, h.start, h.end) for h in m.search("a pretrial order")] == baseline_hits


def test_suffix_patterns_are_still_reported_after_a_rebuild() -> None:
    # The failure-link inheritance itself must survive the idempotence fix.
    m = AhoCorasickMatcher()
    m.add_patterns({"summary judgment": {}, "judgment": {}})
    m.build()
    m.build()
    assert {h.pattern for h in m.search("the summary judgment motion")} == {
        "summary judgment",
        "judgment",
    }


def test_patterns_added_after_a_build_are_picked_up_without_duplicating_the_old_ones() -> None:
    m = AhoCorasickMatcher()
    m.add_patterns({"trial": {}, "pretrial": {}})
    m.build()
    m.add_pattern("order")
    hits = [(h.pattern, h.start, h.end) for h in m.search("a pretrial order")]
    assert hits == [("pretrial", 2, 10), ("order", 11, 16)]
    assert m.pattern_count == 3


def test_search_builds_lazily_when_the_caller_forgot_to() -> None:
    m = AhoCorasickMatcher()
    m.add_pattern("court")
    assert len(m.search("The court ruled.")) == 1  # no explicit build()


def test_an_empty_pattern_is_ignored() -> None:
    # It cannot anchor a span, and it would attach an output to the root node that `search`
    # would report at a nonsensical offset.
    m = AhoCorasickMatcher()
    m.add_pattern("")
    m.build()
    assert m.pattern_count == 0
    assert m.search("anything") == []


def test_building_an_empty_automaton_is_safe() -> None:
    m = AhoCorasickMatcher()
    m.build()
    assert m.search("The court ruled.") == []


def test_pattern_count_tracks_additions() -> None:
    m = AhoCorasickMatcher()
    assert m.pattern_count == 0
    m.add_patterns({"a court": {}, "b court": {}})
    assert m.pattern_count == 2


# -- case handling -------------------------------------------------------


def test_matching_is_case_insensitive_by_default_and_reports_original_offsets() -> None:
    m = AhoCorasickMatcher()
    m.add_pattern("Cross-Examination")
    m.build()
    text = "The CROSS-EXAMINATION began."
    hits = m.search(text)
    assert len(hits) == 1
    assert hits[0].pattern == "Cross-Examination"  # the pattern as registered
    assert text[hits[0].start : hits[0].end] == "CROSS-EXAMINATION"  # the text as written


def test_case_sensitive_search_keeps_only_exactly_cased_hits() -> None:
    """Regression: `case_sensitive=True` used to match nothing at all.

    Patterns are keyed lowercase by `add_pattern`, but the old implementation walked the
    *original*-cased text against that lowercase trie — so it could only ever match input that
    was already lowercase, and silently returned [] for the obvious case.
    """
    m = AhoCorasickMatcher()
    m.add_pattern("Court")
    m.build()
    assert [h.start for h in m.search("The Court ruled.", case_sensitive=True)] == [4]
    assert m.search("the court ruled.", case_sensitive=True) == []
    assert m.search("The COURT ruled.", case_sensitive=True) == []
    # ...while the default still finds every casing.
    assert len(m.search("the court ruled.")) == 1


def test_case_sensitive_search_filters_within_a_mixed_text() -> None:
    m = AhoCorasickMatcher()
    m.add_pattern("Court")
    m.build()
    hits = m.search("The Court and the court agreed.", case_sensitive=True)
    assert [h.start for h in hits] == [4]
