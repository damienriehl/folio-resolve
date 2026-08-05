"""Source-type classification and the metadata/front-matter exclusion hook.

Ch02 unit ``d3c44e2a``: *"Source is metadata — should never have been considered. Exclude
metadata/front-matter sources from tagging."* This module gives every unit a first-class
``SourceType`` and a policy for which source types are eligible for tagging, so exclusion is a
declared rule rather than a fragile ``is_substantive()`` heuristic buried in the tagger.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import StrEnum


class SourceType(StrEnum):
    BODY = "body"
    FRONT_MATTER = "front_matter"
    METADATA = "metadata"
    BACK_MATTER = "back_matter"
    HEADING = "heading"


# Source types eligible for FOLIO tagging by default.
DEFAULT_TAGGABLE: frozenset[SourceType] = frozenset({SourceType.BODY, SourceType.HEADING})

# Front-/back-matter section markers (matched case-insensitively against a section label).
_FRONT_MATTER_MARKERS = (
    "title page", "copyright", "colophon", "isbn", "table of contents", "toc",
    "dedication", "acknowledgments", "acknowledgements", "preface", "foreword",
    "about the author", "frontispiece", "half title",
)
_BACK_MATTER_MARKERS = ("index", "bibliography", "appendix", "glossary", "colophon", "errata")
_METADATA_MARKERS = ("metadata", "document properties", "publisher", "cataloging")

# A real ISBN-10 (9 digits + check) or ISBN-13 (978/979 + 9 digits + check), with the optional
# hyphen/space grouping publishers use. The digit count is what makes this an ISBN test: an
# earlier `\d{1,5}[- ]?\d{1,7}[- ]?\d{1,7}[- ]?[\dxX]` needed only FOUR digits, so any short body
# unit that mentioned a year ("The 2024 amendments to Rule 26") classified as METADATA and was
# silently dropped from tagging by MatchPipeline.match.
_ISBN_RE = re.compile(r"\b(?:97[89][- ]?)?(?:\d[- ]?){9}[\dxX]\b")


def classify_source(section_label: str, text: str = "") -> SourceType:
    """Classify a unit's source from its section label (and, weakly, its text)."""
    label = (section_label or "").strip().lower()
    for marker in _METADATA_MARKERS:
        if marker in label:
            return SourceType.METADATA
    for marker in _FRONT_MATTER_MARKERS:
        if marker in label:
            return SourceType.FRONT_MATTER
    for marker in _BACK_MATTER_MARKERS:
        if marker in label:
            return SourceType.BACK_MATTER
    # A short unit that is mostly an ISBN / publisher block reads as metadata.
    if text and len(text) < 200 and _ISBN_RE.search(text):
        return SourceType.METADATA
    return SourceType.BODY


class SourceClassifier:
    """Decide whether a unit is eligible for tagging under a taggable-source policy."""

    def __init__(
        self,
        taggable: frozenset[SourceType] = DEFAULT_TAGGABLE,
        classifier: Callable[[str, str], SourceType] = classify_source,
    ) -> None:
        self._taggable = taggable
        self._classify = classifier

    def source_type(self, section_label: str, text: str = "") -> SourceType:
        return self._classify(section_label, text)

    def is_taggable(self, section_label: str, text: str = "") -> bool:
        return self._classify(section_label, text) in self._taggable
