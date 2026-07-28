"""Gold label -> FOLIO IRI resolution (U2, R2).

R2 forbids resolving gold through the pipeline under test, so this module is a plain lookup over
a label index built straight from the ontology's own labels, walked in a fixed ladder:

1. ``exact_preferred``      -- the cell text *is* a preferred label (case-insensitive)
2. ``exact_alternative``    -- the cell text is an alternative/hidden label
3. ``normalized_preferred`` -- matches only after NFKC / dash / whitespace normalization
4. ``normalized_alternative``
5. ``lemma_variant``        -- matches a deterministic singular/plural variant
6. ``legacy_iri``           -- IRI cells rewritten from the ``lmss.sali.org`` namespace (AE3)
7. ``unresolved``           -- goes to the resolution batch, never silently dropped

Every resolution reports which rung fired, so the gold manifest can publish a branch histogram
and the audit gate can see how much of the gold rests on fuzzy rungs. Labels that resolve only
at rungs 3-5 are additionally logged by the caller (KTD6).

Ambiguous labels (687 preferred labels in FOLIO 2.0.0 are shared by two or more concepts) pick
the lexicographically smallest IRI so the build is deterministic, and are flagged so the audit
gate can adjudicate them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .normalize import (
    FOLIO_IRI_PREFIX,
    is_iri_like,
    label_key,
    normalize_iri,
    normalize_label,
    plural_variants,
    split_compound_value,
)


@dataclass(frozen=True, slots=True)
class IndexedConcept:
    """One ontology concept reduced to what gold resolution needs."""

    iri: str
    preferred_labels: tuple[str, ...] = ()
    alternative_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of resolving one gold cell value."""

    raw: str
    normalized: str
    iri: str | None
    branch: str
    parse_branch: str = "plain"
    candidates: tuple[str, ...] = ()
    ambiguous: bool = False
    note: str = ""

    @property
    def resolved(self) -> bool:
        return self.iri is not None


def _add(index: dict[str, list[str]], key: str, iri: str) -> None:
    if not key:
        return
    bucket = index.setdefault(key, [])
    if iri not in bucket:
        bucket.append(iri)


@dataclass
class LabelIndex:
    """Label -> IRI lookup tables, one per ladder rung."""

    exact_preferred: dict[str, list[str]] = field(default_factory=dict)
    exact_alternative: dict[str, list[str]] = field(default_factory=dict)
    norm_preferred: dict[str, list[str]] = field(default_factory=dict)
    norm_alternative: dict[str, list[str]] = field(default_factory=dict)
    iris: frozenset[str] = frozenset()
    labels_by_iri: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_concepts(cls, concepts: Iterable[IndexedConcept]) -> LabelIndex:
        index = cls()
        exact_p: dict[str, list[str]] = {}
        exact_a: dict[str, list[str]] = {}
        norm_p: dict[str, list[str]] = {}
        norm_a: dict[str, list[str]] = {}
        iris: list[str] = []
        labels: dict[str, str] = {}
        for concept in concepts:
            iris.append(concept.iri)
            if concept.preferred_labels:
                labels[concept.iri] = concept.preferred_labels[0]
            elif concept.alternative_labels:
                labels[concept.iri] = concept.alternative_labels[0]
            for label in concept.preferred_labels:
                _add(exact_p, label.casefold(), concept.iri)
                _add(norm_p, label_key(label), concept.iri)
            for label in concept.alternative_labels:
                _add(exact_a, label.casefold(), concept.iri)
                _add(norm_a, label_key(label), concept.iri)
        index.exact_preferred = {k: sorted(v) for k, v in exact_p.items()}
        index.exact_alternative = {k: sorted(v) for k, v in exact_a.items()}
        index.norm_preferred = {k: sorted(v) for k, v in norm_p.items()}
        index.norm_alternative = {k: sorted(v) for k, v in norm_a.items()}
        index.iris = frozenset(iris)
        index.labels_by_iri = labels
        return index

    @property
    def label_count(self) -> int:
        return len(self.norm_preferred) + len(self.norm_alternative)

    def label_for(self, iri: str) -> str:
        return self.labels_by_iri.get(iri, "")


def resolve_label(raw: str, index: LabelIndex) -> Resolution:
    """Walk the R2 ladder for one label string."""
    normalized = normalize_label(raw)
    exact_key = raw.casefold()
    norm_key = label_key(raw)
    ladder: tuple[tuple[dict[str, list[str]], str, str], ...] = (
        (index.exact_preferred, exact_key, "exact_preferred"),
        (index.exact_alternative, exact_key, "exact_alternative"),
        (index.norm_preferred, norm_key, "normalized_preferred"),
        (index.norm_alternative, norm_key, "normalized_alternative"),
    )
    for table, key, branch in ladder:
        hits = table.get(key, [])
        if hits:
            return Resolution(
                raw=raw,
                normalized=normalized,
                iri=min(hits),
                branch=branch,
                candidates=tuple(hits),
                ambiguous=len(hits) > 1,
            )
    for variant in plural_variants(raw):
        for variant_table in (index.norm_preferred, index.norm_alternative):
            hits = variant_table.get(label_key(variant), [])
            if hits:
                return Resolution(
                    raw=raw,
                    normalized=normalized,
                    iri=min(hits),
                    branch="lemma_variant",
                    candidates=tuple(hits),
                    ambiguous=len(hits) > 1,
                    note=f"variant={variant}",
                )
    return Resolution(raw=raw, normalized=normalized, iri=None, branch="unresolved")


