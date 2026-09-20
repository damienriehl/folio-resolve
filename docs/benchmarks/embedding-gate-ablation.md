# Short-label gate ablation

Bypassing the short-label gate recovers **Negligence at final rank 5** with the pinned local model. Positive hit@5 rises from **2/6 to 3/6**, and both exact targets remain first. This satisfies the plan’s rule for further investigation of a selective policy. It does **not** support adopting the broad bypass: the experiment also admits other candidates, removes the named lexical fuzzy-label protection, and admits five N1 candidates under hashing.

Disabled and hashing variants recover no paraphrase, so they do not meet the investigation rule. Retain production defaults. Any later policy proposal needs evidence beyond these eight cases; no model, threshold, answer set, or other lever was tuned.

## Paired results

| Variant | Hit@1, baseline → bypass | Hit@5, baseline → bypass | N1 count | N2 count | Recovered paraphrases | Further investigation |
|---|---|---|---|---|---|---|
| disabled | 2/6 → 2/6 | 2/6 → 2/6 | 0 → 0 | 0 → 0 | None | No |
| hashing (diagnostic provider) | 1/6 → 1/6 | 2/6 → 2/6 | 0 → 5 | 0 → 0 | None | No |
| local | 2/6 → 2/6 | 2/6 → 3/6 | 0 → 0 | 0 → 0 | P1 | Yes, subject to the reported losses |

Local hit@1 remains 33.3%; hit@5 changes from 33.3% to 50.0%. Among the four paraphrases alone, local top-five hits change from 0/4 to 1/4. No exact target leaves the top five in any variant. The local negative controls remain empty because their best semantic scores were already below 45; that does not establish safety.

Final candidate counts across all eight cases:

| Case | Query | Disabled | Hashing | Local |
|---|---|---:|---:|---:|
| E1 | Summary Judgment | 9 → 10 | 9 → 15 | 9 → 10 |
| E2 | Auction | 3 → 3 | 7 → 7 | 4 → 6 |
| P1 | careless conduct injures someone | 0 → 0 | 1 → 5 | 4 → 5 |
| P3 | expired deadline bars suing | 0 → 0 | 0 → 5 | 2 → 2 |
| P4 | previously resolved dispute barred anew | 0 → 0 | 4 → 4 | 4 → 5 |
| P6 | bargained reciprocal benefit | 0 → 0 | 1 → 5 | 0 → 0 |
| N1 | purple nebula hums a lullaby | 0 → 0 | 0 → 5 | 0 → 0 |
| N2 | my sourdough starter smells fruity | 0 → 0 | 0 → 0 | 0 → 0 |

## Approved target ranks

Ranks below are **final pipeline ranks**, baseline → bypass; “absent” means no final target candidate. Semantic top-k remains five even when the final merged list contains more candidates.

| Case | Approved target | Disabled | Hashing | Local |
|---|---|---|---|---|
| E1 | Summary Judgment | 1 → 1 | 1 → 1 | 1 → 1 |
| E2 | Auction | 1 → 1 | 3 → 3 | 1 → 1 |
| P1 | Negligence | absent → absent | absent → absent | absent → 5 |
| P3 | Statute of Limitations | absent → absent | absent → absent | absent → absent |
| P4 | Res Judicata | absent → absent | absent → absent | absent → absent |
| P6 | Consideration | absent → absent | absent → absent | absent → absent |

The exact targets keep their lexical score of 99 in every arm. Local Negligence enters semantic retrieval at rank 5 and score 54.6; baseline demotion to 40 removes it, while bypass preserves 54.6 and final rank 5. Local full-corpus semantic ranks for Statute of Limitations, Res Judicata, and Consideration remain 14, 424, and 1,654, with scores 38.0, 30.3, and 18.5. They remain absent from the final results. Res Judicata’s lexical score of 27.5 also stays below the floor. Full target retrieval and gate traces are retained in each raw artifact.

## Every candidate delta

These tables enumerate all final candidate additions and rank or score changes, keyed by IRI and extraction path in the raw artifacts. No existing final candidate is removed or changes score. “—” means absent from the final list; added candidates have no baseline final score. Gate-reason wording changes for retained candidates are visible in the complete snapshots but are not relevance deltas. `label_search` is the lexical path; `semantic` is the index path.

### disabled

| Case | Candidate | Path | Final rank, baseline → bypass | Final score, baseline → bypass |
|---|---|---|---|---|
| E1 | Judgment | `label_search` | — → 2 | — → 91.3 |
| E1 | Motion for Summary Judgment | `label_search` | 2 → 3 | 86.0 → 86.0 |
| E1 | Summary Judgment in Lieu of Complaint | `label_search` | 3 → 4 | 79.4 → 79.4 |
| E1 | JJ10 Applications relating to originating process or Statement of Case or for default or summary Judgment (UK J-CODE) | `label_search` | 4 → 5 | 61.3 → 61.3 |
| E1 | Dispositive Motions | `label_search` | 5 → 6 | 60.0 → 60.0 |
| E1 | Abstract of Judgment | `label_search` | 6 → 7 | 50.6 → 50.6 |
| E1 | Judgment Lien | `label_search` | 7 → 8 | 47.3 → 47.3 |
| E1 | Judgment of Arbitration | `label_search` | 8 → 9 | 47.3 → 47.3 |
| E1 | Administrative Judgment | `label_search` | 9 → 10 | 47.3 → 47.3 |

