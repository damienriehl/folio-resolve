"""Typed, deterministic span annotation results; no implicit time or random IDs."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from .index import MatchMethod


class RankedAnchor(BaseModel):
    id: str
    confidence: float
    match_method: MatchMethod
    rank: Literal["primary", "fallback"] = "primary"
    outranked_by: str | None = None


class FolioAnchor(RankedAnchor):
    label: str
    matched_label: str
    label_type: str
    source: str = "FOLIO"


class OpenGlossAnchor(RankedAnchor):
    headword: str
    pos: str
    sense_index: int
    domain: str | None
    tier: str
    source: str
    gloss: str


class TaggedSpan(BaseModel):
    id: str
    start: int
    end: int
    text: str
    contains: list[str] = Field(default_factory=list)
    contained_by: list[str] = Field(default_factory=list)
    folio: list[FolioAnchor] = Field(default_factory=list)
    opengloss: list[OpenGlossAnchor] = Field(default_factory=list)


class CrosswalkLink(BaseModel):
    span: str  # The span bearing the FOLIO subject.
    subject_id: str
    object_span: str
    object_id: str  # Raw OpenGloss sense_id.
    predicate_id: str
    confidence: float | None
    mapping_justification: str
    mapping_set_id: str
    mapping_set_version: str


class OpenGlossProvenance(BaseModel):
    dataset_id: str
    revision: str
    lexicon_digest: str


class FolioProvenance(BaseModel):
    release: str
    label_index_digest: str
    label_source: Literal["all_concepts", "all_labels"]


class CrosswalkProvenance(BaseModel):
    mapping_set_id: str
    mapping_set_version: str


class Provenance(BaseModel):
    folio_resolve_version: str
    tagger_version: str
    normalization: str = "nfkc-casefold-apostrophe-gap-v4"
    opengloss: OpenGlossProvenance | None = None
    folio: FolioProvenance | None = None
    crosswalk: CrosswalkProvenance | None = None


class TagResult(BaseModel):
    spans: list[TaggedSpan]
    links: list[CrosswalkLink]
    provenance: Provenance
    tagged_on: str | None = None

    def to_json(self) -> str:
        """Serialize with sorted keys, without absent data layers or an implicit date."""
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
