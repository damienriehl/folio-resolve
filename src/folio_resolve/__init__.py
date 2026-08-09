"""folio-resolve — the shared FOLIO source-text-to-concept matching engine.

A lift-and-improve extraction of the matching intelligence that previously lived — and diverged —
across folio-mapper, folio-enrich, and folio-insights (informally shared through a fragile
``sys.path`` hack). This package is the single pinned source of truth: the word-order-invariant
scorer, span decomposition, the place-name / short-label gates, the alias blocklist, the
domain-prior judge, and the annotate primitives.

Public surface (see module docstrings for provenance):

* scoring       — ``compute_relevance_score`` (type-defensive; optional ``specificity_penalty``
                  weight), ``word_overlap``, ``LEGAL_TERM_EXPANSIONS``
* ontology      — ``Concept``, ``OntologyProvider``, ``InMemoryOntology``, ``FolioPythonProvider``
* entity_ruler  — ``FOLIOEntityRuler`` (pure-Python Aho-Corasick)
* reconciler    — ``Reconciler``, ``ConceptMatch``
* decompose     — ``decompose`` (conjunction split + shared-head)
* gates         — ``PlaceNameGate`` (extensible vocabulary), ``ShortLabelGate``
* blocklist     — ``AliasBlocklist`` (Action != Auction)
* sources       — ``SourceClassifier`` (metadata/front-matter exclusion)
* domain_prior  — ``DomainPrior``, ``DomainPriorSuggester``, ``TaxonomyNode`` (multi-tag)
* calibration   — ``ScoreCalibration`` (weak-band recalibration)
* judge         — ``Judge``, ``build_judge_prompt``, ``parse_judge_json``, ``strip_markdown_fences``
* lemma         — ``augment_labels`` (lemma-key index augmentation; ``[spacy]`` extra, build-time only)
* pipeline      — ``MatchPipeline`` (filter -> expand -> rank -> judge)
* resolve       — ``LabelResolver``, ``ResolvedConcept`` (decompose-first, branch-carrying)
* annotate      — ``ConceptTag``, ``TagVerdict``, ``Annotation``, ``FeedbackStore``, lifecycle
"""

from __future__ import annotations

__version__ = "0.4.0"

from .blocklist import AliasBlocklist, BlockedAlias, load_seed_blocklist
from .calibration import CalibrationSample, ScoreCalibration
from .decompose import decompose
from .domain_prior import DomainPrior, DomainPriorSuggester, SubjectTag, TagStatus, TaxonomyNode
from .entity_ruler import FOLIOEntityRuler
from .gates import GateDecision, PlaceNameGate, ShortLabelGate
from .judge import (
    Judge,
    build_judge_prompt,
    enforce_verdict,
    parse_judge_json,
    strip_markdown_fences,
)
from .lemma import (
    LEMMA_VERSION,
    Lemmatizer,
    SpacyNotInstalledError,
    augment_labels,
    compute_label_lemmas,
    load_lemma_cache,
    save_lemma_cache,
    spacy_lemmatizer,
)
from .ontology import (
    Concept,
    FolioPythonProvider,
    InMemoryOntology,
    LabelInfo,
    OntologyProvider,
    RecallOntology,
)
from .pipeline import MatchCandidate, MatchPipeline
from .recall import MultiStrategyRecall, RecallResult
from .reconciler import ConceptMatch, Reconciler, ReconciliationResult
from .resolve import (
    CONJUNCT_THRESHOLD,
    WHOLE_STRING_THRESHOLD,
    LabelResolver,
    ResolvedConcept,
)
from .scoring import (
    LEGAL_TERM_EXPANSIONS,
    SEARCH_STOPWORDS,
    SPECIFICITY_PENALTY_WEIGHT,
    compute_relevance_score,
    content_words,
    generate_search_terms,
    tokenize,
    word_overlap,
)
from .sources import SourceClassifier, SourceType
from .spec import BUILTIN_SPECS, CANON_SPEC, FOLIO_SPEC, OntologySpec

__all__ = [
    "BUILTIN_SPECS",
    "CANON_SPEC",
    "CONJUNCT_THRESHOLD",
    "FOLIO_SPEC",
    "LEGAL_TERM_EXPANSIONS",
    "LEMMA_VERSION",
    "SEARCH_STOPWORDS",
    "SPECIFICITY_PENALTY_WEIGHT",
    "WHOLE_STRING_THRESHOLD",
    "AliasBlocklist",
    "BlockedAlias",
    "CalibrationSample",
    "Concept",
    "ConceptMatch",
    "DomainPrior",
    "DomainPriorSuggester",
    "FOLIOEntityRuler",
    "FolioPythonProvider",
    "GateDecision",
    "InMemoryOntology",
    "Judge",
    "LabelInfo",
    "LabelResolver",
    "Lemmatizer",
    "MatchCandidate",
    "MatchPipeline",
    "MultiStrategyRecall",
    "OntologyProvider",
    "OntologySpec",
    "PlaceNameGate",
    "RecallOntology",
    "RecallResult",
    "Reconciler",
    "ReconciliationResult",
    "ResolvedConcept",
    "ScoreCalibration",
    "ShortLabelGate",
    "SourceClassifier",
    "SourceType",
    "SpacyNotInstalledError",
    "SubjectTag",
    "TagStatus",
    "TaxonomyNode",
    "__version__",
    "augment_labels",
    "build_judge_prompt",
    "compute_label_lemmas",
    "compute_relevance_score",
    "content_words",
    "decompose",
    "enforce_verdict",
    "generate_search_terms",
    "load_lemma_cache",
    "load_seed_blocklist",
    "parse_judge_json",
    "save_lemma_cache",
    "spacy_lemmatizer",
    "strip_markdown_fences",
    "tokenize",
    "word_overlap",
]
