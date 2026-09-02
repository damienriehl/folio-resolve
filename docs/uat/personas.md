# User acceptance personas

These personas represent documented library consumers and repository roles. They are not invented
end users, and all examples used by their stories are synthetic.

## PI — Pipeline integrator

- **Stands for:** Developers building document and intake pipelines, especially the
  `folio-insights` matching paths and `alea-intake` claim-classification path.
- **Goal:** Turn source text into stable, ranked concepts through one composed entry point while
  retaining control over optional enrichment stages.
- **Public entry points:** `MatchPipeline`, `LabelResolver`, `SourceClassifier`, `DomainPrior`,
  `Concept`, and `InMemoryOntology`.
- **Sources:** [README: What it does](../../README.md#what-it-does),
  [README: Quick start](../../README.md#quick-start), and
  [migration schedule: folio-insights and alea-intake](../migration/SCHEDULE.md).

## SI — Scoring-only integrator

- **Stands for:** Developers embedding the scorer and search-term helpers into an existing engine,
  as `folio-mapper` and `alea-intake` do.
- **Goal:** Obtain calibrated, deterministic relevance scores and recall terms without adopting
  the full pipeline.
- **Public entry points:** `compute_relevance_score`, `content_words`, `word_overlap`,
  `generate_search_terms`, `LEGAL_TERM_EXPANSIONS`, and `SPECIFICITY_PENALTY_WEIGHT`.
- **Sources:** [README: Reading the capability table](../../README.md#reading-that-table-precisely),
  [README: Consumer-driven API additions](../../README.md#consumer-driven-api-additions-v030),
  and [migration schedule: folio-mapper](../migration/SCHEDULE.md).

## RI — Reconciliation integrator

- **Stands for:** Developers combining exact spans, ranked or judged candidates, and local policy,
  especially `folio-enrich`.
- **Goal:** Produce one clean, provenance-carrying candidate set while rejecting known homonyms and
  inappropriate place-name matches.
- **Public entry points:** `FOLIOEntityRuler`, `AliasBlocklist`, `PlaceNameGate`, `ShortLabelGate`,
  `LabelResolver`, `Reconciler`, `ConceptMatch`, and `ReconciliationResult`.
- **Sources:** [README: What it does](../../README.md#what-it-does),
  [README: Use cases](../../README.md#use-cases), and
  [migration schedule: folio-enrich](../migration/SCHEDULE.md).

## AA — Annotation-app developer

- **Stands for:** Developers building review and feedback applications on the annotation primitives
  extracted from `folio-enrich`.
- **Goal:** Let reviewers inspect, correct, reject, restore, and explain concept tags without
  reimplementing annotation state transitions.
- **Public entry points:** The public `folio_resolve.annotate` types and helpers, including
  `Annotation`, `ConceptTag`, `TagVerdict`, `FeedbackStore`, and rendering/insight helpers.
- **Sources:** [README: What it does](../../README.md#what-it-does),
  [README: Use cases](../../README.md#use-cases), and
  [migration schedule: folio-enrich](../migration/SCHEDULE.md).

## LJ — LLM-judge integrator

- **Stands for:** Developers who supply their own model transport behind the `Judge` protocol, as
  the migrated mapper and enrichment paths do.
- **Goal:** Add contextual judgment while keeping prompts, parsing, score bounds, and verdict policy
  deterministic and provider-neutral.
- **Public entry points:** `Judge`, `build_judge_prompt`, `parse_judge_json`,
  `strip_markdown_fences`, and `enforce_verdict`.
- **Sources:** [README: Bring Your Own Key](../../README.md#bring-your-own-key-byok),
  [README: Judge transport hardening](../../README.md#judge-transport-hardening-v021), and
  [migration schedule: folio-mapper finding](../migration/SCHEDULE.md).

## OM — Ontology and spec maintainer

- **Stands for:** The FOLIO ontology team and maintainers curating ontology adapters, behavior specs,
  label indexes, lemma caches, and calibration inputs.
- **Goal:** Keep ontology-backed matching explainable and consistent across in-memory, cached, and
  optional live-ontology execution.
- **Public entry points:** `Concept`, `OntologyProvider`, `InMemoryOntology`,
  `FolioPythonProvider`, `OntologySpec`, `decompose`, `augment_labels`, `LEMMA_VERSION`, and
  `ScoreCalibration`.
- **Sources:** [README: Personas](../../README.md#personas),
  [README: Lemma-key augmentation](../../README.md#lemma-key-augmentation-spacy-extra-v020), and
  [migration schedule: ontology-shaped code](../migration/SCHEDULE.md).

## EO — Public synthetic-eval operator

- **Stands for:** Operators of the repository's offline, public synthetic scoring and experiment
  lane.
- **Goal:** Exercise the consumer-visible matching path on versioned synthetic inputs with
  deterministic, leak-safe artifacts.
- **Public entry points:** The public library matching surface (`InMemoryOntology`,
  `MatchPipeline`, gates, blocklist, and scoring helpers) as driven by the public
  `folio_eval` synthetic corpus, scoring, leak-check, and experiment commands.
- **Sources:** [README: Development](../../README.md#development),
  [migration schedule: reproducible consumer baselines](../migration/SCHEDULE.md),
  [eval operator surface](../../eval/README.md), and
  [synthetic corpus contract](../../eval/synthetic/README.md).

## RM — Release maintainer

- **Stands for:** Maintainers who build and validate the core wheel and its optional extras before a
  consumer pin changes.
- **Goal:** Confirm that the package exposes the documented version and API, remains importable with
  only core dependencies, and returns byte-identical results across processes.
- **Public entry points:** `folio_resolve.__version__`, the names in `folio_resolve.__all__`, and the
  core and optional-extra install surfaces (`folio`, `spacy`, and `embedding`).
- **Sources:** [README: Install](../../README.md#install),
  [README: Determinism](../../README.md#determinism-is-a-guarantee-not-a-coincidence), and
  [migration schedule: release steps](../migration/SCHEDULE.md).
