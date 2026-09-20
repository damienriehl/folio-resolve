# Embedding retrieval and gate evidence

The pinned local model retrieves Negligence in its first five neighbors, but the short-label gate removes it. The other three approved paraphrase targets fall outside that window and have scores below the existing floor. Increasing the window alone would recover none of those three.

These observations explain the [unchanged public baseline](embedding-baseline.md). They do not establish that relaxing a gate is safe or that a different model would perform better.

## Target traces

Measured on 2026-09-20 with the original eight frozen cases and 18,325 concepts. Semantic ranks cover the complete corpus; scores below are cosine multiplied by 100 and rounded as the pipeline does. The pipeline retrieves only five semantic neighbors and applies a score floor of 45.

| Case | Approved target | Local semantic rank | Semantic score | Observed pipeline outcome |
|---|---|---:|---:|---|
| E1 | Summary Judgment | 1 | 83.0 | Exact lexical candidate survives at 99; semantic duplicate discarded |
| E2 | Auction | 1 | 76.4 | Exact lexical candidate survives at 99; semantic duplicate discarded |
| P1 | Negligence | 5 | 54.6 | Semantic candidate demoted to 40 by ShortLabelGate, then removed below floor |
| P3 | Statute of Limitations | 14 | 38.0 | No target retrieved; full-corpus semantic score is also below floor |
| P4 | Res Judicata | 424 | 30.3 | Semantic target outside window; lexical target retrieved at 27.5 and removed below floor |
| P6 | Consideration | 1,654 | 18.5 | No target retrieved; full-corpus semantic score is also below floor |

This attributes the current P1 miss to a gate. P3 and P6 are retrieval misses at the configured window, with an additional score-floor obstacle visible in the untimed full-corpus ranking. P4 has a weak lexical route as well as a distant semantic neighbor. The trace does not pretend those unretrieved semantic candidates were actually evaluated by a gate.

With embeddings disabled, only P4 enters lexical retrieval among the paraphrase targets, at 27.5. Hashing ranks P1, P3, P4, and P6 at 2,456, 1,663, 5,030, and 10,995 respectively. Hashing supplies no nearby paraphrase targets in this sample.

All final candidates, including scores, paths, gate fields, and ordering, exactly match each variant's original baseline. Final recall@5 remains 2/6 for all variants. Both negative controls still return no final candidates.

## Runtime attribution

Each cell shows pooled p50 / p95 seconds over 24 actual matches: three repetitions of each of eight queries. Components are measured inside the same match, with one uninstrumented warmup per query. Full-corpus rank queries and trace serialization happen after all timed matches.

| Variant | Whole match | Lexical search | Semantic query, including encoding | Query encoding, nested within semantic |
|---|---:|---:|---:|---:|
| disabled | 1.143 / 1.433 | 1.143 / 1.433 | 0 / 0 | 0 / 0 |
| hashing | 1.415 / 1.640 | 1.097 / 1.337 | 0.312 / 0.320 | 0.000030 / 0.000038 |
| local | 1.727 / 2.598 | 1.246 / 1.933 | 0.505 / 0.657 | 0.007775 / 0.010753 |

For the local model, summed paired durations attribute **70.29%** to lexical search, **29.70%** to semantic query, and **0.01%** to remaining pipeline work and observer overhead. Query encoding is **0.46% of total time**, included in the semantic share. Semantic work excluding encoding is 29.24%; this includes exhaustive cosine scoring, vector validation, sorting, and observer overhead, not cosine arithmetic alone. Do not add nested encoding time to semantic time or add independently computed percentiles.

The local pipeline build took 227.195 seconds, including model-file verification, model loading, and index construction. This diagnostic build interval excludes corpus parsing and therefore differs from the original baseline's build interval. No new peak-memory measurement was taken.

The lexical measurement is the full scan in `InMemoryOntology`, including decomposition searches when present. It is not a measurement of the bounded, cached `FolioPythonProvider` adapter. Runs were sequential fresh processes on the same host with cached inputs and one-thread limits; they were not randomized repeated experiments on an isolated machine. Absolute latency varies across runs. These samples support attribution within this workload, not a production latency target or a claimed performance regression.

## Implications for the next experiment

A controlled short-label-gate ablation is the smallest next experiment with a demonstrated recoverable target. Keep the model, top-five window, score floor, corpus, and answer sets fixed. Compare the baseline against bypassing only that gate in benchmark code, and report every newly admitted candidate by extraction path. Such an ablation measures the gate's effect; it is not a proposed shipping policy.

The current negative controls cannot establish the safety of that change: their highest local semantic scores are only 38.0 and 38.7, already below the floor. Existing short-label and place-name regression scenarios must remain explicit guard checks. Even a recall gain with unchanged negative controls would justify further policy evaluation, not automatic adoption.

Wider retrieval alone cannot recover P3/P4/P6 at their observed scores. Query-embedding caching targets less than 1% of this warm workload. FAISS could address part of the remaining semantic-query cost, but not the lexical majority or these relevance failures. Leave model replacement, score-floor tuning, FAISS, and caching for separately justified work.

Rank cutoffs and final pipeline recall answer different questions; the [Sentence Transformers retrieval evaluator](https://sbert.net/docs/package_reference/sentence_transformer/evaluation.html#sentence_transformers.evaluation.InformationRetrievalEvaluator) likewise evaluates retrieval at explicit cutoffs. Here the existing library and frozen annotations remain the measurement authority; no new evaluation dependency was introduced.

## Artifacts and reproduction

- [Diagnostic runner](../../benchmarks/embedding_diagnostics.py) and [real-pipeline tests](../../tests/test_embedding_diagnostics.py).
- Raw traces and timings: [disabled](embedding-diagnostics-disabled.json), [hashing](embedding-diagnostics-hashing.json), [local](embedding-diagnostics-local.json).
- Inputs and acquisition instructions: [baseline protocol](embedding-baseline.md#reproduce).

Reuse the pinned public ontology and offline model environment from the baseline. Run each variant separately; add the pinned `--model-path` only for `local`:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  /tmp/folio-embedding-venv/bin/python -m benchmarks.embedding_diagnostics \
  --owl /tmp/folio-public-benchmark.owl --variant local \
  --model-path /tmp/folio-model-cache/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41 \
  --output /tmp/embedding-diagnostics-local.json --repeats 3
```

The runner rejects drift in fixture, normalized corpus, and library-source hashes relative to the frozen baseline. It verifies the imported library comes from this checkout and verifies exact pinned model bytes before construction. Instrumented results must equal both an uninstrumented match and the frozen final candidates. Raw artifacts retain source, baseline-file, fixture, corpus, model, package, thread, and Git identities; source hashes identify the measured uncommitted diagnostic code more precisely than the base commit alone.

The original fixture hash remains `70603b759e56ad252129fc5c4de95c6418f436e06598e773e0ab79c71a759c72`; corpus hash remains `a829784b946f3c7e87d55c4734d895a1ece589f78fb8618a2c05008c67c29c73`. The runner reads no private gold or campaign data. Tracing uses private library methods, so a future library change requires a new baseline rather than silently reusing these diagnostics.
