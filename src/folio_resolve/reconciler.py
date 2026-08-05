"""Candidate reconciliation.

Lifted from folio-enrich ``reconciliation/reconciler.py``. Merges entity-ruler and LLM
candidate lists into a single set with a provenance category, applying a diminishing
confidence boost when both paths agree and gating low-confidence ruler-only hits. The
embedding-triage path resolves IRI conflicts via an injected similarity function (no hard
FAISS dependency here — the caller supplies it).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RULER_ONLY_MIN_CONFIDENCE = 0.60
EMBEDDING_AUTO_RESOLVE_THRESHOLD = 0.85

# similarity_batch(pairs) -> parallel list of cosine scores in [0, 1]
SimilarityBatch = Callable[[list[tuple[str, str]]], list[float]]


@dataclass
class ConceptMatch:
    concept_text: str
    folio_iri: str = ""
    folio_label: str = ""
    folio_definition: str = ""
    confidence: float = 0.0
    branch: str = ""
    source: str = ""


@dataclass
class ReconciliationResult:
    concept: ConceptMatch
    category: str  # "both_agree" | "ruler_only" | "llm_only" | "conflict_resolved"


def _diminishing_boost(base: float, max_boost: float = 0.05) -> float:
    return max_boost * (1.0 - base)


def _definition_overlap_score(context: str, definition: str) -> float:
    if not context or not definition:
        return 0.0
    stopwords = {"the", "a", "an", "of", "to", "in", "for", "and", "or", "is", "on", "at", "by", "with"}
    ctx_words = set(context.lower().split()) - stopwords
    def_words = set(definition.lower().split()) - stopwords
    if not ctx_words or not def_words:
        return 0.0
    return len(ctx_words & def_words) / max(len(ctx_words), len(def_words))


def _build_maps(
    concepts: list[ConceptMatch],
) -> tuple[dict[tuple[str, str], ConceptMatch], dict[str, list[ConceptMatch]]]:
    by_key: dict[tuple[str, str], ConceptMatch] = {}
    by_text: dict[str, list[ConceptMatch]] = {}
    for c in concepts:
        by_key[(c.concept_text.lower(), c.folio_iri or "")] = c
        by_text.setdefault(c.concept_text.lower(), []).append(c)
    return by_key, by_text


@dataclass
class Reconciler:
    """Merge EntityRuler and LLM concept identification results."""

    similarity_batch: SimilarityBatch | None = None
    index_size: int = field(default=0)

    def reconcile(
        self, ruler_concepts: list[ConceptMatch], llm_concepts: list[ConceptMatch]
    ) -> list[ReconciliationResult]:
        ruler_by_key, ruler_by_text = _build_maps(ruler_concepts)
        llm_by_key, llm_by_text = _build_maps(llm_concepts)

        results: list[ReconciliationResult] = []
        handled_ruler: set[tuple[str, str]] = set()
        handled_llm: set[tuple[str, str]] = set()

        # Pass 1: exact (text, IRI) agreement.
        # Iterate the ruler dict (insertion-ordered), NOT `set(a) | set(b)` — set iteration order
        # over string tuples varies with PYTHONHASHSEED, which made the result list order differ
        # between processes for identical input.
        for key in ruler_by_key:
            if key in llm_by_key:
                concept = llm_by_key[key]
                base = max(ruler_by_key[key].confidence, llm_by_key[key].confidence)
                concept.confidence = min(1.0, base + _diminishing_boost(base))
                concept.source = "reconciled"
                results.append(ReconciliationResult(concept=concept, category="both_agree"))
                handled_ruler.add(key)
                handled_llm.add(key)

        # Pass 2: cross-match empty-IRI concepts by text
        for key, concept in ruler_by_key.items():
            if key in handled_ruler or key[1]:
                continue
            for lc in llm_by_text.get(key[0], []):
                lkey = (key[0], lc.folio_iri or "")
                if lkey not in handled_llm:
                    base = max(concept.confidence, lc.confidence)
                    lc.confidence = min(1.0, base + _diminishing_boost(base))
                    lc.source = "reconciled"
                    results.append(ReconciliationResult(concept=lc, category="both_agree"))
                    handled_ruler.add(key)
                    handled_llm.add(lkey)
                    break

        for key, concept in llm_by_key.items():
            if key in handled_llm or key[1]:
                continue
            for rc in ruler_by_text.get(key[0], []):
                rkey = (key[0], rc.folio_iri or "")
                if rkey not in handled_ruler:
                    base = max(concept.confidence, rc.confidence)
                    rc.confidence = min(1.0, base + _diminishing_boost(base))
                    rc.source = "reconciled"
                    results.append(ReconciliationResult(concept=rc, category="both_agree"))
                    handled_llm.add(key)
                    handled_ruler.add(rkey)
                    break

        # Pass 3: remaining unmatched
        for key, concept in ruler_by_key.items():
            if key in handled_ruler:
                continue
            if concept.confidence >= RULER_ONLY_MIN_CONFIDENCE:
                concept.source = "entity_ruler"
                results.append(ReconciliationResult(concept=concept, category="ruler_only"))
            else:
                logger.debug("Filtered ruler-only '%s' (%.2f)", key, concept.confidence)

        for key, concept in llm_by_key.items():
            if key in handled_llm:
                continue
            concept.source = "llm"
            results.append(ReconciliationResult(concept=concept, category="llm_only"))

        return results

    def reconcile_with_embedding_triage(
        self, ruler_concepts: list[ConceptMatch], llm_concepts: list[ConceptMatch]
    ) -> list[ReconciliationResult]:
        """Like :meth:`reconcile` but resolves IRI conflicts via embedding similarity."""
        if self.similarity_batch is None or self.index_size == 0:
            return self.reconcile(ruler_concepts, llm_concepts)

        ruler_by_key, _ = _build_maps(ruler_concepts)
        llm_by_key, llm_by_text = _build_maps(llm_concepts)

        results: list[ReconciliationResult] = []
        handled_ruler: set[tuple[str, str]] = set()
        handled_llm: set[tuple[str, str]] = set()
        conflicts: list[tuple[str, ConceptMatch, ConceptMatch]] = []

        # Ruler insertion order, not set order — see the note in `reconcile`.
        for key in ruler_by_key:
            if key in llm_by_key:
                lc = llm_by_key[key]
                base = max(ruler_by_key[key].confidence, lc.confidence)
                lc.confidence = min(1.0, base + _diminishing_boost(base))
                lc.source = "reconciled"
                results.append(ReconciliationResult(concept=lc, category="both_agree"))
                handled_ruler.add(key)
                handled_llm.add(key)

        for key, concept in ruler_by_key.items():
            if key in handled_ruler:
                continue
            for lc in llm_by_text.get(key[0], []):
                lkey = (key[0], lc.folio_iri or "")
                if lkey in handled_llm:
                    continue
                if not concept.folio_iri or not lc.folio_iri:
                    winner = concept if concept.folio_iri else lc
                    base = max(concept.confidence, lc.confidence)
                    winner.confidence = min(1.0, base + _diminishing_boost(base))
                    winner.source = "reconciled"
                    results.append(ReconciliationResult(concept=winner, category="both_agree"))
                    handled_ruler.add(key)
                    handled_llm.add(lkey)
                    break
                if concept.folio_iri != lc.folio_iri:
                    conflicts.append((key[0], concept, lc))
                    handled_ruler.add(key)
                    handled_llm.add(lkey)
                    break

        for key, concept in ruler_by_key.items():
            if key in handled_ruler:
                continue
            if concept.confidence >= RULER_ONLY_MIN_CONFIDENCE:
                concept.source = "entity_ruler"
                results.append(ReconciliationResult(concept=concept, category="ruler_only"))

        for key, concept in llm_by_key.items():
            if key in handled_llm:
                continue
            concept.source = "llm"
            results.append(ReconciliationResult(concept=concept, category="llm_only"))

        if conflicts:
            pairs: list[tuple[str, str]] = []
            for text, rc, lc in conflicts:
                pairs.append((text, rc.folio_label or rc.concept_text))
                pairs.append((text, lc.folio_label or lc.concept_text))
            sims = self.similarity_batch(pairs)
            for i, (text, rc, lc) in enumerate(conflicts):
                ruler_sim, llm_sim = sims[i * 2], sims[i * 2 + 1]
                if max(ruler_sim, llm_sim) > EMBEDDING_AUTO_RESOLVE_THRESHOLD:
                    winner, sim = (rc, ruler_sim) if ruler_sim >= llm_sim else (lc, llm_sim)
                    winner.source = "reconciled"
                    winner.confidence = max(winner.confidence, sim)
                    results.append(ReconciliationResult(concept=winner, category="conflict_resolved"))
                else:
                    rc_overlap = _definition_overlap_score(text, rc.folio_definition or "")
                    lc_overlap = _definition_overlap_score(text, lc.folio_definition or "")
                    if rc_overlap > lc_overlap and rc_overlap > 0:
                        rc.source = "reconciled"
                        results.append(ReconciliationResult(concept=rc, category="conflict_resolved"))
                    elif lc_overlap > rc_overlap and lc_overlap > 0:
                        lc.source = "reconciled"
                        results.append(ReconciliationResult(concept=lc, category="conflict_resolved"))
                    else:
                        rc.source = lc.source = "reconciled"
                        results.append(ReconciliationResult(concept=rc, category="conflict_resolved"))
                        results.append(ReconciliationResult(concept=lc, category="conflict_resolved"))

        return results
