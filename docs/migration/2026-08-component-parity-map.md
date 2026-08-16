# Downstream component parity map

Date: 2026-08-16  
Campaign: R18 / KD9

This map compares the matching-pipeline code currently present in `folio-enrich` and
`folio-mapper` with `src/folio_resolve`. A **ported** row delegates its policy to the library;
**divergent** means both sides have related behavior but the consumer still owns material policy;
**absent** means no library counterpart was found; and **library-only** means the library feature
has no downstream import. Citations name files and symbols inspected in the three clones.

## Parity table

| Consumer / pipeline stage | Downstream citation (opened) | `folio_resolve` citation (opened) | Status | Verification note |
|---|---|---|---|---|
| enrich: reconcile ruler and LLM matches | `folio-enrich/backend/app/services/reconciliation/reconciler.py` — `Reconciler._run` | `src/folio_resolve/reconciler.py` — `Reconciler` | ported | Thin model adapter constructs and calls `_LibReconciler`; merge policy is library-owned. |
| enrich: primary label resolution | `folio-enrich/backend/app/services/folio/resolver.py` — `ConceptResolver._library_resolve_all` | `src/folio_resolve/resolve.py` — `LabelResolver.resolve` | ported | Primary path instantiates the pinned library resolver and adapts results. |
| enrich: relevance scoring and search-term generation | `folio-enrich/backend/app/services/folio/search.py` — `multi_strategy_search` | `src/folio_resolve/scoring.py` — `compute_relevance_score`, `generate_search_terms` | ported | Consumer orchestration imports the shared scorer, tokenization, stopwords, and expansions. |
| enrich: place/short-label precision gates | `folio-enrich/backend/app/services/folio/search.py` — `candidate_vetoed` | `src/folio_resolve/gates.py` — `PlaceNameGate`, `ShortLabelGate` | ported | Enrich directly uses `PlaceNameGate`; `ShortLabelGate` is library policy but is not separately invoked on this fallback path. |
| enrich: alias/homonym blocklist | `folio-enrich/backend/app/services/folio/search.py` — `_blocklist`, `candidate_vetoed` | `src/folio_resolve/blocklist.py` — `AliasBlocklist`, `load_seed_blocklist` | ported | Consumer loads and applies the packaged library blocklist. |
| enrich: entity-ruler extraction | `folio-enrich/backend/app/services/entity_ruler/ruler.py` — `FOLIOEntityRuler` | `src/folio_resolve/entity_ruler.py` — `FOLIOEntityRuler` | divergent | Parallel implementations remain; enrich does not import the library ruler. |
| enrich: Aho–Corasick phrase matching | `folio-enrich/backend/app/services/matching/aho_corasick.py` — `AhoCorasickMatcher`; `folio-enrich/backend/tests/test_aho_corasick.py` — `test_matches_folio_resolve_semantics` | `src/folio_resolve/matching/aho_corasick.py` — `AhoCorasickMatcher` | divergent | Local copy is explicitly parity-tested against the library, but is still separately maintained. |
| enrich: branch/context LLM judgment | `folio-enrich/backend/app/services/concept/branch_judge.py` — `BranchJudge` | `src/folio_resolve/judge.py` — `build_judge_prompt`, `parse_judge_json` | divergent | Both judge candidates, but enrich owns branch-context prompt and response behavior rather than using library judge functions. |
| enrich: seven-strategy recall fallback | `folio-enrich/backend/app/services/folio/search.py` — `multi_strategy_search`; `folio-enrich/backend/app/services/folio/resolver.py` — `ConceptResolver._search_candidates` | `src/folio_resolve/recall.py` — `MultiStrategyRecall` | divergent | Enrich retains its legacy orchestration as fallback when `LabelResolver` yields nothing; it does not import `MultiStrategyRecall`. |
| enrich: semantic/embedding retrieval | `folio-enrich/backend/app/services/embedding/service.py` — `EmbeddingService`, `build_embedding_index` | `src/folio_resolve/embedding.py` — `BruteForceIndex`, `LocalEmbeddingProvider` | divergent | Similar index/provider responsibilities, but consumer implementation and lifecycle remain local. |
| enrich: individual extraction | `folio-enrich/backend/app/services/individual/entity_extractors.py` — `EntityExtractorRunner` | absent | absent | No individual-extraction model or runner exists under `src/folio_resolve`. |
| enrich: property matching | `folio-enrich/backend/app/services/property/property_matcher.py` — `PropertyMatcher` | absent | absent | No property-matching counterpart exists under `src/folio_resolve`. |
| enrich: subject–predicate–object triples | `folio-enrich/backend/app/services/dependency/parser.py` — `DependencyParser.extract_triples_and_pos` | absent | absent | No dependency/triple extraction counterpart exists under `src/folio_resolve`. |
| mapper: FOLIO relevance scoring | `folio-mapper/backend/app/services/folio_service.py` — `_compute_relevance_score`, `_generate_search_terms` | `src/folio_resolve/scoring.py` — `compute_relevance_score`, `generate_search_terms` | ported | Thin bindings call library policy; mapper adds only its optional spaCy similarity/expansion seam. |
| mapper Stage 3: judge JSON parse and verdict enforcement | `folio-mapper/backend/app/services/pipeline/stage3_judge.py` — `_parse_judge_json` | `src/folio_resolve/judge.py` — `parse_judge_json`, `enforce_verdict` | ported | Adapter delegates defensive parsing, clamping, and verdict consistency to the library. |
| mapper Stage 3: prompt and score calibration | `folio-mapper/backend/app/services/pipeline/stage3_judge.py` — `run_stage3`; `folio-mapper/backend/app/services/pipeline/prompts.py` — `build_judge_prompt` | `src/folio_resolve/judge.py` — `build_judge_prompt`, `SCORE_CALIBRATION` | divergent | Partial parity: parser/policy constants are shared, while mapper retains its pipeline prompt construction, LLM call, fallback, and missing-row handling. |
| mapper Stage 1: branch-scoped keyword recall | `folio-mapper/backend/app/services/pipeline/stage1_filter.py` — `_search_within_branch`, `run_stage1` | `src/folio_resolve/recall.py` — `MultiStrategyRecall` | divergent | Mapper owns branch filtering, candidate caps, expansion rescoring, and pipeline models; it does not call library recall. |
| mapper Stage 1b: LLM label expansion | `folio-mapper/backend/app/services/pipeline/stage1b_expand.py` — `run_stage1b` | absent | absent | No LLM suggestion/underrepresented-branch expansion stage exists in the library. |
| mapper: mandatory-branch fallback | `folio-mapper/backend/app/services/pipeline/mandatory_fallback.py` — `run_mandatory_fallback` | absent | absent | No mandatory-branch recovery stage exists in the library. |
| mapper: embedding candidate recall | `folio-mapper/backend/app/services/pipeline/stage1_filter.py` — `_add_embedding_candidates`; `folio-mapper/backend/app/services/embedding/service.py` — `build_embedding_index` | `src/folio_resolve/embedding.py` — `BruteForceIndex.query` | divergent | Related semantic retrieval exists, but mapper owns index lifecycle, branch filtering, thresholds, and score scaling. |
| mapper: embedding rerank | `folio-mapper/backend/app/services/pipeline/orchestrator.py` — `_embedding_rerank` | `src/folio_resolve/embedding.py` — `BruteForceIndex.score_candidates` | divergent | Library supplies a comparable score primitive; mapper owns the 60/40 blend and mandatory-branch retention. |
| library: conjunction/shared-head decomposition | absent (no import in either consumer) | `src/folio_resolve/decompose.py` — `decompose` | library-only | Consumer-wide Python import search found no `folio_resolve.decompose` or imported `decompose`. |
| library: domain priors | absent (no import in either consumer) | `src/folio_resolve/domain_prior.py` — `DomainPrior`, `DomainPriorSuggester` | library-only | No downstream import found. |
| library: source classification | absent (no import in either consumer) | `src/folio_resolve/sources.py` — `SourceClassifier`, `classify_source` | library-only | No downstream import found. |
| library: fitted score calibration | absent (no import in either consumer) | `src/folio_resolve/calibration.py` — `ScoreCalibration` | library-only | Distinct from mapper's use of `judge.SCORE_CALIBRATION`; neither consumer imports `folio_resolve.calibration`. |
| library: annotation lifecycle/models/rendering | absent (no import in either consumer) | `src/folio_resolve/annotate/lifecycle.py` — `reject`, `restore`, `promote`; `src/folio_resolve/annotate/models.py` — `Annotation`; `src/folio_resolve/annotate/render.py` — `render_segments` | library-only | No downstream `folio_resolve.annotate` import found. |
| library: composed match pipeline | absent (no import in either consumer) | `src/folio_resolve/pipeline.py` — `MatchPipeline` | library-only | Consumers compose their own pipelines and do not import the library pipeline. |
| library: composed multi-strategy recall | absent (no import in either consumer) | `src/folio_resolve/recall.py` — `MultiStrategyRecall` | library-only | Both consumers retain recall orchestration and do not import the library class. |

