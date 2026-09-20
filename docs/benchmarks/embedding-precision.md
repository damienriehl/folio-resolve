# Public embedding precision evidence

Owner review distinguishes direct matches, children, parents, and related concepts. Of 55 pooled query/concept pairs, 29 received classifications and 26 explicitly remain unjudged as too ambiguous. Both strict and expanded precision are reported below; neither full-positive macro has a supported point estimate.

The selective gate retains the observed intended-target gain: hit@5 is 9/12 versus 8/12 under the original gate. This bounded evidence does not establish general precision or justify a production policy change.

## Review and metric definitions

The [owner review](embedding-precision-owner-review.md) preserves the actual decisions, numbering, exact query/IRI identities, and subsequent approval of two scoring interpretations. The [judgment sheet](embedding-precision-judgments.json) binds those decisions to the frozen collection and categorical rubric. Categories are owner annotations, not verified ontology edges.

- Strict P@5 counts direct matches only. Children, parents, and related concepts do not enter its numerator; this does not label them generally irrelevant.
- Expanded P@5 counts direct matches, children, parents, and relationships equally. It measures the owner-approved broader notion of relevance, not only exact identity.
- Both use five positions per positive query, including empty positions as zero. For R qualifying returned concepts and U unjudged returned concepts, bounds are R/5 to (R+U)/5.
- A paired query point estimate requires both policies to be fully judged. Macros retain all positives in their stated group; they never average only completed queries.
- The 26 omitted pairs stay unjudged (`null`). No irrelevant labels were supplied. Their ambiguity has been explicitly reviewed; these are not pending owner answers.

Candidate 17, Motion to File Claim After Claims Bar Date for “expired deadline bars suing,” is preserved as an owner-labeled direct match. The original intended target remains Statute of Limitations. Thus that query has a known direct relevance match but still misses its intended target.

## Collection

The pinned local model ran once per query over the same 18,325-concept corpus. Each original candidate stream was copied and ranked through the real original and exact-semantic-path bypass policies. All eight earlier complete control snapshots reproduced exactly. The collection contains all input, mutated, and final candidates; relevance pooling is only the union of each policy’s top five, deduplicated by query/IRI.

