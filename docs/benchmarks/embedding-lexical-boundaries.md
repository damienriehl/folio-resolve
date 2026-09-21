# Lexical word-boundary experiment

Whole-word containment removes the three embedded Riga matches, but also loses an owner-approved relationship under the original gate. It adds no intended-target hits. **Do not enable this policy as a global production default on this evidence.** Under selective gating it changes scores and one winning path, but no top-five membership or order. That combined policy is not established as better than selective gating alone.

## Controlled comparison

The [collection](embedding-lexical-boundaries-collection.json) covers the same 16 approved public queries: 12 positives and four weak negative controls. All 16 complete original/selective control snapshots reproduced exactly before treatment retrieval. Both lexical arms retrieved over all 18,325 pinned concepts before applying limits. Each query used one real local-model semantic response, shared across arms; ranking used fresh candidate copies.

The treatment changes only four containment predicates: query in label, label in query, query in preferred label, and query in definition. A qualifying literal occurrence has no adjacent Unicode letter, number, combining mark, or underscore. Existing overlap, synonym, exact-match, specificity, and ranking behavior remain intact. Other evidence can therefore retain a concept after it loses a containment bonus.

The [results](embedding-lexical-boundaries-results.json) retain the approved strict and expanded relevance interpretations from [the owner review](embedding-precision-owner-review.md). Strict counts direct matches; expanded also counts children, parents, and relationships. Each positive query has five denominator slots. Unknown judgments contribute only to upper bounds. All full-positive macro point estimates remain withheld.

| Fixed gate | Lexical policy | Target hit@1 | Target hit@5 | Strict P@5 bounds | Expanded P@5 bounds | Returned / unknown |
| --- | --- | ---: | ---: | --- | --- | ---: |
| Original | Substring | 8/12 | 8/12 | 9/60–27/60 | 25/60–43/60 | 43 / 18 |
| Original | Boundary | 8/12 | 8/12 | 9/60–24/60 | 24/60–39/60 | 39 / 15 |
| Selective | Substring | 8/12 | 9/12 | 10/60–32/60 | 29/60–51/60 | 51 / 22 |
| Selective | Boundary | 8/12 | 9/12 | 10/60–32/60 | 29/60–51/60 | 51 / 22 |

The 8/12 to 9/12 gain belongs to the earlier selective-gate change, which recovers Negligence for the paraphrase query. Boundaries produce no additional target gain. Original/new, exact, paraphrase, and geographic group counts are preserved in the scored artifact. All four negative controls return zero concepts in every cell; this does not establish general precision.

The four-cell pool contains the same 55 pairs: 29 classified and 26 explicitly unjudged, with no newly introduced pairs. The owner categories remain 10 direct matches, seven children, one parent, and 11 relationships. No omissions were relabeled irrelevant. The lower upper bounds under the original gate reflect fewer unknown results; they are not a demonstrated precision improvement.

## What changed

- **Riga:** Under the original gate, Surigao del Norte, Surigao del Sur, and Water Supply and Irrigation Systems leave the top five. They previously scored 59.5 through label search with the pinned corpus metadata. All three remain unjudged. Riga and Riga Stock Exchange retain their semantic scores of 100.0 and 70.9. Under selective gating, Latvia, Lithuania, and Estonia already occupied those three slots; the boundary policy leaves that list unchanged.
- **Auction:** Auctioneer is an owner-approved relationship. Its original lexical score of 99.0 passes the short-label gate. With boundaries, its lexical score falls below the gate's near-exact threshold; both lexical and semantic paths are demoted to 40 under the original policy, below the 45 floor. It disappears. Under selective gating, its semantic path survives at 70.9, preserving its top-five position while changing the winning path from lexical to semantic.
- **Bid:** Antitrust - Bid-Rigging Claims decreases from 66.6 to 65.3 under both gates without changing membership or order.

There are 45 query/concept records with changed containment awards or scores, including changes below the result cutoff. Traces identify the field and qualifying occurrence; these are mechanism observations, not new relevance judgments. Across the measured top-five lists there are no new admissions, no lost direct matches, and one lost reviewed relationship under the original gate. Selective-gate top-five membership and order are identical across lexical policies.

## Implications for folio-enrich and folio-mapper

Read-only inspection on 2026-09-21 found both consumer source trees already pinning `folio-resolve==0.4.0` and importing its scorer. Folio-enrich was inspected at `f5364365346d93a3aa01fd5fecf219090afe5410`; folio-mapper at `af4a764922fe1f6fb05d47ae27b54d48faaee465`. These are source observations, not verification of deployed versions.

In folio-enrich, `backend/app/services/folio/search.py` imports `compute_relevance_score` while retaining multi-strategy retrieval, branch behavior, and a default threshold of 30. In folio-mapper, `backend/app/services/folio_service.py` delegates scoring with `use_vectors=True` and its spaCy similarity callback. The benchmark uses InMemoryOntology, no branch inference, no spaCy similarity, and a score floor of 45. The existing [component parity map](../migration/2026-08-component-parity-map.md) also documents consumer-owned stages beyond shared scoring.

The relevant next decision is adoption of a proposed scoring change or additional pipeline components. This experiment does not justify replacing those consumer pipelines or retiring their retrieval fallbacks. The known Auctioneer loss argues against a blanket boundary default. Selective gating remains a candidate for consumer-specific evaluation, supported here by one additional target hit but unresolved precision.

Before proposing a shared-library release or consumer pin update, replay the consumers' existing migration harnesses and candidate-recall canaries in isolated environments with their actual retrieval limits, gates, vector settings, and thresholds. Require explanations for lost reviewed relationships, retain the Riga examples as mechanism checks, and add representative consumer queries with explicit relevance judgments. That is follow-up validation, not work performed by this experiment. No production scoring, consumer pin, or deployment changed here.

## Reproduction and verification

The collector and offline scorer are in `benchmarks/embedding_lexical_boundaries.py`; their `collect` and `score` commands expose required paths. Collection requires the pinned OWL and local model snapshot with offline mode enabled. The artifacts record complete input/source hashes, the owner approval receipt, and runtime dependency versions. Model revision: `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; Python 3.12.12; sentence-transformers 3.4.1; transformers 4.57.6; torch 2.13.0+cpu; NumPy 2.5.3. Semantic top-k is five, lexical limit ten, and domains/headings/context are empty.

Offline scoring validates the historical evidence chain, new collection schema, saved lexical scores and traces, shared semantic inputs, real ranking replay, and judgment transfer. It does not re-encode the corpus or independently prove full-corpus completeness; that rests on the pinned collector's actual run. The local source and historical artifact hashes were checked unchanged.

Test-first evidence showed six baseline characterization tests passing, then an expected `55.2 != 0.0` failure for label-only Riga against Surigao del Norte before the boundary implementation. The combined focused suite passed 351 tests. The isolated core suite passed **1,626 tests, four skipped, two deselected**. Ruff passed and configured mypy passed for 26 source files. Independent arithmetic over raw candidates and owner categories matched all four cells, six groups, and both precision interpretations.

The committed-artifact test reproduces offline scoring without loading model assets. Its first Python 3.11 run exposed last-digit differences in aggregate floating-point bounds compared with Python 3.13. The test now checks those bounds against integer counts within 1e-15 and compares every other field exactly; the corrected full suite passes.

This small, purposively selected challenge set is not a blind holdout. Owner categories are not independently verified ontology edges, unknown relevance remains unresolved, and consumer end-to-end tests were not run. No significance or general-quality claim follows.