## Gap attribution procedure

Treat comparison deltas as leads, not causal proof. `eval/folio_eval/comparison.py` writes one
canonical row snapshot per stack and lane through `write_stage_snapshots`; each `stages.json` is
keyed by item ID and contains that stack's stage payload. For every item whose candidate loses a
previously correct IRI (or adds a false-positive IRI), diff the candidate and incumbent snapshots
stage by stage, in pipeline order. Record the first stage at which the expected IRI disappears,
its score/rank and relevant gate or verdict before and after that boundary, and every later stage
that could independently suppress or recover it. Use stable item IDs and IRI hashes—not display
order—to join rows. Also check the comparison's per-item score/verdict so an apparent stage gap is
not merely an output-adapter or scoring difference.

A first-divergence association is only a hypothesis. Before promoting a divergent/absent row to a
port candidate, replay the lost item through the same pinned inputs and configuration with a
single-component ablation: disable or substitute only the suspected component, holding extraction,
ontology/version, candidate limits, prompts/model settings, thresholds, and randomness constant.
Then replay a representative retained-item/control set to detect precision or recall regressions.
Promotion requires the loss to disappear under the ablation (or reappear when the component is
restored), repeatably, with snapshots showing the expected causal boundary. If isolation is
impossible, use the smallest factorial replay that separates interacting components and document
the interaction. Correlation between a missing item and a stage difference is not causation.

## Verification boundary

The comparison implementation inspected was `eval/folio_eval/comparison.py`, specifically
`write_stage_snapshots`, `run_synthetic_comparison`, `score_stack`, and `build_comparison`. No
`eval/data/**` path and no `eval/reports/experiments.jsonl` was read. “No import” claims were checked
with a Python-source search across both consumer clones for the named `folio_resolve` modules and
symbols.
