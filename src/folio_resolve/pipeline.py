"""The 4-stage matching pipeline — filter -> expand -> rank -> judge.

This is the build-once-use-many entry point. It unifies the lifted engines and the new v0
capabilities into one flow, mirroring folio-mapper's 4-stage orchestrator but with the Ch02
fixes wired in:

* **filter**  — entity-ruler + label search over the ontology produce raw candidates; the
  metadata/front-matter source hook can veto the whole unit up front.
* **expand**  — span decomposition (conjunction split + shared-head) and the semantic index add
  the candidates that label matching alone cannot reach (Presumptions -> Burdens of Proof).
* **rank**    — the alias blocklist drops homonyms (Action != Auction); the place-name and
  short-label gates demote pathological fuzzy hits; score calibration redraws the weak band.
* **judge**   — an optional LLM judge validates the survivors with the multi-tag domain prior
  threaded in (Defenses -> Litigation Defenses).

Every stage is optional/injectable, so a consumer can adopt as little or as much as it needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .blocklist import AliasBlocklist
from .calibration import ScoreCalibration
from .decompose import decompose
from .domain_prior import DomainPrior
from .embedding import BruteForceIndex
from .entity_ruler import FOLIOEntityRuler
from .gates import PlaceNameGate, ShortLabelGate
from .judge import Judge, build_judge_prompt, parse_judge_json
from .ontology import Concept, OntologyProvider
from .recall import MultiStrategyRecall
from .scoring import definition_context_score
from .sources import SourceClassifier


@dataclass
class MatchCandidate:
    iri: str
    label: str
    score: float
    branch: str = ""
    extraction_path: str = ""  # "entity_ruler" | "label_search" | "semantic" | "decomposition"
    surface_term: str = ""
    gated: bool = False
    gate_reason: str = ""
    rank_tiebreak_score: float = 0.0

    def as_probability(self, calibration: ScoreCalibration) -> float:
        return calibration.probability(self.score)


@dataclass
class MatchPipeline:
    """Wires the engines + Ch02 gates into a single ``match`` call."""

    ontology: OntologyProvider
    entity_ruler: FOLIOEntityRuler | None = None
    semantic_index: BruteForceIndex | None = None
    blocklist: AliasBlocklist = field(default_factory=AliasBlocklist)
    place_gate: PlaceNameGate = field(default_factory=PlaceNameGate)
    short_gate: ShortLabelGate = field(default_factory=ShortLabelGate)
    calibration: ScoreCalibration = field(default_factory=ScoreCalibration)
    source_classifier: SourceClassifier = field(default_factory=SourceClassifier)
    judge: Judge | None = None
    label_search_limit: int = 10
    score_floor: float = 45.0
    recall_engine: MultiStrategyRecall | None = None

    # -- Stage 1: filter --------------------------------------------------

    def _filter(self, surface_term: str) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        if self.entity_ruler is not None:
            for m in self.entity_ruler.find_matches(surface_term):
                concept = self.ontology.get_concept(m.entity_id)
                candidates.append(
                    MatchCandidate(
                        iri=m.entity_id,
                        label=(concept.label if concept else m.text),
                        score=m.confidence * 100.0,
                        branch=(concept.branch if concept else ""),
                        extraction_path="entity_ruler",
                        surface_term=surface_term,
                    )
                )
        for concept, score in self.ontology.search_by_label(surface_term, limit=self.label_search_limit):
            candidates.append(
                MatchCandidate(
                    iri=concept.iri,
                    label=concept.label,
                    score=score,
                    branch=concept.branch,
                    extraction_path="label_search",
                    surface_term=surface_term,
                )
            )
        return candidates

    # -- Stage 2: expand --------------------------------------------------

    def _expand(self, surface_term: str) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        if self.recall_engine is not None:
            for recalled in self.recall_engine.recall(surface_term):
                candidates.append(
                    MatchCandidate(
                        iri=recalled.concept.iri,
                        label=recalled.concept.label,
                        score=recalled.score,
                        branch=recalled.concept.branch,
                        extraction_path="multi_strategy_recall",
                        surface_term=surface_term,
                    )
                )
        parts = decompose(surface_term)
        # Skip the first element (the original) — already searched in _filter.
        for part in parts[1:]:
            for concept, score in self.ontology.search_by_label(part, limit=3):
                candidates.append(
                    MatchCandidate(
                        iri=concept.iri,
                        label=concept.label,
                        score=score,
                        branch=concept.branch,
                        extraction_path="decomposition",
                        surface_term=part,
                    )
                )
        if self.semantic_index is not None:
            for iri, label, cosine in self.semantic_index.query(surface_term, top_k=5):
                sem_concept = self.ontology.get_concept(iri)
                candidates.append(
                    MatchCandidate(
                        iri=iri,
                        label=label,
                        score=round(cosine * 100.0, 1),
                        branch=(sem_concept.branch if sem_concept else ""),
                        extraction_path="semantic",
                        surface_term=surface_term,
                    )
                )
        return candidates

    # -- Stage 3: rank ----------------------------------------------------

    def _rank(
        self,
        candidates: list[MatchCandidate],
        *,
        domains: list[str],
        heading_terms: set[str],
        context_text: str | None = None,
    ) -> list[MatchCandidate]:
        survivors: dict[str, MatchCandidate] = {}
        for cand in candidates:
            if self.blocklist.is_blocked(cand.surface_term, cand.iri, domains=domains):
                continue
            heading_match = cand.label.lower() in heading_terms
            place = self.place_gate.evaluate(
                query=cand.surface_term,
                label=cand.label,
                branch=cand.branch,
                score=cand.score,
                heading_context_match=heading_match,
            )
            short = self.short_gate.evaluate(query=cand.surface_term, label=cand.label, score=place.score)
            cand.score = short.score
            if context_text:
                concept = self.ontology.get_concept(cand.iri)
                cand.rank_tiebreak_score = definition_context_score(
                    context_text,
                    concept.definition if concept is not None else None,
                    anchor=cand.surface_term,
                )
            cand.gated = place.demoted or short.demoted
            cand.gate_reason = "; ".join(r for r in (place.reason, short.reason) if r)
            if cand.score < self.score_floor:
                continue
            # Keep the strongest primary/secondary ranking evidence per IRI.
            existing = survivors.get(cand.iri)
            if existing is None or (
                cand.score,
                cand.rank_tiebreak_score,
            ) > (
                existing.score,
                existing.rank_tiebreak_score,
            ):
                survivors[cand.iri] = cand
        ranked = sorted(
            survivors.values(),
            key=lambda candidate: (
                -candidate.score,
                -candidate.rank_tiebreak_score,
                candidate.iri,
            ),
        )
        return ranked

    # -- Stage 4: judge ---------------------------------------------------

    def _judge(
        self, text: str, candidates: list[MatchCandidate], *, document_type: str
    ) -> list[MatchCandidate]:
        if self.judge is None or not candidates:
            return candidates
        ranked_by_iri = {c.iri: c.score for c in candidates}
        payload = [{"iri_hash": c.iri, "label": c.label, "score": c.score} for c in candidates]
        system, user = build_judge_prompt(text, payload, document_type=document_type)
        judged = parse_judge_json(self.judge.complete(system, user), ranked_by_iri)
        by_iri = {c.iri: c for c in candidates}
        out: list[MatchCandidate] = []
        for j in judged:
            cand = by_iri[j.iri]
            if j.adjusted_score <= 0:
                continue
            cand.score = j.adjusted_score
            out.append(cand)
        return sorted(
            out,
            key=lambda candidate: (
                -candidate.score,
                -candidate.rank_tiebreak_score,
                candidate.iri,
            ),
        )

    # -- public API -------------------------------------------------------

    def match(
        self,
        surface_term: str,
        *,
        section_label: str = "",
        domain_prior: DomainPrior | None = None,
        heading_terms: set[str] | None = None,
        full_text: str | None = None,
        run_judge: bool = False,
    ) -> list[MatchCandidate]:
        """Match one surface term to ranked FOLIO candidates through all four stages."""
        if section_label and not self.source_classifier.is_taggable(section_label, surface_term):
            return []  # metadata/front-matter excluded (Ch02 unit d3c44e2a)

        domains = [t.label for t in domain_prior.active_tags()] if domain_prior else []
        document_type = domain_prior.as_judge_context() if domain_prior else ""

        raw = self._filter(surface_term) + self._expand(surface_term)
        ranked = self._rank(
            raw,
            domains=domains,
            heading_terms=heading_terms or set(),
            context_text=full_text,
        )
        if run_judge:
            ranked = self._judge(full_text or surface_term, ranked, document_type=document_type)
        return ranked

    def best_match(self, surface_term: str, **kwargs: object) -> Concept | None:
        """Convenience: the single best concept for a surface term, or None."""
        results = self.match(surface_term, **kwargs)  # type: ignore[arg-type]
        if not results:
            return None
        return self.ontology.get_concept(results[0].iri)