### hashing

| Case | Candidate | Path | Final rank, baseline → bypass | Final score, baseline → bypass |
|---|---|---|---|---|
| E1 | Judgment | `label_search` | — → 2 | — → 91.3 |
| E1 | Motion for Summary Judgment | `label_search` | 2 → 3 | 86.0 → 86.0 |
| E1 | Summary Judgment in Lieu of Complaint | `label_search` | 3 → 4 | 79.4 → 79.4 |
| E1 | Yalova | `semantic` | — → 5 | — → 75.0 |
| E1 | Florida | `semantic` | — → 6 | — → 70.7 |
| E1 | Rivera | `semantic` | — → 7 | — → 70.7 |
| E1 | Jabat | `semantic` | — → 8 | — → 70.7 |
| E1 | Cyprus | `semantic` | — → 9 | — → 70.7 |
| E1 | JJ10 Applications relating to originating process or Statement of Case or for default or summary Judgment (UK J-CODE) | `label_search` | 4 → 10 | 61.3 → 61.3 |
| E1 | Dispositive Motions | `label_search` | 5 → 11 | 60.0 → 60.0 |
| E1 | Abstract of Judgment | `label_search` | 6 → 12 | 50.6 → 50.6 |
| E1 | Judgment Lien | `label_search` | 7 → 13 | 47.3 → 47.3 |
| E1 | Judgment of Arbitration | `label_search` | 8 → 14 | 47.3 → 47.3 |
| E1 | Administrative Judgment | `label_search` | 9 → 15 | 47.3 → 47.3 |
| P1 | Bosilovo | `semantic` | — → 2 | — → 50.0 |
| P1 | Nunavut | `semantic` | — → 3 | — → 50.0 |
| P1 | Guinea | `semantic` | — → 4 | — → 50.0 |
| P1 | Arbil | `semantic` | — → 5 | — → 50.0 |
| P3 | Tarrafal | `semantic` | — → 1 | — → 50.0 |
| P3 | Puglia | `semantic` | — → 2 | — → 50.0 |
| P3 | Amazonas (`R134e167D3f09C90718A98e0`) | `semantic` | — → 3 | — → 50.0 |
| P3 | Guekedou | `semantic` | — → 4 | — → 50.0 |
| P3 | Amazonas (`R3D23cf2FCf794605deF26ad`) | `semantic` | — → 5 | — → 50.0 |
| P6 | Ulcinj | `semantic` | — → 2 | — → 57.7 |
| P6 | Auckland | `semantic` | — → 3 | — → 57.7 |
| P6 | Colombia | `semantic` | — → 4 | — → 57.7 |
| P6 | Chlef | `semantic` | — → 5 | — → 57.7 |
| N1 | Ohangwena | `semantic` | — → 1 | — → 53.0 |
| N1 | Mwanza | `semantic` | — → 2 | — → 53.0 |
| N1 | Denmark | `semantic` | — → 3 | — → 50.0 |
| N1 | Riga | `semantic` | — → 4 | — → 50.0 |
| N1 | Amambay | `semantic` | — → 5 | — → 50.0 |

### local

| Case | Candidate | Path | Final rank, baseline → bypass | Final score, baseline → bypass |
|---|---|---|---|---|
| E1 | Judgment | `label_search` | — → 2 | — → 91.3 |
| E1 | Motion for Summary Judgment | `label_search` | 2 → 3 | 86.0 → 86.0 |
| E1 | Summary Judgment in Lieu of Complaint | `label_search` | 3 → 4 | 79.4 → 79.4 |
| E1 | Abstract of Judgment | `semantic` | 4 → 5 | 70.8 → 70.8 |
| E1 | JJ10 Applications relating to originating process or Statement of Case or for default or summary Judgment (UK J-CODE) | `label_search` | 5 → 6 | 61.3 → 61.3 |
| E1 | Dispositive Motions | `label_search` | 6 → 7 | 60.0 → 60.0 |
| E1 | Judgment Lien | `label_search` | 7 → 8 | 47.3 → 47.3 |
| E1 | Judgment of Arbitration | `label_search` | 8 → 9 | 47.3 → 47.3 |
| E1 | Administrative Judgment | `label_search` | 9 → 10 | 47.3 → 47.3 |
| E2 | Bid | `semantic` | — → 4 | — → 54.7 |
| E2 | Antitrust - Bid-Rigging Claims | `semantic` | 4 → 5 | 49.1 → 49.1 |
| E2 | Seller | `semantic` | — → 6 | — → 47.8 |
| P1 | Negligence | `semantic` | — → 5 | — → 54.6 |
| P4 | Ratification | `semantic` | — → 4 | — → 50.6 |
| P4 | Dispute Events | `semantic` | 4 → 5 | 49.9 → 49.9 |

