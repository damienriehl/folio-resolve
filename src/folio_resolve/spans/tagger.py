"""Build indexes once, then annotate both layers without merging their identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .. import __version__
from ..entity_ruler import (
    _ALTERNATIVE_CONFIDENCE,
    _PREFERRED_CONFIDENCE,
    _PREFERRED_TIER,
    _STOPWORDS,
    MIN_PATTERN_LENGTH,
)
from ..ontology import Concept, OntologyProvider
from .crosswalk import Crosswalk
from .index import NGramIndex
from .lexicon import OpenGlossLexicon, Sense
from .models import (
    CrosswalkLink,
    CrosswalkProvenance,
    FolioAnchor,
    FolioProvenance,
    OpenGlossAnchor,
    OpenGlossProvenance,
    Provenance,
    TaggedSpan,
    TagResult,
)
from .tokenize import normalize

TAGGER_VERSION = "1.0.0"
_EXACT_CONFIDENCE = 1.0
_PLURAL_CONFIDENCE = 0.9


@dataclass(frozen=True)
class _FolioLabel:
    concept: Concept
    text: str
    label_type: str

    @property
    def confidence(self) -> float:
        return (
            _PREFERRED_CONFIDENCE if self.label_type in _PREFERRED_TIER else _ALTERNATIVE_CONFIDENCE
        )


def _concept_labels(concept: Concept) -> Iterable[_FolioLabel]:
    for text in (concept.label, concept.preferred_label):
        if text:
            yield _FolioLabel(concept, text, "preferred")
    for text in concept.alternative_labels:
        if text:
            yield _FolioLabel(concept, text, "alternative")


def _folio_index(ontology: OntologyProvider) -> tuple[NGramIndex[_FolioLabel], FolioProvenance]:
    all_concepts = getattr(ontology, "all_concepts", None)
    labels: list[_FolioLabel] = []
    source: Literal["all_concepts", "all_labels"]
    if callable(all_concepts):
        source = "all_concepts"
        for concept in all_concepts():
            labels.extend(_concept_labels(concept))
    else:
        source = "all_labels"
        concepts: dict[str, Concept] = {}
        for text, info in ontology.all_labels().items():
            labels.append(_FolioLabel(info.concept, text, info.label_type))
            concepts[info.concept.iri] = info.concept
        for concept in concepts.values():
            labels.extend(_concept_labels(concept))
    # Prefer the strongest label evidence when normalization aliases the same concept.
    labels.sort(
        key=lambda label: (-label.confidence, label.text, label.label_type, label.concept.iri)
    )
    entries: dict[tuple[tuple[str, ...], str], _FolioLabel] = {}
    for label in labels:
        if len(label.text) < MIN_PATTERN_LENGTH or label.text.casefold() in _STOPWORDS:
            continue
        key = normalize(label.text)
        if key:
            entries.setdefault((key, label.concept.iri), label)
    index = NGramIndex[_FolioLabel]()
    digest_rows = []
    for (key, iri), label in sorted(entries.items()):
        index.add(label.text, label)
        digest_rows.append(
            (
                key,
                iri,
                label.concept.preferred_label or label.concept.label,
                label.text,
                label.label_type,
                label.confidence,
            )
        )
    digest = hashlib.sha256(
        json.dumps(digest_rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return index, FolioProvenance(release="", label_index_digest=digest, label_source=source)


class SpanTagger:
    """Opt-in lookup annotation over FOLIO labels and/or compiled OpenGloss senses.

    A FOLIO layer requires a caller-supplied release identity. Its content digest
    records the label index actually used, including all concepts sharing a label.
    """

    def __init__(
        self,
        ontology: OntologyProvider | None = None,
        lexicon: OpenGlossLexicon | None = None,
        crosswalk: Crosswalk | None = None,
        *,
        folio_release: str | None = None,
    ) -> None:
        if ontology is None and lexicon is None:
            raise ValueError("SpanTagger requires at least one layer: ontology or lexicon")
        if ontology is not None and not folio_release:
            raise ValueError("The FOLIO layer requires a caller-supplied folio_release")
        self._lexicon = lexicon
        self._crosswalk = crosswalk if ontology is not None and lexicon is not None else None
        self._folio = NGramIndex[_FolioLabel]()
        self._opengloss = NGramIndex[Sense]()
        self._provenance = Provenance(
            folio_resolve_version=__version__,
            tagger_version=TAGGER_VERSION,
        )
        if ontology is not None:
            self._folio, metadata = _folio_index(ontology)
            metadata.release = folio_release or ""
            self._provenance.folio = metadata
        if lexicon is not None:
            for senses in lexicon.by_headword.values():
                for sense in senses:
                    self._opengloss.add(sense.headword, sense)
            self._provenance.opengloss = OpenGlossProvenance(
                dataset_id=lexicon.manifest.dataset_id,
                revision=lexicon.manifest.revision,
                lexicon_digest=lexicon.manifest.content_sha256,
            )
        if self._crosswalk is not None:
            self._provenance.crosswalk = CrosswalkProvenance(
                mapping_set_id=self._crosswalk.metadata["mapping_set_id"],
                mapping_set_version=self._crosswalk.metadata["mapping_set_version"],
            )

    def tag(
        self,
        text: str,
        regions: Iterable[tuple[int, int]] | None = None,
        tagged_on: str | None = None,
    ) -> TagResult:
        allowed = None if regions is None else tuple(regions)
        content_word = None if self._lexicon is None else self._lexicon.is_content_word
        spans: dict[tuple[int, int], TaggedSpan] = {}

        def span_at(start: int, end: int) -> TaggedSpan:
            key = (start, end)
            if key not in spans:
                spans[key] = TaggedSpan(
                    id=f"s{start}-{end}", start=start, end=end, text=text[start:end]
                )
            return spans[key]

        for hit in self._folio.lookup(text, regions=allowed, content_word=content_word):
            span_at(hit.start, hit.end).folio = sorted(
                [
                    FolioAnchor(
                        id=label.concept.iri,
                        label=label.concept.preferred_label or label.concept.label,
                        matched_label=label.text,
                        label_type=label.label_type,
                        confidence=label.confidence,
                        match_method=hit.match_method,
                    )
                    for label in hit.payloads
                ],
                key=lambda anchor: anchor.id,
            )
        for og_hit in self._opengloss.lookup(
            text,
            regions=allowed,
            noun=lambda sense: sense.pos.casefold() == "noun",
            content_word=content_word,
        ):
            span_at(og_hit.start, og_hit.end).opengloss = sorted(
                [
                    OpenGlossAnchor(
                        id=sense.sense_id,
                        headword=sense.headword,
                        pos=sense.pos,
                        sense_index=sense.sense_index,
                        domain=sense.domain,
                        tier=sense.tier,
                        source=sense.source,
                        gloss=sense.gloss,
                        match_method=og_hit.match_method,
                        confidence=(
                            _EXACT_CONFIDENCE
                            if og_hit.match_method == "exact"
                            else _PLURAL_CONFIDENCE
                        ),
                    )
                    for sense in og_hit.payloads
                ],
                key=lambda anchor: anchor.id,
            )
        ordered = sorted(spans.values(), key=lambda span: (span.start, -span.end))
        # Retain every strict containment, not just immediate parent/child relationships.
        for span in ordered:
            containers = [
                other
                for other in ordered
                if other is not span and other.start <= span.start and span.end <= other.end
            ]
            span.contained_by = [other.id for other in containers]
            span.contains = [
                other.id
                for other in ordered
                if other is not span and span.start <= other.start and other.end <= span.end
            ]
            for layer in ("folio", "opengloss"):
                anchored = [other for other in containers if getattr(other, layer)]
                if anchored:
                    nearest = min(
                        anchored,
                        key=lambda other: (other.end - other.start, other.start, -other.end),
                    )
                    for anchor in getattr(span, layer):
                        anchor.rank = "fallback"
                        anchor.outranked_by = nearest.id
        links = self._join(ordered)
        return TagResult(
            spans=ordered,
            links=links,
            provenance=self._provenance.model_copy(deep=True),
            tagged_on=tagged_on,
        )

    def _join(self, spans: list[TaggedSpan]) -> list[CrosswalkLink]:
        if self._crosswalk is None:
            return []
        by_id = {span.id: span for span in spans}
        links: list[CrosswalkLink] = []
        for span in spans:
            for anchor in span.folio:
                for mapping in self._crosswalk.for_subject(anchor.id):
                    for target_id in [span.id, *span.contains]:
                        if any(a.id == mapping.object_id for a in by_id[target_id].opengloss):
                            links.append(
                                CrosswalkLink(
                                    span=span.id,
                                    subject_id=anchor.id,
                                    object_span=target_id,
                                    object_id=mapping.object_id,
                                    predicate_id=mapping.predicate_id,
                                    confidence=mapping.confidence,
                                    mapping_justification=mapping.mapping_justification,
                                    mapping_set_id=mapping.mapping_set_id,
                                    mapping_set_version=mapping.mapping_set_version,
                                )
                            )
        return sorted(
            links,
            key=lambda link: (
                link.span,
                link.subject_id,
                link.predicate_id,
                link.object_id,
                link.object_span,
            ),
        )
