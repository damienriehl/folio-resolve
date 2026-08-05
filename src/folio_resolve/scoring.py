"""Word-order-invariant relevance scoring.

Lifted from folio-mapper ``backend/app/services/folio_service.py`` — the canonical
scorer that both folio-mapper and (via a copy) folio-enrich relied on. The set-based
content-word overlap makes scoring word-order-invariant: ``"arbitration rules"`` and
``"rules of arbitration"`` both reduce to ``{arbitration, rules}`` (Ch02 finding 004 is
solved here).

The functions in this module depend only on the standard library, so they port cleanly
with no ontology or heavy-dependency coupling. The optional spaCy vector fallback used by
the original ``use_vectors=True`` path is injected via a callable, keeping this module free
of spaCy at import time.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# Words too common to be useful for individual search or scoring.
SEARCH_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "of", "and", "or", "in", "for", "to", "with", "by", "on", "at",
        "is", "are", "was", "were", "be", "been", "being",
        "not", "no", "has", "have", "had", "do", "does", "did",
        "this", "that", "it", "its", "their", "other", "such", "than",
        "your", "yours", "own", "my", "mine", "our", "ours",
        "her", "hers", "him", "his", "whom", "whose", "self",
        "law", "legal", "type", "types", "general",
    }
)

# Map expansion suffixes to their dominant FOLIO branch. Used to scope standalone
# suffix searches to the most relevant branch (folio-mapper Phase 1b).
BRANCH_SIGNAL_WORDS: dict[str, str] = {
    "claim": "Objectives",
    "claims": "Objectives",
    "liability": "Objectives",
    "negligence": "Objectives",
    "malpractice": "Objectives",
    "defense": "Objectives",
    "defenses": "Objectives",
    "practice": "Service",
    "law": "Area of Law",
}

# Domain-aware expansions: common legal content words -> FOLIO label suffixes.
LEGAL_TERM_EXPANSIONS: dict[str, list[str]] = {
    "litigation": ["practice", "service"],
    "transactional": ["practice", "service"],
    "transaction": ["practice", "service"],
    "transactions": ["practice", "service"],
    "regulatory": ["practice", "compliance"],
    "compliance": ["practice", "service"],
    "advisory": ["practice", "service"],
    "dispute": ["service", "resolution"],
    "disputes": ["service", "resolution"],
    "mediation": ["service"],
    "arbitration": ["service"],
    "negotiation": ["service"],
    "settlement": ["service", "practice"],
    "appellate": ["practice", "service"],
    "trial": ["practice", "service"],
    "appeals": ["practice", "service"],
    "prosecution": ["service"],
    "enforcement": ["service", "action"],
    "investigation": ["service"],
    "error": ["malpractice", "negligence"],
    "fault": ["negligence", "liability"],
    "harm": ["liability", "injury"],
    "injury": ["liability", "claim"],
    "negligence": ["claim", "liability"],
    "malpractice": ["claim", "liability"],
    "contract": ["law", "claim", "claims"],
    "breach": ["claim", "claims"],
    "corporate": ["practice", "service", "law"],
    "employment": ["practice", "service", "law"],
    "intellectual": ["property", "practice"],
    "bankruptcy": ["practice", "service", "law"],
    "family": ["practice", "law"],
    "immigration": ["practice", "service", "law"],
    "environmental": ["practice", "law", "compliance"],
    "antitrust": ["practice", "law", "compliance"],
    "tax": ["practice", "service", "law"],
    "real": ["estate", "property"],
    "estate": ["planning", "practice", "law"],
    "counsel": ["service", "practice"],
    "counseling": ["service", "practice"],
    "consulting": ["service", "practice"],
    "collection": ["service", "practice"],
    "recovery": ["service", "practice"],
    "foreclosure": ["service", "practice"],
    "discovery": ["service", "practice"],
    "diligence": ["service", "practice"],
    "audit": ["service", "practice"],
    "drafting": ["service", "practice"],
    "documentation": ["service", "practice"],
    "filing": ["service", "practice"],
    "strategy": ["service", "practice"],
    "planning": ["service", "practice"],
    "risk": ["service", "management"],
    "structuring": ["service", "practice"],
}

# A word-similarity callable: (word_a, word_b) -> cosine in [0, 1]. Defaults to a no-op
# so the core has no spaCy dependency; consumers may inject a real vectorizer.
WordSimilarity = Callable[[str, str], float]


def _no_vector_similarity(_a: str, _b: str) -> float:
    return 0.0


def tokenize(text: str) -> list[str]:
    """Split text into lowercase alphabetic tokens (2+ chars).

    Type-defensive: anything that is not a ``str`` (``None``, a test double, a float coming
    out of an untyped ontology row) tokenizes to ``[]`` rather than raising ``TypeError`` from
    ``re.findall``. Real callers hand this module whatever their ontology handed them.
    """
    if not isinstance(text, str):
        return []
    return [w.lower() for w in re.findall(r"[a-zA-Z]+", text) if len(w) >= 2]


def content_words(text: str) -> set[str]:
    """Extract meaningful (non-stopword) words from text."""
    return {w for w in tokenize(text) if w not in SEARCH_STOPWORDS}


def _as_text(value: object) -> str:
    """Coerce a maybe-string to text: non-``str`` values read as absent (``""``)."""
    return value if isinstance(value, str) else ""


# Strength of the specificity penalty at ``specificity_penalty=1.0`` (the historical constant):
# a candidate whose label is entirely extra words loses 40% of its score.
SPECIFICITY_PENALTY_WEIGHT = 0.4


def word_overlap(
    query_words: set[str],
    target_words: set[str],
    *,
    use_vectors: bool = False,
    word_similarity: WordSimilarity = _no_vector_similarity,
) -> float:
    """Bidirectional word overlap with prefix-match credit.

    Computes both forward (query -> target) and reverse (target -> query) overlap. Reverse
    overlap helps multi-concept queries match narrower targets. When ``use_vectors`` is set,
    words with 0.0 character-based match get a vector cosine fallback (capped at 0.5).
    """
    if not query_words or not target_words:
        return 0.0

    def _directional_overlap(source: list[str], dest: set[str]) -> float:
        matched = 0.0
        for sw in source:
            best = 0.0
            for dw in dest:
                if sw == dw:
                    best = 1.0
                    break
                elif len(sw) >= 3 and len(dw) >= 3:
                    if sw.startswith(dw) or dw.startswith(sw):
                        best = max(best, 0.8)
                    elif len(sw) >= 5 and len(dw) >= 5:
                        pfx = 0
                        for c1, c2 in zip(sw, dw, strict=False):
                            if c1 == c2:
                                pfx += 1
                            else:
                                break
                        if pfx >= 4 and pfx / min(len(sw), len(dw)) >= 0.7:
                            best = max(best, 0.7)
            if best == 0.0 and use_vectors and len(sw) >= 3:
                for dw in dest:
                    if len(dw) >= 3:
                        vec_sim = word_similarity(sw, dw)
                        if vec_sim > 0.25:
                            best = max(best, min(vec_sim, 0.5))
            matched += best
        return matched / len(source)

    # `matched += best` accumulates floats in `source` iteration order, and float addition is
    # not associative — so iterating the *set* made the returned overlap depend on
    # PYTHONHASHSEED. word_overlap("rules of arbitration" vs "Northern Mariana Islands") with
    # an injected `word_similarity` returned 0.21750000000000003 under seed 0 and
    # 0.21749999999999997 under seed 1. Only `source` needs a total order: the inner `dest`
    # loop resolves to a max (or an early exact-match break), which is order-independent.
    forward = _directional_overlap(sorted(query_words), target_words)

    reverse = 0.0
    if len(target_words) >= 2:
        reverse = _directional_overlap(sorted(target_words), query_words) * 0.75

    return max(forward, reverse)


def compute_relevance_score(
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
) -> float:
    """Score 0-100 based on word overlap between query and a candidate concept.

    **Type-defensive by contract (v0.3.0).** Every text argument is coerced: anything that is
    not a ``str`` at runtime — ``None`` (folio-python genuinely returns ``None`` for a concept
    with no preferred label), a test double, a number — reads as *absent* rather than raising
    ``TypeError`` out of ``re.findall``. A non-``str`` ``label`` scores 0.0; non-``str``
    synonym entries are skipped. Callers no longer need a coercion shim at their boundary.

    ``specificity_penalty`` scales the "candidate is more specific than the query" haircut.
    ``1.0`` (the default) is the historical behavior and must stay bit-identical; ``0.0``
    disables the penalty entirely. Consumers whose queries are *shorter than their targets by
    construction* — a 1-2 word claim name against a 3-5 word FOLIO label, e.g. *Habitability*
    → *Breach of Warranty of Habitability* — can damp it (0.3-0.5) instead of forking the
    scorer. Consumers that map taxonomy nodes, where an over-specific target IS an error,
    should leave it at 1.0.
    """
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
    if len(query_lower) >= 4 and query_lower in label_lower:
        label_score = 92.0
    elif (
        len(label_lower) >= 4
        and label_lower in query_lower
        and len(label_lower) / len(query_lower) > 0.3
    ):
        label_score = 88.0
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
        elif len(query_lower) >= 4 and query_lower in pref_lower:
            pref_score = 84.0
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
        if query_lower in def_lower:
            def_score = 60.0
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


def generate_search_terms(term: str) -> list[str]:
    """Generate search terms: full phrase, sub-phrases, individual content words, expansions.

    Ported from folio-mapper ``_generate_search_terms`` (minus the spaCy expansion, which is
    injected separately by consumers that install the ``spacy`` extra).
    """
    words = tokenize(term)
    content = content_words(term)

    terms = [term]

    if len(words) >= 3:
        for n in range(len(words) - 1, 1, -1):
            for i in range(len(words) - n + 1):
                sub = " ".join(words[i : i + n])
                if content_words(sub):
                    terms.append(sub)

    # `content` is a set, so both loops need a total order or the emitted term order varies
    # between processes: "Commercial Litigation" yielded [..., "commercial", "litigation", ...]
    # or [..., "litigation", "commercial", ...] depending on PYTHONHASHSEED, because the two
    # words are the same length. Longest-first (the intended ranking), then alphabetical.
    for w in sorted(content, key=lambda word: (-len(word), word)):
        if len(w) >= 3:
            terms.append(w)

    for w in sorted(content):
        for suffix in LEGAL_TERM_EXPANSIONS.get(w, []):
            terms.append(f"{w} {suffix}")

    seen: set[str] = set()
    result: list[str] = []
    for t in terms:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            result.append(t)
    return result
