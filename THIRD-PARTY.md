# Third-Party Components & Attribution

`folio-resolve` is MIT-licensed. This file logs (1) the internal components lifted into this
package and (2) every third-party dependency with its license, per house policy.

## Components extracted from sibling repos (lift-and-improve)

All three source repos are Damien Riehl's own MIT-licensed projects; this is a consolidation, not
a third-party incorporation. Logged here for provenance.

| Component | Source repo · file | Reused as |
|---|---|---|
| Word-order-invariant scorer (`_compute_relevance_score`, `_word_overlap`, `_tokenize`, `_content_words`, `LEGAL_TERM_EXPANSIONS`, `SEARCH_STOPWORDS`, `BRANCH_SIGNAL_WORDS`) | folio-mapper · `backend/app/services/folio_service.py` | `scoring.py` |
| Multi-strategy search term generation | folio-mapper · `_generate_search_terms`; folio-enrich · `folio/search.py` (itself "ported from folio-mapper") | `scoring.generate_search_terms`, `pipeline.py` |
| 4-stage pipeline (filter → expand → rank → judge) | folio-mapper · `backend/app/services/pipeline/` | `pipeline.py` |
| LLM judge verdict enforcement + 90+/70-89/50-69 calibration prompt | folio-mapper · `pipeline/stage3_judge.py`, `pipeline/prompts.py` | `judge.py` |
| FAISS embedding index (IndexFlatIP, `all-MiniLM-L6-v2`, disk cache) | folio-mapper · `embedding/folio_index.py` | `embedding.py` (Protocol + optional adapter) |
| Aho-Corasick matcher (containment-aware overlap, word boundaries) | folio-enrich · `matching/aho_corasick.py` | `matching/aho_corasick.py` (reimplemented pure-Python; C dep dropped) |
| Entity ruler + pattern builder (label_type-encoded IDs) | folio-enrich · `entity_ruler/{ruler,pattern_builder}.py` | `entity_ruler.py` |
| Reconciler (diminishing boost, ruler-only gate, embedding triage) | folio-enrich · `reconciliation/reconciler.py` | `reconciler.py` |
| Domain-prior judge (contextual rerank + branch judge threading document_type) | folio-enrich · `llm/prompts/contextual_rerank.py`, `concept/branch_judge.py` | `judge.py`, `domain_prior.py` |
| Ontology-neutral spec layer (FOLIO_SPEC + CANON_SPEC, lemma denylist) | folio-enrich · `ontology/spec.py` | `spec.py` |
| Annotate models (Span, StageEvent, FeedbackItem, Annotation, FeedbackEntry, InsightsSummary) | folio-enrich · `models/annotation.py`, `models/feedback.py` | `annotate/models.py` |
| Feedback store + insights aggregation | folio-enrich · `storage/feedback_store.py` | `annotate/feedback_store.py` |
| Reject/restore/promote/cascade/bulk-reject lifecycle | folio-enrich · `api/routes/enrich.py` | `annotate/lifecycle.py` |
| Boundary-sweep span layout | folio-enrich · `frontend/index.html:renderAnnotatedText` | `annotate/render.py` (ported to Python) |

New in this package (no prior implementation): `decompose.py`, `gates.py`, `blocklist.py`,
`sources.py`, the multi-tag `domain_prior.py`, `calibration.py`, and the per-tag `TagVerdict`.

## Runtime dependencies

| Package | License | Used for | Tier |
|---|---|---|---|
| [pydantic](https://github.com/pydantic/pydantic) | MIT | annotate data models | core (required) |
| [folio-python](https://pypi.org/project/folio-python/) | MIT | live FOLIO ontology + `search_by_label` | optional (`folio` extra) |
| [faiss-cpu](https://github.com/facebookresearch/faiss) | MIT | ANN index for the semantic path | optional (`embedding` extra) |
| [sentence-transformers](https://github.com/UKPLab/sentence-transformers) | Apache-2.0 | `all-MiniLM-L6-v2` local embeddings | optional (`embedding` extra) |
| [numpy](https://numpy.org) | BSD-3-Clause | vector math for embeddings | optional (`embedding` extra) |
| [spaCy](https://github.com/explosion/spaCy) | MIT | optional EntityRuler adapter | optional (`spacy` extra) |

The **pure-Python Aho-Corasick** implementation in `matching/aho_corasick.py` deliberately removes
the `pyahocorasick` (BSD-3) C-extension dependency that folio-enrich required.

## Dev dependencies

| Package | License |
|---|---|
| [pytest](https://github.com/pytest-dev/pytest) | MIT |
| [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) | Apache-2.0 |
| [mypy](https://github.com/python/mypy) | MIT |
| [ruff](https://github.com/astral-sh/ruff) | MIT |

## Build dependencies

| Package | License | Used for |
|---|---|---|
| [hatchling](https://github.com/pypa/hatch) | MIT | PEP 517 build backend (`build-system.requires`) |

## Ontology data

FOLIO (Federated Open Legal Information Ontology) — CC-BY-4.0, © SALI Alliance. This package does
not vendor FOLIO; it is fetched at runtime via `folio-python`. Consumers displaying FOLIO concepts
should carry the CC-BY attribution.

## OpenGloss span-tagging data and build tooling

| Component | License | Used for |
|---|---|---|
| [pyarrow](https://arrow.apache.org/docs/python/) | Apache-2.0 | Optional `opengloss` extra and development tool for compiling parquet; never required to load or tag with a lexicon |
| [OpenGloss v2.4](https://huggingface.co/datasets/mjbommar/opengloss-v2.4-senses) | CC BY 4.0 | Sense data in compiled lexicons and the small test extract, pinned to revision `c64e480015b8c84f9d08469d4dfd64a7a8fb89bd` |
| [WordNet 3.0](https://wordnet.princeton.edu/license-and-commercial-use) | Princeton WordNet license | OpenGloss senses whose `source` is `wordnet-3.0`; these retain their source on every annotation |

OpenGloss by Michael J. Bommarito II, from the OpenGloss v2.4 release,
https://huggingface.co/datasets/mjbommar/opengloss-v2.4-senses, licensed CC BY 4.0.
The test extract retains the selected senses' original fields; compilation changes their
storage format only.

WordNet 3.0: Copyright 2006 by Princeton University. All rights reserved.
WordNet and its associated data are distributed under the Princeton WordNet license;
the license and disclaimer are available at the link above. OpenGloss-native senses
and WordNet-derived senses remain distinguishable through the `source` field.