def resolve_iri_value(raw: str, index: LabelIndex) -> Resolution:
    """Normalize an IRI cell into the FOLIO namespace and verify it exists in the ontology."""
    normalized_iri = normalize_iri(raw)
    normalized = normalize_label(raw)
    if normalized_iri is None:
        return Resolution(raw=raw, normalized=normalized, iri=None, branch="unresolved")
    branch = "legacy_iri" if not normalize_label(raw).startswith(FOLIO_IRI_PREFIX) else "iri_exact"
    if normalized_iri in index.iris:
        return Resolution(
            raw=raw,
            normalized=normalized_iri,
            iri=normalized_iri,
            branch=branch,
            parse_branch="iri",
        )
    return Resolution(
        raw=raw,
        normalized=normalized_iri,
        iri=None,
        branch="unresolved",
        parse_branch="iri",
        note="iri_not_in_ontology",
    )


def resolve_gold_value(raw: str, index: LabelIndex, *, compound: bool = False) -> Resolution:
    """Resolve one gold cell value, optionally parsing ``Bucket: Concept`` compounds (KTD6).

    Compound candidate order is right-hand side first, whole string second, bucket last; the
    branch that fired is recorded on the returned :class:`Resolution`.
    """
    if is_iri_like(raw):
        return resolve_iri_value(raw, index)
    if not compound:
        return resolve_label(raw, index)

    bucket, right = split_compound_value(raw)
    if not right and bucket is None:
        return resolve_label(raw, index)

    candidates: list[tuple[str, str]] = []
    for position, value in enumerate(right):
        candidates.append((value, "rhs_last" if position == 0 else "rhs_first"))
    candidates.append((normalize_label(raw), "whole"))
    if bucket:
        candidates.append((bucket, "bucket"))

    for value, parse_branch in candidates:
        resolution = resolve_label(value, index)
        if resolution.resolved:
            return Resolution(
                raw=raw,
                normalized=resolution.normalized,
                iri=resolution.iri,
                branch=resolution.branch,
                parse_branch=parse_branch,
                candidates=resolution.candidates,
                ambiguous=resolution.ambiguous,
                note=resolution.note,
            )
    return Resolution(
        raw=raw,
        normalized=normalize_label(raw),
        iri=None,
        branch="unresolved",
        parse_branch="unresolved",
    )


# --------------------------------------------------------------------------------------
# Ontology loading (offline, from the folio-python cache)
# --------------------------------------------------------------------------------------

FOLIO_CACHE_DIR = Path.home() / ".folio" / "cache"
FOLIO_GITHUB_OWNER = "alea-institute"
FOLIO_GITHUB_REPO = "FOLIO"
FOLIO_GITHUB_BRANCH = "2.0.0"


class OntologyCacheError(RuntimeError):
    """Raised when the pinned FOLIO cache file is missing (KTD7: never fall through to network)."""


def folio_cache_file(
    *,
    cache_dir: Path = FOLIO_CACHE_DIR,
    owner: str = FOLIO_GITHUB_OWNER,
    repo: str = FOLIO_GITHUB_REPO,
    branch: str = FOLIO_GITHUB_BRANCH,
) -> Path:
    """The concrete cache file folio-python will load, computed the same way it does.

    folio-python keys its GitHub cache on ``blake2b("<owner>/<repo>/<branch>")``
    (``folio/graph.py::load_cache``); resolving it here lets the gold manifest pin the exact
    ontology bytes the build saw.
    """
    key = f"{owner}/{repo}/{branch}"
    digest = hashlib.blake2b(key.encode()).hexdigest()
    return cache_dir / "github" / f"{digest}.owl"


def ontology_cache_sha256(path: Path | None = None) -> str:
    """SHA-256 of the pinned ontology cache file; raises when it is absent."""
    cache_path = path or folio_cache_file()
    if not cache_path.exists():
        raise OntologyCacheError(
            f"FOLIO ontology cache file not found: {cache_path} — refusing to fetch over the network"
        )
    return hashlib.sha256(cache_path.read_bytes()).hexdigest()


def folio_python_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("folio-python")
    except PackageNotFoundError:  # pragma: no cover - only when the extra is absent
        return "unknown"


def index_from_folio(folio: Any) -> LabelIndex:
    """Build the label index from a constructed ``folio.FOLIO`` graph.

    Preferred rung: ``label`` and ``preferred_label``. Alternative rung: ``alternative_labels``
    (which in FOLIO 2.0.0 already carries translations) plus ``hidden_label``.
    """
    concepts: list[IndexedConcept] = []
    for owl in getattr(folio, "classes", []):
        iri = getattr(owl, "iri", "") or ""
        if FOLIO_IRI_PREFIX not in iri:
            continue
        preferred = [
            text
            for text in (getattr(owl, "preferred_label", None), getattr(owl, "label", None))
            if isinstance(text, str) and text.strip()
        ]
        alternative = [
            text
            for source in ("alternative_labels", "hidden_label")
            for text in (getattr(owl, source, None) or [])
            if isinstance(text, str) and text.strip()
        ]
        concepts.append(
            IndexedConcept(
                iri=iri,
                preferred_labels=tuple(dict.fromkeys(preferred)),
                alternative_labels=tuple(dict.fromkeys(alternative)),
            )
        )
    return LabelIndex.from_concepts(concepts)


def load_folio_index() -> tuple[LabelIndex, str, str]:
    """Load the ontology offline and return ``(index, ontology_sha256, folio_python_version)``.

    The cache file is hashed *before* construction and its absence aborts the run, so a build can
    never silently score against a network-fetched ontology (KTD7).
    """
    cache_sha256 = ontology_cache_sha256()
    from folio import FOLIO

    return index_from_folio(FOLIO()), cache_sha256, folio_python_version()