Model: `sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, dimension 384. Ontology revision: `e36f3f8e7e7ecc8e081b78ababc3daca149fcaf2`. Semantic top-k 5, score floor 45, label-search limit 10, no branch inference, empty domains/headings, no context text. Complete hashes are in the [collection](embedding-precision-collection.json).

The categorical scorer is separate from the frozen collector: owner review changed the scoring rubric after collection. Rescoring validates the original collection and replays its saved ranking inputs offline; it does not load a model or retrieve candidates. The original fixture and collector remain unchanged.

## Coverage

| Scope | Direct | Child | Parent | Relationship | Unjudged | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Unique pooled pairs | 10 | 7 | 1 | 11 | 26 | 55 |
| Original top-five occurrences | 9 | 7 | 0 | 9 | 18 | 43 |
| Selective top-five occurrences | 10 | 7 | 1 | 11 | 22 | 51 |

Coverage is 29/55 unique pairs (52.7%); per-policy coverage is 25/43 (58.1%) original and 29/51 (56.9%) selective. All four negative controls return zero concepts under both policies; they receive no precision score. Zero admissions on these weak nonsense negatives do not establish real-world precision.

## Intended-target hits

| Positive group | Queries | Original hit@1 | Selective hit@1 | Original hit@5 | Selective hit@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| combined | 12 | 8/12 | 8/12 | 8/12 | 9/12 |
| original | 6 | 2/6 | 2/6 | 2/6 | 3/6 |
| new | 6 | 6/6 | 6/6 | 6/6 | 6/6 |
| exact | 6 | 6/6 | 6/6 | 6/6 | 6/6 |
| paraphrase | 4 | 0/4 | 0/4 | 0/4 | 1/4 |
| geographic | 2 | 2/2 | 2/2 | 2/2 | 2/2 |

## Precision bounds

All macro entries below are bounds, not point estimates. Group denominators are five times the query count; all six group point comparisons remain withheld.

| Group | Slots | Original strict | Selective strict | Original expanded | Selective expanded |
| --- | ---: | --- | --- | --- | --- |
| combined | 60 | 9/60–27/60 | 10/60–32/60 | 25/60–43/60 | 29/60–51/60 |
| original | 30 | 3/30–15/30 | 4/30–16/30 | 7/30–19/30 | 10/30–22/30 |
| new | 30 | 6/30–12/30 | 6/30–16/30 | 18/30–24/30 | 19/30–29/30 |
| exact | 30 | 6/30–12/30 | 6/30–11/30 | 21/30–27/30 | 24/30–29/30 |
| paraphrase | 20 | 1/20–10/20 | 2/20–12/20 | 1/20–10/20 | 2/20–12/20 |
| geographic | 10 | 2/10–5/10 | 2/10–9/10 | 3/10–6/10 | 3/10–10/10 |

For the combined group, strict bounds are 15.0–45.0% original and 16.7–53.3% selective; expanded bounds are 41.7–71.7% original and 48.3–85.0% selective. Overlapping bounds do not settle which policy has better precision.

Per-query entries are points only when both policies are fully judged; otherwise they are bounds.

| Query | Original strict | Selective strict | Original expanded | Selective expanded | Returned original/selective |
| --- | --- | --- | --- | --- | --- |
| E1 | 0.2–0.6 | 0.2–0.4 | 0.6–1.0 | 0.8–1.0 | 5/5 |
| E2 | 0.2–0.4 | 0.2–0.4 | 0.6–0.8 | 0.8–1.0 | 4/5 |
| P1 | 0.0–0.8 | 0.2–1.0 | 0.0–0.8 | 0.2–1.0 | 4/5 |
| P3 | 0.2–0.4 | 0.2–0.4 | 0.2–0.4 | 0.2–0.4 | 2/2 |
| P4 | 0.0–0.8 | 0.0–1.0 | 0.0–0.8 | 0.0–1.0 | 4/5 |
| P6 | 0.0 | 0.0 | 0.0 | 0.0 | 0/0 |
| S1 | 0.2 | 0.2 | 0.6 | 0.8 | 3/4 |
| S2 | 0.2–0.8 | 0.2–0.8 | 0.4–1.0 | 0.4–1.0 | 5/5 |
| S3 | 0.2 | 0.2 | 1.0 | 1.0 | 5/5 |
| S4 | 0.2 | 0.2 | 1.0 | 1.0 | 5/5 |
| G1 | 0.2–0.2 | 0.2–1.0 | 0.2–0.2 | 0.2–1.0 | 1/5 |
| G2 | 0.2–0.8 | 0.2–0.8 | 0.4–1.0 | 0.4–1.0 | 5/5 |

## Observed changes and limits

Selective gating adds an owner-labeled direct match for P1 (Negligence), a parent for E1 (Judgment), and relationships for E2 (Bid) and S1 (Auction). The added Ratification for P4 and additional geographic candidates remain unjudged. No general irrelevant-admission count can be inferred from these omissions. The complete scored artifact exposes each added/removed IRI and winning-path change.

E1 Judgment is absent under the original policy and survives the selective policy through the semantic path. In the earlier full-bypass experiment it instead won through a lexical path; that historical contrast does not change its current owner classification as a parent. Seller for Auction remains below rank five and outside this judgment pool; the direct Seller query does not confer a judgment on Seller for Auction.

This is a small, purposively selected challenge set motivated by previous results, not a blind holdout. Exact and geographic additions must not obscure the unchanged paraphrase target misses for P3, P4, and P6. The four weak negative controls, incomplete coverage, and owner-specific expanded rubric limit interpretation. No significance, general-quality, tuning, or production-adoption claim follows. Production code, thresholds, model, corpus, previous fixtures and evidence, private eval, and U10 remain unchanged.

## Reproduction and verification

Run the categorical scorer with the approval receipt digest recorded below; see its `--help` for arguments. It consumes the frozen collection and reviewed judgment sheet and produces [embedding-precision-results.json](embedding-precision-results.json). 

```bash
.venv/bin/python -m benchmarks.embedding_precision_relations \
  --collection docs/benchmarks/embedding-precision-collection.json \
  --judgments docs/benchmarks/embedding-precision-judgments.json \
  --approval-sha256 8141ed7277b9b98a3f9d82f5625378c78b0b69fc4c8c55ab0985f4765623330d \
  --output docs/benchmarks/embedding-precision-results.json
```

The command exited 0 and reported 55 pooled pairs, 29 annotated, and 26 missing. The approval digest is a consistency check against the recorded owner decision, not an authentication or signature system. Inherited strict-summary `irrelevant` fields denote exclusions from that metric, including children, parents, and relationships; actual owner category counts remain separate and include zero explicit irrelevant labels.

Independent arithmetic checked every query under both policies and both metrics, all six fixed-denominator groups, target hits, source hashes, and the exact unchanged collection. Focused embedding verification passed 178 tests. Ruff passed; configured mypy passed for 26 source files. The categorical tests were observed failing before implementation (8 failures because the new module did not exist) and then passing (35 tests, including the 22 original precision tests).

The final isolated core suite passed: **1,445 passed, 4 skipped, 2 deselected** in 50.97 seconds.
