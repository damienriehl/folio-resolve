# Consumer pipelines versus folio-resolve: evidence audit

Date: 2026-09-21. Scope: folio-enrich and folio-mapper; current source and deterministic migration replays, committed demo captures, and approved shared benchmark artifacts. This audit changes no production code, consumer environment, pin, or deployment. It does not rerun the pending U10 v2 comparison or access its private data.

## Decision

Owner direction recorded September 21: preserve the existing consumer pipelines until significant F1 improvement is demonstrated per consumer. See the [active adoption gate](../migration/SCHEDULE.md#active-adoption-gate--owner-direction-2026-09-21). Neither consumer has adopted the complete library pipeline, so no runtime rollback or selector was introduced.

Keep the existing component integrations and retain each consumer's pipeline. Evidence supports targeted fixes in enrich and output parity in mapper. It does **not** establish that substituting the library's complete pipeline improves either application. The historical whole-pipeline candidate lost against both incumbents; the updated adoption measurement remains unpublished/pending.

There are three different comparisons:

1. **Pre-library legacy versus integrated consumer:** does replacing duplicated scoring/resolution code help while retaining application orchestration?
2. **Integrated consumer versus library document adapter:** can the library replace the larger pipeline? The historical incumbents already included released folio-resolve 0.4.0, so this is not library versus no library.
3. **Current proposed policy experiments:** do selective semantic gating or lexical boundaries improve a small public query benchmark? These have not been adopted into the consumers.

Combining these comparisons would wrongly credit or blame the library for changes in extraction, candidate limits, embeddings, LLM judgment, or ontology versions.

## Shared end-to-end benchmark: historical replacement was worse

The [committed comparison](../../eval/reports/synthetic-comparison-v1.json) contains predictions and gold sets for the same 60 positive passages plus 30 no-match controls. Positives contain 99 item/concept gold relations covering 20 distinct concept IDs. Recomputed counts, micro metrics, and paired bootstrap intervals agree with the artifact.

| Historical deterministic system | TP | FP | FN | Precision | Recall | Micro-F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| folio-enrich incumbent | 9 | 629 | 90 | 1.41% | 9.09% | 2.44% |
| folio-mapper incumbent | 10 | 590 | 89 | 1.67% | 10.10% | 2.86% |
| folio-resolve document-adapter candidate | 1 | 359 | 98 | 0.28% | 1.01% | 0.44% |

Precision = TP/(TP+FP); recall = TP/(TP+FN). These rows score the **60 positives only**. Separately, every system returned at least one result for **30/30 no-match controls**, a 100% false-positive-item rate. Negative controls are not silently included in the table's precision denominator.

The recorded paired **mean per-item F1** delta is -2.033 percentage points versus enrich (95% CI -3.506 to -0.650) and -2.386 points versus mapper (-3.939 to -0.972). These are not confidence intervals on the difference between the table's micro-F1 values. Independent resampling reproduced the stored intervals using 2,000 draws, seed 20260727, and the repository's nearest-index percentile convention.

These low values are strict exact-concept-set scores on a difficult synthetic pilot. A related parent, child, or relationship does not count as the expected concept unless present in gold. They are not general production accuracy estimates, and the July demo numbers below use different units and cannot be compared directly.

**What the comparison isolates and what it does not:** all three use folio-python 0.3.6 and the same scored items. Enrich runs no LLM or embeddings; mapper runs its deterministic stage-1 path plus available embedding rerank (60% keyword/40% embedding), committing ten results; the candidate commits up to six with a 0.5 threshold. These are configured pipeline comparisons, not a scorer-only ablation or equal-output-budget ranking test. Candidate source is `db1cf4c`; enrich `bb576ac`; mapper `626412b`. All report package version 0.4.0; source SHA and configuration distinguish the candidate from the pinned incumbents.

Independent joins of the saved stage snapshots confirm the [stage attribution](../migration/2026-08-component-parity-map.md#u10-v8-runtime-attribution-2026-08-30): the candidate had retrieved all eight enrich-winning and nine mapper-winning relations, but placed them below its answer cutoff. Their ranks included 7, 9, 10, 24, and 36. The observed loss was principally ordering/cutoff behavior, not missing extraction alone.

**Freshness limit:** the [campaign report](../../eval/reports/campaign-report-v1.md) explicitly labels this comparison stale. Subsequent candidate-only full-corpus work improved TP from 6 to 15, recall from 1.65% to 4.13%, and micro-F1 from 0.70% to 1.75% on **225** positives. That is genuine within-cohort progress, but it cannot be compared directly against the 60-item incumbent table. Its no-match false-positive rate stayed 100%. The updated comparative adoption result is still pending; the existing leak-triage restriction remains untouched.

## folio-enrich: component integration and demos

Current consumer source: `f5364365346d93a3aa01fd5fecf219090afe5410`; installed release used: folio-resolve 0.4.0 and folio-python 0.3.6. A fresh offline paired replay exported legacy search/resolver modules from `2b35aac1a3c75e96c3baf53a925fca55f8c4cec2` and ran both arms under the same current ontology service/environment. Both exited 0 and exactly reproduced their historical capture structures. This isolates the resolution seam; it is not a reconstruction of every old application dependency.

| Migration measure | Pre-integration seam | Current integrated seam |
| --- | ---: | ---: |
| Queries returning a primary result | 24/24 | 24/24 |
| Candidates returned at limit five | 120 | 120 |
| Generic-place/agency canary violations | 3 | 0 |
| Primary concept IDs changed | — | 12/24 |
| Query/concept pairs retained | — | 68/120 |
| Comparator classifications | — | 5 intended fixes, 0 canary regressions, 7 neutral |

The replay removed 52 query/concept pairs and added 52. Constant candidate counts do not prove constant relevant recall. The comparator classifies changes using branch/query heuristics, not independently judged relevance; “0 regressions” is confined to those canaries. Resolution coverage of 24/24 is not 100% accuracy. Four entity-ruler documents and five reconciler cases match the old capture exactly.

The output changes expose blind spots in those canaries:

| Query | Legacy primary | Integrated primary | Comparator classification |
| --- | --- | --- | --- |
| Lawyer | Legal Aid Attorney | Lawyer | Intended fix |
| state | State Court | Estate (0.99 confidence) | Neutral |
| justice | U.S. Dept. of Justice | Obstruction of Justice | Intended fix |

The exact Lawyer recovery is a useful concrete improvement. The state result raises a clear semantic concern, while the justice substitution needs context; removing an agency match does not by itself establish correct meaning. These are observed outputs and qualitative risk findings, not newly owner-adjudicated gold labels. The canned “zero regressions” count therefore must not be presented as proof that the integrated pipeline never harms quality.

The library-only resolution ablation preserves all 24 primary IDs but returns only **15 candidates instead of 120**. That is an 87.5% loss of candidate alternatives, not a measured 87.5% loss of relevant recall. It supports retaining enrich's local candidate gathering.

Sources: consumer `backend/migration/README.md`, `DELTA-REPORT.md`, `corpus.json`, and `captures/{baseline,candidate,stage2-baseline}.json`; current `backend/app/services/folio/{search,resolver}.py`. The current scorer/resolver are shared, while candidate gathering, branch behavior, extraction, and application LLM stages remain consumer-owned.

### What the enrich demos actually show

There are 22 baked FOLIO demos; six have sampled gold: contract, lease, motion, NDA, opinion, and patent. Those six outputs contain 1,131 confirmed predictions. Their gold file contains 159 spans: **62 deterministic positives, 97 unjudged ambiguous entries, no human-verified entries, and no negatives**.

Re-scoring the baked outputs with the existing overlap metric gives TP 62, FP 0, FN 0, hence displayed precision/recall of 100%/100%. However, the scorer excludes predictions outside the sampled gold spans. It does not judge all 1,131 predictions or discover every missing relevant concept. Two scored opinion entries have text/offset mismatches; the text-valid subset recovers **60/60** targets. These numbers describe recovery on a selected positive subset, not whole-demo precision or comprehensive recall.

| Demo | Confirmed predictions | Scored positive targets recovered |
| --- | ---: | ---: |
| Contract | 88 | 8/8 |
| Lease | 317 | 7/7 |
| Motion | 106 | 13/13 |
| NDA | 370 | 13/13 |
| Opinion | 97 | 12/12 under overlap scoring; two offset mismatches |
| Patent | 153 | 9/9 |

The baked demo timestamps are May 25–26, before July library adoption. The separate full-mode NER report also scores 62/62 under both flag settings, but compares NER cross-validation OFF/ON, not folio-resolve versus legacy. Sources: consumer `frontend/demos/*.json`, `backend/eval/gold/folio_ner_gold.jsonl`, `backend/eval/metrics.py`, and `docs/evidence/ner-eval/full-mode-closure.md`.

**Enrich verdict:** measured help on a narrow false-place/agency canary, no observed regression in the named migration canaries, and no supported whole-demo precision/recall improvement claim. Keep the integration and its local fallback. No current paid-LLM full-demo replay was performed.

## folio-mapper: component integration and demos

Current consumer source: `af4a764922fe1f6fb05d47ae27b54d48faaee465`; pre-adoption scorer/service source: `64e36572bd0bd5233247ff51bc5963cbc1457f02`; adoption commit: `96aacf76077935da3ed19bc16cc7e09334a1c556` (July 24). Current source pins and installed package both identify released folio-resolve 0.4.0. Mapper originally donated much of the shared scorer; moving it into a library did not inherently add a new matching algorithm.

Independent comparison of the historical migration captures finds **all 70 seam records exactly equal**, with zero deltas or failed canaries: five tokenization cases, 22 search-term cases, 16 scoring pairs, 22 searches, two mandatory-branch cases, and three branch-scoped cases. The historical library arm used 0.2.0. Both arms disabled spaCy/embedding, used threshold 0.3 and branch cap eight, and saved ten candidates. This establishes behavioral parity on those cases, not precision or recall. Source: consumer `backend/migration/captures/{baseline,candidate}.json`.

### Fresh paired replay over all 188 demo inputs

Both legacy and current runs completed successfully using actual consumer candidate retrieval over the same local public ontology, threshold 0.3, branch cap eight, bridging enabled, and hash seed zero. SpaCy vectors and embedding retrieval were explicitly disabled in both arms; no paid LLM stage was run.

- **187/188 complete candidate lists are identical.**
- **All 188 top-five lists are unchanged.**
- **13,929 of 13,931 legacy candidate occurrences are retained (99.9856%).** This is candidate retention, not relevant recall.
- The only changed input is **Securities Litigation**. Two candidates leave: Securities Law Claims and Registered Securities. Two enter: Absence of Litigation and Litigation Risk. All four have score 47.3 in the Objectives branch.

The old search-term sequence considers securities before litigation; the deterministic library ordering reverses those two terms. Equal scores at the per-branch cap make insertion order affect membership. This is a concrete potential loss of useful securities alternatives, even though top-five output is preserved; the new alternatives have not been independently adjudicated, so a net quality gain or loss cannot be assigned. The historical migration seams also reproduce except for the known search-term ordering change.

**Environment limitation:** the consumer's local virtual environment initially failed label search because an installed alea dependency lacked `get_llm_kwargs`, causing folio-python to disable search support. The successful comparison used compatible existing dependency imports in an isolated process while retaining the consumer's released folio-resolve package. Both arms used this same normalized environment. This does not verify that the unmodified local installation or any deployed instance works; the dependency failure is not attributed to the scoring migration. Concurrent replay durations are not valid comparative latency measurements.

### What the mapper demos actually show

All ten demo snapshots predate library adoption: May 25, pipeline `0.10.0+020af7f`. They contain **188 input items, 3,475 saved candidate occurrences, 397 selected mapping occurrences, and 151 auto-completed items**. Zero items have an empty candidate list.

| Demo | Items | Auto-completed | Saved candidate occurrences | Selected occurrences |
| --- | ---: | ---: | ---: | ---: |
| Banking/finance | 18 | 17 | 342 | 46 |
| Commercial litigation | 19 | 16 | 358 | 54 |
| Corporate/M&A | 19 | 16 | 360 | 50 |
| Employment/labor | 19 | 14 | 342 | 43 |
| Family law | 19 | 16 | 362 | 30 |
| Immigration | 18 | 13 | 319 | 31 |
| IP/technology | 19 | 14 | 362 | 42 |
| Personal injury | 19 | 12 | 334 | 28 |
| Real estate | 19 | 17 | 357 | 50 |
| Solo/criminal | 19 | 16 | 339 | 23 |

These are saved Gemini-assisted predictions and UI selection states, not human-adjudicated reference answers. mapper's `scripts/curate_demos.py` selects mappings by score thresholds, sometimes taking the top result per branch. Scoring against those selected rows would measure agreement with the old generator, not correctness. Current demo loading restores saved state without new scoring; a library update cannot change an existing demo snapshot. Sources: consumer `apps/web/src/exemplar/demos/*.demo.json`, `docs/curating-demo-payloads.md`, and `scripts/curate_demos.py`.

The demo probe calls folio-python directly and bypasses the mapper pipeline. It cannot stand in for a legacy-versus-integrated full-pipeline evaluation. Whole-demo precision and recall are **not identifiable** from these captures.

**Mapper verdict:** near-neutral deterministic behavior on these demo inputs, with one concrete tie-cutoff candidate change that could harm recall; no demonstrated precision/recall gain. Keep its branch retrieval, embedding blend, mandatory fallback, and LLM stages. Replacing them is governed by the [active adoption gate](../migration/SCHEDULE.md#active-adoption-gate--owner-direction-2026-09-21): a significant paired per-consumer F1 improvement, with the incumbent kept as the default behind a selector and a quick switch back. Equal quality is not enough. Do not apply enrich's global place exclusion: mapper intentionally maps jurisdictions.

## Isolated ruler benchmark on enrich demo material

The historical [ruler shootout](../../bench/RESULTS.md), checked against [raw aggregate results](../../bench/summary.json), compares matching engines over the same lemma-augmented label index. Its workload includes 22 synthetic demo documents, roughly 157 KB.

| Engine-only measure | Enrich spaCy ruler | Library AC + same lemma index |
| --- | ---: | ---: |
| Preferred-label planted-target recall | 255/300 (85.0%) | 300/300 (100%) |
| Alternative-label planted-target recall | 187/200 (93.5%) | 200/200 (100%) |
| Punctuated-label planted-target recall | 110/150 (73.3%) | 150/150 (100%) |
| Homonym-trap false-positive matches | 13 across 15 traps | 13 across 15 traps |
| Demo matching throughput, historical run | 9,940 characters/s | 537,260 characters/s |

This supports a faster, more complete literal matching component with that index. It does not prove better document-level precision: extra matches and nested spans can be irrelevant. Dropping lemma augmentation loses all 200 planted lemma targets. The roughly 54-fold speed ratio belongs to matching on that historical machine/workload, not full application latency. No new timing claim is made here. The prose report's “800/800” is inconsistent with its five raw category sizes totaling 850; the table above uses the raw category counts.

## Recent query experiments do not settle consumer adoption

The [reviewed public query benchmark](embedding-precision.md) improved intended-target hit@5 from 8/12 to 9/12 with selective semantic gating. This is target-hit rate, not recall over every relevant concept. Strict P@5 bounds overlap (15.0–45.0% versus 16.7–53.3%); expanded relevance bounds also overlap (41.7–71.7% versus 48.3–85.0%). Twenty-six pooled pairs remain explicitly unjudged.

The [lexical-boundary experiment](embedding-lexical-boundaries.md) adds no target hits and loses the owner-reviewed Auctioneer relationship under original gating. Selective-gate top-five membership and order are identical across lexical policies. Neither experiment ran the consumers' complete pipelines or justifies a global production policy change.

## What would settle helping versus harming today

1. Freeze a representative sample of each consumer's demo inputs plus new held-out inputs. Judge relevant concept IDs independently of generated scores or selected UI rows, including omitted concepts and negative examples. Preserve direct/child/parent/relationship categories rather than silently calling all nonexact results irrelevant.
2. Run three explicit arms per consumer on the same ontology, inputs, model settings, thresholds, and output budgets: pre-integration implementation; today's integrated pipeline; proposed replacement. Keep a consumer-configured comparison and a separate equal-budget diagnostic.
3. Report strict and expanded precision/recall, no-match false-positive-item rate, per-item paired uncertainty, and stage of first loss. Measure cold/warm latency separately from quality. Cache the same model responses when isolating deterministic components.
4. Use existing finalized checkpoints where authorized. Do not repeat the expensive U10 v2 run; its separate pending publication decision remains a prerequisite to using that result.

Existing evidence is sufficient to **keep the current shared components, retain consumer-specific recall/LLM stages, and reject an unvalidated wholesale replacement**. It is insufficient to assign current whole-demo precision/recall or claim that the latest candidate outperforms either production pipeline.

## Audit receipts and reproduction limits

The paired consumer executions exited 0. Both read the same public OWL file, SHA-256 `44657b4ed844f5f9c9c48869184606b4fc671471a8263d79d241de87809fa239`, parsed by folio-python as 18,326 classes. This count is not interchangeable with the public embedding benchmark's 18,325 retained concepts. Socket access was blocked and writes were redirected away from consumer source and caches. Legacy modules were exported from the commits identified above; current modules used released 0.4.0. Full historical dependency stacks were not reconstructed.

Session receipts and isolated replay scripts are retained under `.codex-out/consumer-comparison/` (gitignored); the narrative report is under `docs/benchmarks/`. The shared pilot was independently rescored from its committed predictions, without loading private gold or rerunning inference; its source SHA-256 is `142fa9022e94a100b1dcf4555f7a8b413d66d136343c15345a6ea76d117f2c4f`. Paired bootstrap intervals and stage-loss attribution were independently recomputed. Demo counts and gold scoring were derived from each consumer's actual saved artifacts. No full LLM-on or deployed-service test, throughput rerun, or publication of the blocked comparison was performed.