Hashing is a deterministic diagnostic provider, not evidence of semantic model quality. Its N1 admissions are all explicitly listed above. Those five additions and the other non-target additions are evidence to inspect, not newly adjudicated answer sets. The two Amazonas entries are different IRIs.

Every case’s full final candidate list is retained in the paired JSON, including unchanged candidates, scores, IRIs, paths, and ordering.

## Method and scope

Measured on 2026-09-20 against the same eight owner-approved public cases and 18,325 normalized concepts as the [frozen baseline](embedding-baseline.md). Each embedding variant ran in a separate fresh process, sequentially, using the pinned offline inputs. Each process built one index and compared the real pipeline with its short-label gate against a shared-index pipeline with that entire gate bypassed. Semantic top-k stayed 5 and score floor stayed 45; lexical retrieval, place gate, blocklist, deduplication, ranking, model, and answer sets stayed fixed.

All three uninstrumented controls and their recorded traces exactly reproduce the frozen baseline candidates, including scores, metadata, extraction paths, and ordering. Source, input, model-file, and baseline hashes were checked before interpreting the comparison. This experiment measures candidate eligibility and relevance; it records no latency or memory comparison.

The six positive queries comprise two exact labels and four paraphrases. Hit rates count approved targets within the first 1 or 5 final candidates, divided by six. Negative-control counts cover all final candidates, not only the first five. N1 and N2 have no intended target; the report exposes their candidates without adding blanket false-positive annotations to the frozen answers.

## Named guard probes

These probes use controlled raw `MatchCandidate` scores through the real `_rank`, blocklist, and gates. They are separate from the eight full-pipeline public-query measurements and their metric denominators. The input scores do not claim that the public corpus actually retrieves these examples at those values.

| Named guard | Controlled input | Baseline | Bypass | Result |
|---|---|---|---|---|
| `short_fuzzy_law` | “law of the sea” → law, lexical score 88 | Demoted to 40; removed | Survives at 88 | Short-label fuzzy protection lost |
| `short_exact_tax` | “tax” → Tax, lexical score 99 | Survives at 99 | Survives at 99 | Exact-label behavior intact |
| `uncorroborated_place` | “Presumptions” → Northern Mariana Islands, branch Location, lexical score 90 | Demoted to 40; removed | Demoted to 40; removed | Place-gate protection intact for this tagged input |
| `blocked_action_auction` | “Action” → Auction, explicit blocklist entry, lexical score 90 | Removed by blocklist | Removed by blocklist | Blocklist protection intact |

Results are identical across the three variants because the probes do not use embeddings. Every normal-mode guard meets its expected outcome. Bypassing the entire gate deliberately removes its lexical protection as well as its semantic effect.

The public corpus preserves the baseline's `no-branch-inference` normalization. Newly admitted hashing candidates such as Denmark have an empty branch and a `not-a-place` gate reason. The intact Location-tagged probe therefore does not establish that all geographic labels in this corpus receive place-gate protection.

## Artifacts and reproduction

- [Runner](../../benchmarks/embedding_gate_ablation.py) and [real-pipeline tests](../../tests/test_embedding_gate_ablation.py).
- Complete paired candidates, target traces, deltas, guards, and provenance: [disabled](embedding-gate-ablation-disabled.json), [hashing](embedding-gate-ablation-hashing.json), [local](embedding-gate-ablation-local.json).
- [Experiment plan and decision rule](../plans/2026-09-20-0605-test-semantic-short-gate-ablation-plan.md); [stage evidence motivating the lever](embedding-diagnostics.md).

Use the pinned inputs and environment described in the [baseline reproduction instructions](embedding-baseline.md#reproduce). Run each variant separately; omit `--model-path` for disabled and hashing:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  /tmp/folio-embedding-venv/bin/python -m benchmarks.embedding_gate_ablation \
  --owl /tmp/folio-public-benchmark.owl --variant local \
  --model-path /tmp/folio-model-cache/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41 \
  --output /tmp/embedding-gate-ablation-local.json
```

The fixture hash remains `70603b759e56ad252129fc5c4de95c6418f436e06598e773e0ab79c71a759c72`; the corpus hash remains `a829784b946f3c7e87d55c4734d895a1ece589f78fb8618a2c05008c67c29c73`. Raw artifacts identify the committed runner revision, exact runner and library source hashes, frozen baseline files, model files, Python, and installed packages. No private gold or campaign data is read.
