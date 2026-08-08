"""Source classification and the metadata/front-matter exclusion policy (Ch02 unit d3c44e2a).

Every fixture here is synthetic front-matter boilerplate — no book source text (public repo).
"""

from __future__ import annotations

import pytest

from folio_resolve import SourceClassifier, SourceType
from folio_resolve.sources import DEFAULT_TAGGABLE, classify_source

# -- classify_source: label-driven classification ------------------------


@pytest.mark.parametrize(
    "label",
    ["Metadata", "Document Properties", "Publisher Information", "Cataloging in Publication"],
)
def test_metadata_labels(label: str) -> None:
    assert classify_source(label) == SourceType.METADATA


@pytest.mark.parametrize(
    "label",
    [
        "Title Page", "Copyright", "Colophon", "ISBN", "Table of Contents", "TOC",
        "Dedication", "Acknowledgments", "Acknowledgements", "Preface", "Foreword",
        "About the Author", "Frontispiece", "Half Title",
    ],
)
def test_front_matter_labels(label: str) -> None:
    assert classify_source(label) == SourceType.FRONT_MATTER


@pytest.mark.parametrize("label", ["Index", "Bibliography", "Appendix A", "Glossary", "Errata"])
def test_back_matter_labels(label: str) -> None:
    assert classify_source(label) == SourceType.BACK_MATTER


def test_classification_is_case_insensitive_and_substring_based() -> None:
    assert classify_source("  COPYRIGHT NOTICE  ") == SourceType.FRONT_MATTER
    assert classify_source("Chapter 9: Appendix of Forms") == SourceType.BACK_MATTER


def test_metadata_markers_win_over_front_matter() -> None:
    # Both markers present; METADATA is checked first, so the precedence is pinned, not accidental.
    assert classify_source("Copyright Metadata") == SourceType.METADATA


def test_colophon_is_front_matter_not_back_matter() -> None:
    # "colophon" appears in both marker tuples; front matter is checked first.
    assert classify_source("Colophon") == SourceType.FRONT_MATTER


def test_unknown_labels_and_blank_labels_are_body() -> None:
    assert classify_source("Chapter 2: Cross-Examination") == SourceType.BODY
    assert classify_source("") == SourceType.BODY
    assert classify_source("", "Plain body prose about arbitration.") == SourceType.BODY


# -- classify_source: the ISBN text heuristic ----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ISBN 978-0-13-468599-1",
        "978-0-306-40615-7",
        "0-306-40615-2",
        "0306406152",
        "080442957X",
        "ISBN-13: 9780134685991",
    ],
)
def test_short_text_carrying_a_real_isbn_reads_as_metadata(text: str) -> None:
    assert classify_source("", text) == SourceType.METADATA


@pytest.mark.parametrize(
    "text",
    [
        "The 2024 amendments to Rule 26 changed discovery.",
        "See page 1234 for the full stipulation.",
        "Docket No. 62-CV-26-2379 was consolidated.",
        "The jury awarded $1,000,000 in damages.",
        "97 F.3d 1234 (2d Cir. 1996) controls here.",
    ],
)
def test_ordinary_numbers_do_not_read_as_metadata(text: str) -> None:
    """Regression: the ISBN pattern needed only FOUR digits, so any short unit mentioning a
    year, page, docket, or citation number classified as METADATA — and MatchPipeline.match
    then returned [] for it, silently dropping a substantive unit from tagging.
    """
    assert classify_source("Chapter 3", text) == SourceType.BODY


def test_the_isbn_heuristic_only_applies_to_short_text() -> None:
    isbn = "ISBN 978-0-13-468599-1"
    padding = "The court considered the arbitration agreement at length. " * 5
    assert len(padding + isbn) >= 200
    # A long substantive passage that happens to quote an ISBN is still body text.
    assert classify_source("", padding + isbn) == SourceType.BODY


def test_an_explicit_label_outranks_the_isbn_heuristic() -> None:
    assert classify_source("Chapter 1", "ISBN 978-0-13-468599-1") == SourceType.METADATA
    # ...but a front-matter label still classifies as front matter, not metadata.
    assert classify_source("Copyright", "ISBN 978-0-13-468599-1") == SourceType.FRONT_MATTER


# -- SourceClassifier: the taggability policy ----------------------------


def test_default_policy_tags_only_body_and_heading() -> None:
    assert set(DEFAULT_TAGGABLE) == {SourceType.BODY, SourceType.HEADING}
    sc = SourceClassifier()
    assert sc.is_taggable("Chapter 2: Cross-Examination")
    for excluded in ("Copyright Page", "Document Metadata", "Bibliography", "Index"):
        assert not sc.is_taggable(excluded), excluded


def test_source_type_exposes_the_classification_verbatim() -> None:
    sc = SourceClassifier()
    assert sc.source_type("Copyright", "ISBN 978-0-13-468599-1") == SourceType.FRONT_MATTER


def test_a_custom_taggable_policy_widens_the_gate() -> None:
    sc = SourceClassifier(taggable=frozenset({SourceType.BODY, SourceType.BACK_MATTER}))
    assert sc.is_taggable("Appendix A")  # opted in
    assert not sc.is_taggable("Preface")  # still excluded


def test_a_custom_classifier_is_honored() -> None:
    sc = SourceClassifier(classifier=lambda _label, _text: SourceType.METADATA)
    assert not sc.is_taggable("Chapter 2")
    assert sc.source_type("Chapter 2") == SourceType.METADATA


def test_source_type_values_are_stable_strings() -> None:
    # Consumers persist these in JSON manifests; the wire values must not drift.
    assert [s.value for s in SourceType] == [
        "body",
        "front_matter",
        "metadata",
        "back_matter",
        "heading",
    ]
