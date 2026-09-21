"""Isolated public-benchmark lexical policies; production scoring stays unchanged.

KTD1 intentionally keeps a small copy of the production scorer below. Shared
arithmetic helpers remain production imports. Differential tests constrain drift.
The substring policy reproduces production, including its empty-query definition
bonus; the boundary treatment alone excludes empty containment needles.
"""
from __future__ import annotations

import unicodedata
from typing import Literal, TypedDict

from folio_resolve.scoring import (
    SPECIFICITY_PENALTY_WEIGHT,
    WordSimilarity,
    _as_text,
    _no_vector_similarity,
    content_words,
    word_overlap,
)
from folio_resolve.scoring import (
    compute_relevance_score as baseline_relevance_score,
)

__all__ = ["ContainmentAward", "baseline_relevance_score", "boundary_relevance_score", "containment_start"]


class ContainmentAward(TypedDict):
    field: str
    text: str
    needle: str
    start: int
    end: int
    bonus: float


def _word_character(character: str) -> bool:
    return character == "_" or unicodedata.category(character)[0] in "LNM"


def containment_start(
    text: str, needle: str, policy: Literal["substring", "boundary"] = "boundary",
) -> int | None:
    """Find the first qualifying literal occurrence, including overlapping matches.

    Callers supply production-normalized strings. No tokenization, case folding,
    accent normalization, or internal whitespace rewriting happens here.
    """
    if policy == "substring":
        start = text.find(needle)
        return start if start >= 0 else None
    if policy != "boundary":
        raise ValueError(f"Unknown lexical policy: {policy}")
    if not needle:
        return None
    start = text.find(needle)
    while start >= 0:
        end = start + len(needle)
        if (start == 0 or not _word_character(text[start - 1])) and (
            end == len(text) or not _word_character(text[end])
        ):
            return start
        start = text.find(needle, start + 1)
    return None


def boundary_relevance_score(
    query_content: set[str] | None,
    query_full: str | None,
    label: str | None,
    definition: str | None = None,
    synonyms: list[str] | None = None,
    preferred_label: str | None = None,
    *,
    use_vectors: bool = False,
    word_similarity: WordSimilarity = _no_vector_similarity,
    specificity_penalty: float = 1.0,
    policy: Literal["substring", "boundary"] = "boundary",
    trace: list[ContainmentAward] | None = None,
) -> float:
    """Production arithmetic with only four containment predicates replaced.

    ``trace`` receives awards as the scorer executes those branches. Offsets refer
    to lowercased text (and stripped query), not original Unicode source offsets.
    Awards describe channel bonuses before overlap maxima and specificity penalties;
    they need not be the winning contribution to the final score.
    """
    if policy not in ("substring", "boundary"):
        raise ValueError(f"Unknown lexical policy: {policy}")

    def award(field: str, text: str, needle: str, start: int, bonus: float) -> None:
        if trace is not None:
            trace.append({
                "field": field, "text": text, "needle": needle,
                "start": start, "end": start + len(needle), "bonus": bonus,
            })

    label = _as_text(label)
    if not label:
        return 0.0
    query_full = _as_text(query_full)
    definition = _as_text(definition) or None
    preferred_label = _as_text(preferred_label) or None
    synonyms = [s for s in (synonyms or []) if isinstance(s, str)]
    query_content = {w for w in (query_content or set()) if isinstance(w, str)}

    query_lower = query_full.lower().strip()
    label_lower = label.lower()

    if query_lower == label_lower:
        return 99.0

    label_content = content_words(label)

    label_score = 0.0
    if len(query_lower) >= 4 and (start := containment_start(label_lower, query_lower, policy)) is not None:
        label_score = 92.0
        award("label", label_lower, query_lower, start, 92.0)
    elif (
        len(label_lower) >= 4
        and (start := containment_start(query_lower, label_lower, policy)) is not None
        and len(label_lower) / len(query_lower) > 0.3
    ):
        label_score = 88.0
        award("reverse_label", query_lower, label_lower, start, 88.0)
    overlap = word_overlap(
        query_content, label_content, use_vectors=use_vectors, word_similarity=word_similarity
    )
    if overlap > 0:
        label_score = max(label_score, overlap * 88)

    pref_score = 0.0
    if preferred_label:
        pref_lower = preferred_label.lower()
        if query_lower == pref_lower:
            pref_score = 90.0
        elif len(query_lower) >= 4 and (start := containment_start(pref_lower, query_lower, policy)) is not None:
            pref_score = 84.0
            award("preferred_label", pref_lower, query_lower, start, 84.0)
        else:
            pref_content = content_words(preferred_label)
            p_overlap = word_overlap(
                query_content, pref_content, use_vectors=use_vectors, word_similarity=word_similarity
            )
            if p_overlap > 0:
                pref_score = p_overlap * 86

    syn_score = 0.0
    for syn in synonyms:
        syn_content = content_words(syn)
        s_overlap = word_overlap(
            query_content, syn_content, use_vectors=use_vectors, word_similarity=word_similarity
        )
        if s_overlap > 0:
            syn_score = max(syn_score, s_overlap * 82)

    def_score = 0.0
    if definition:
        def_lower = definition.lower()
        if (start := containment_start(def_lower, query_lower, policy)) is not None:
            def_score = 60.0
            award("definition", def_lower, query_lower, start, 60.0)
        def_content = content_words(definition)
        d_overlap = word_overlap(query_content, def_content, word_similarity=word_similarity)
        if d_overlap > 0:
            def_score = max(def_score, d_overlap * 55)

    primary = max(label_score, pref_score, syn_score)
    final = primary + min(def_score * 0.12, 8) if primary > 0 else def_score

    # Specificity penalty: penalize candidates more specific than the query.
    if label_content and query_content and final > 0 and specificity_penalty > 0:
        extra_words = label_content - query_content
        if extra_words and len(label_content) > len(query_content):
            specificity_ratio = len(extra_words) / len(label_content)
            penalty = specificity_ratio * SPECIFICITY_PENALTY_WEIGHT * specificity_penalty
            final = final * (1.0 - min(max(penalty, 0.0), 1.0))

    return round(min(final, 99.0), 1)

