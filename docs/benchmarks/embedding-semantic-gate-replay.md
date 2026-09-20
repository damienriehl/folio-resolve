# Semantic-only short-label gate replay

Replaying the frozen candidate inputs preserves the observed local-model gain: Negligence remains rank 5, both exact targets remain rank 1, and all four lexical protection probes match the original gate. Local hit@5 stays at 3/6, compared with 2/6 under the original gate. Production defaults remain unchanged.

The selective rule still admits semantic candidates: local E1 gains Judgment at rank 5 (65.3), and hashing still returns five candidates for negative query N1. Passing the lexical probes does not establish general precision or production adoption readiness.

## Method and identities

This is ranking replay of the [prior full ablation](embedding-gate-ablation.md), using its saved pre-rank candidates. It performs no new model inference, corpus retrieval, or timing measurement. The [raw replay artifact](embedding-semantic-gate-replay.json) contains complete input, mutated, and final candidate snapshots, target ranks, deltas, guards, and provenance.

The three arms are original (`baseline`), full ShortLabelGate bypass (`bypass`), and bypass only for an extraction path exactly equal to `semantic` (`selective`). The selective adapter delegates one complete candidate stream to the real ranker, retaining the blocklist, place gate, score floor, deduplication, and tie-breaking. Other extraction paths retain the original short-label gate. All arms use empty domains and heading terms, and no context text.

Before comparing selective results, the runner verified both saved controls for all 24 variant/case pairs: 48 exact final candidate lists, including every field and order, plus the corresponding mutated input lists. Fresh candidate objects are reconstructed for each arm. The replay rejects frozen-input, fixture, source, configuration, and paired-input drift.

The eight approved unambiguous cases and answers are unchanged: E1/E2 are exact queries, P1/P3/P4/P6 are paraphrases, and N1/N2 are negative controls. Only the six positive cases enter hit-rate denominators. The eight constructed guard probes below are separate evidence.

The recorded retrievals used 18,325 concepts, semantic top-k 5, label search limit 10, and score floor 45. The local model identity is `sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; hashing is a 256-dimensional diagnostic provider. Corpus policy and model-file hashes are inherited evidence retained in the JSON, not newly measured here.

| Identity | SHA-256 |
| --- | --- |
| Frozen disabled ablation | `92a9bedd1564c7b41743e99cb4e2f41a852aabe39ba2977d38d49421a2374e9f` |
| Frozen hashing ablation | `123aea07faaf993ce856a72e8e0252544c568177ac5866d872b74799e3deaa1b` |
| Frozen local ablation | `8d605aba45356a40a131298b721eae9b03c5c60ad39a7f8dcf2dfbced5a2cd92` |
| fixture_sha256 | `70603b759e56ad252129fc5c4de95c6418f436e06598e773e0ab79c71a759c72` |
| library_source_sha256 | `3f1682970e0cd62535777946e38c8776687ca355cb7ac81f70aa87fb615e1e44` |
| replay_source_sha256 | `ce7ec1b5b71d3ab91d2039948e326a01b2e93907b88735937b8d9a1b0db3b107` |
| Replay configuration | `9baa1a377d774b2c347c100bd24c35998feaa1bf5b6ae59b8cd341e3684960a8` |

## Positive hits and negative counts

Each cell lists **original / full bypass / selective**. Hit counts have denominator six. Negative counts include every final candidate, not just the top five.

| Variant | Hit@1 | Hit@5 | N1 candidates | N2 candidates |
| --- | --- | --- | --- | --- |
| disabled | 2/6 / 2/6 / 2/6 | 2/6 / 2/6 / 2/6 | 0 / 0 / 0 | 0 / 0 / 0 |
| hashing | 1/6 / 1/6 / 1/6 | 2/6 / 2/6 / 2/6 | 0 / 5 / 5 | 0 / 0 / 0 |
| local | 2/6 / 2/6 / 2/6 | 2/6 / 3/6 / 3/6 | 0 / 0 / 0 | 0 / 0 / 0 |

N1 is “purple nebula hums a lullaby”; N2 is “my sourdough starter smells fruity”. Their approved policy is no intended target. These counts expose admissions; they are not exhaustive false-positive labels or a precision estimate.

## Approved target final ranks

Each rank cell lists **original / full bypass / selective**; `—` means absent from the final candidate list.

| Case | Approved target | Disabled | Hashing | Local |
| --- | --- | --- | --- | --- |
| E1 | [Summary Judgment](https://folio.openlegalstandard.org/R8K6VFq9c39kbQouame6Onf) | 1 / 1 / 1 | 1 / 1 / 1 | 1 / 1 / 1 |
| E2 | [Auction](https://folio.openlegalstandard.org/R8kOvHwkY6TrQmB7RnYiWNO) | 1 / 1 / 1 | 3 / 3 / 3 | 1 / 1 / 1 |
| P1 | [Negligence](https://folio.openlegalstandard.org/R7nqxxlAfhYqqSA2UQ5UxpX) | — / — / — | — / — / — | — / 5 / 5 |
| P3 | [Statute of Limitations](https://folio.openlegalstandard.org/RDKJ7nEqeDxNDRxI2Wqil1N) | — / — / — | — / — / — | — / — / — |
| P4 | [Res Judicata](https://folio.openlegalstandard.org/R8OsGCPOsihiJLtbFkIbRfx) | — / — / — | — / — / — | — / — / — |
| P6 | [Consideration](https://folio.openlegalstandard.org/RBKXEdlGEMzeuinxEdRGBF5) | — / — / — | — / — / — | — / — / — |

## Every candidate delta

Tables include every addition, removal, and rank or score change, keyed by IRI and extraction path. Candidate links retain identity when labels repeat. `—` means that path-specific candidate is absent; rank and score arrows run **control → selective**. An unchanged score is printed once. In the JSON delta helper, `baseline`/`baseline_rank` name the selected control and `bypass`/`bypass_rank` name the selective result, even inside `deltas.bypass`.

Gate-reason-only changes are retained in the complete snapshots and are excluded from these structural delta tables. Semantic survivors use `semantic short-label gate bypassed`; lexical candidates retain normal reasons. No shared path-specific surviving candidate changes score in these comparisons. Local E1 Judgment changes winning path: full bypass keeps lexical Judgment at 91.3, while selective keeps semantic Judgment at 65.3. This appears as one removal and one addition against full bypass.

### Against original

**disabled: 0 delta rows.**

No structural deltas in any case.

**hashing: 29 delta rows.**

| Case | Candidate | Path | Change | Rank | Score |
| --- | --- | --- | --- | --- | --- |
| E1 | [Florida](https://folio.openlegalstandard.org/R2909d38D76fc77Adea35e7b) | semantic | added | — → 5 | — → 70.7 |
| E1 | [Rivera](https://folio.openlegalstandard.org/R2A41deBDA16b1A4738FD9c6) | semantic | added | — → 6 | — → 70.7 |
| E1 | [Abstract of Judgment](https://folio.openlegalstandard.org/R2iMgtqApgam5KhZ1XpJ0s) | label_search | changed | 6 → 11 | 50.6 |
| E1 | [Jabat](https://folio.openlegalstandard.org/R4890017CC29336732e24299) | semantic | added | — → 7 | — → 70.7 |
| E1 | [Cyprus](https://folio.openlegalstandard.org/R4C5f2bCD6543024063B3c41) | semantic | added | — → 8 | — → 70.7 |
| E1 | [Judgment Lien](https://folio.openlegalstandard.org/R7mLi0c5rJV5GYGGuEi5dmD) | label_search | changed | 7 → 12 | 47.3 |
| E1 | [Judgment of Arbitration](https://folio.openlegalstandard.org/R81opfsWnlEQvW2jFPWFOxo) | label_search | changed | 8 → 13 | 47.3 |
| E1 | [Administrative Judgment](https://folio.openlegalstandard.org/R82iOqJMuYOjOGxNmuj1pDp) | label_search | changed | 9 → 14 | 47.3 |
| E1 | [Yalova](https://folio.openlegalstandard.org/RA775144C678614Da0e22079) | semantic | added | — → 4 | — → 75.0 |
| E1 | [JJ10 Applications relating to originating process or Statement of Case or for default or summary Judgment (UK J-CODE)](https://folio.openlegalstandard.org/RODsAVADydNHhEWk3l57k1) | label_search | changed | 4 → 9 | 61.3 |
| E1 | [Dispositive Motions](https://folio.openlegalstandard.org/RUM0A3dwvk0A2dp3WHxNrj) | label_search | changed | 5 → 10 | 60.0 |
| P1 | [Bosilovo](https://folio.openlegalstandard.org/R0FE3503CC85797351e297d1) | semantic | added | — → 2 | — → 50.0 |
| P1 | [Nunavut](https://folio.openlegalstandard.org/R110bd9C43ebeF5275a4Ef56) | semantic | added | — → 3 | — → 50.0 |
| P1 | [Guinea](https://folio.openlegalstandard.org/R15E945B1Dc01430fd4DEf04) | semantic | added | — → 4 | — → 50.0 |
| P1 | [Arbil](https://folio.openlegalstandard.org/R3B5a1676D17bAB6ac141c9e) | semantic | added | — → 5 | — → 50.0 |
| P3 | [Tarrafal](https://folio.openlegalstandard.org/R08E8b9F74f4eCBF74bAF036) | semantic | added | — → 1 | — → 50.0 |
| P3 | [Puglia](https://folio.openlegalstandard.org/R0B9e9643A3c460C9b14580e) | semantic | added | — → 2 | — → 50.0 |
| P3 | [Amazonas](https://folio.openlegalstandard.org/R134e167D3f09C90718A98e0) | semantic | added | — → 3 | — → 50.0 |
| P3 | [Guekedou](https://folio.openlegalstandard.org/R1CC264214a3cCF49eaD9d92) | semantic | added | — → 4 | — → 50.0 |
| P3 | [Amazonas](https://folio.openlegalstandard.org/R3D23cf2FCf794605deF26ad) | semantic | added | — → 5 | — → 50.0 |
| P6 | [Ulcinj](https://folio.openlegalstandard.org/R03Fc64344618BA02ea7C47c) | semantic | added | — → 2 | — → 57.7 |
| P6 | [Auckland](https://folio.openlegalstandard.org/R1117dbDF6309A7Cd9571f72) | semantic | added | — → 3 | — → 57.7 |
| P6 | [Colombia](https://folio.openlegalstandard.org/R5DC0564093b84E4f17A1176) | semantic | added | — → 4 | — → 57.7 |
| P6 | [Chlef](https://folio.openlegalstandard.org/R7367d1F519b5FE21781E155) | semantic | added | — → 5 | — → 57.7 |
| N1 | [Denmark](https://folio.openlegalstandard.org/R1ABd0796Ff01FF7573A211f) | semantic | added | — → 3 | — → 50.0 |
| N1 | [Riga](https://folio.openlegalstandard.org/R2311533686ac96916eE33cd) | semantic | added | — → 4 | — → 50.0 |
| N1 | [Amambay](https://folio.openlegalstandard.org/R4C1381EC293922Fb3dF319e) | semantic | added | — → 5 | — → 50.0 |
| N1 | [Ohangwena](https://folio.openlegalstandard.org/R97A8cc9F0d1154478cE2ed3) | semantic | added | — → 1 | — → 53.0 |
| N1 | [Mwanza](https://folio.openlegalstandard.org/RB371ab6302b0540019EF495) | semantic | added | — → 2 | — → 53.0 |

No structural deltas for E2, P4, N2.

**local: 12 delta rows.**

| Case | Candidate | Path | Change | Rank | Score |
| --- | --- | --- | --- | --- | --- |
| E1 | [Judgment Lien](https://folio.openlegalstandard.org/R7mLi0c5rJV5GYGGuEi5dmD) | label_search | changed | 7 → 8 | 47.3 |
| E1 | [Judgment of Arbitration](https://folio.openlegalstandard.org/R81opfsWnlEQvW2jFPWFOxo) | label_search | changed | 8 → 9 | 47.3 |
| E1 | [Administrative Judgment](https://folio.openlegalstandard.org/R82iOqJMuYOjOGxNmuj1pDp) | label_search | changed | 9 → 10 | 47.3 |
| E1 | [Judgment](https://folio.openlegalstandard.org/R9jpIjzc10qdgl78jXOZtfm) | semantic | added | — → 5 | — → 65.3 |
| E1 | [JJ10 Applications relating to originating process or Statement of Case or for default or summary Judgment (UK J-CODE)](https://folio.openlegalstandard.org/RODsAVADydNHhEWk3l57k1) | label_search | changed | 5 → 6 | 61.3 |
| E1 | [Dispositive Motions](https://folio.openlegalstandard.org/RUM0A3dwvk0A2dp3WHxNrj) | label_search | changed | 6 → 7 | 60.0 |
| E2 | [Antitrust - Bid-Rigging Claims](https://folio.openlegalstandard.org/R73DJPQnmrqO90OL7OBDZJU) | semantic | changed | 4 → 5 | 49.1 |
| E2 | [Seller](https://folio.openlegalstandard.org/R7kkf8NvitG8hubZUnTwevG) | semantic | added | — → 6 | — → 47.8 |
| E2 | [Bid](https://folio.openlegalstandard.org/RiQHdRDZfaAIOlPy9vfwB8) | semantic | added | — → 4 | — → 54.7 |
| P1 | [Negligence](https://folio.openlegalstandard.org/R7nqxxlAfhYqqSA2UQ5UxpX) | semantic | added | — → 5 | — → 54.6 |
| P4 | [Ratification](https://folio.openlegalstandard.org/RBDRtzbiNquelJuhuvpkCWl) | semantic | added | — → 4 | — → 50.6 |
| P4 | [Dispute Events](https://folio.openlegalstandard.org/RDFhtJuTWcY7UZGV9F5yOoA) | semantic | changed | 4 → 5 | 49.9 |

No structural deltas for P3, P6, N1, N2.

### Against full bypass

**disabled: 9 delta rows.**

| Case | Candidate | Path | Change | Rank | Score |
| --- | --- | --- | --- | --- | --- |
| E1 | [Abstract of Judgment](https://folio.openlegalstandard.org/R2iMgtqApgam5KhZ1XpJ0s) | label_search | changed | 7 → 6 | 50.6 |
| E1 | [Judgment Lien](https://folio.openlegalstandard.org/R7mLi0c5rJV5GYGGuEi5dmD) | label_search | changed | 8 → 7 | 47.3 |
| E1 | [Judgment of Arbitration](https://folio.openlegalstandard.org/R81opfsWnlEQvW2jFPWFOxo) | label_search | changed | 9 → 8 | 47.3 |
| E1 | [Administrative Judgment](https://folio.openlegalstandard.org/R82iOqJMuYOjOGxNmuj1pDp) | label_search | changed | 10 → 9 | 47.3 |
| E1 | [Summary Judgment in Lieu of Complaint](https://folio.openlegalstandard.org/R9RRyCC6w6V3oGeHdAUkdAo) | label_search | changed | 4 → 3 | 79.4 |
| E1 | [Judgment](https://folio.openlegalstandard.org/R9jpIjzc10qdgl78jXOZtfm) | label_search | removed | 2 → — | 91.3 → — |
| E1 | [Motion for Summary Judgment](https://folio.openlegalstandard.org/RGcqiLEe0IK8lPRt5mFC0D) | label_search | changed | 3 → 2 | 86.0 |
| E1 | [JJ10 Applications relating to originating process or Statement of Case or for default or summary Judgment (UK J-CODE)](https://folio.openlegalstandard.org/RODsAVADydNHhEWk3l57k1) | label_search | changed | 5 → 4 | 61.3 |
| E1 | [Dispositive Motions](https://folio.openlegalstandard.org/RUM0A3dwvk0A2dp3WHxNrj) | label_search | changed | 6 → 5 | 60.0 |

No structural deltas for E2, P1, P3, P4, P6, N1, N2.

**hashing: 14 delta rows.**

| Case | Candidate | Path | Change | Rank | Score |
| --- | --- | --- | --- | --- | --- |
| E1 | [Florida](https://folio.openlegalstandard.org/R2909d38D76fc77Adea35e7b) | semantic | changed | 6 → 5 | 70.7 |
| E1 | [Rivera](https://folio.openlegalstandard.org/R2A41deBDA16b1A4738FD9c6) | semantic | changed | 7 → 6 | 70.7 |
| E1 | [Abstract of Judgment](https://folio.openlegalstandard.org/R2iMgtqApgam5KhZ1XpJ0s) | label_search | changed | 12 → 11 | 50.6 |
| E1 | [Jabat](https://folio.openlegalstandard.org/R4890017CC29336732e24299) | semantic | changed | 8 → 7 | 70.7 |
| E1 | [Cyprus](https://folio.openlegalstandard.org/R4C5f2bCD6543024063B3c41) | semantic | changed | 9 → 8 | 70.7 |
| E1 | [Judgment Lien](https://folio.openlegalstandard.org/R7mLi0c5rJV5GYGGuEi5dmD) | label_search | changed | 13 → 12 | 47.3 |
| E1 | [Judgment of Arbitration](https://folio.openlegalstandard.org/R81opfsWnlEQvW2jFPWFOxo) | label_search | changed | 14 → 13 | 47.3 |
| E1 | [Administrative Judgment](https://folio.openlegalstandard.org/R82iOqJMuYOjOGxNmuj1pDp) | label_search | changed | 15 → 14 | 47.3 |
| E1 | [Summary Judgment in Lieu of Complaint](https://folio.openlegalstandard.org/R9RRyCC6w6V3oGeHdAUkdAo) | label_search | changed | 4 → 3 | 79.4 |
| E1 | [Judgment](https://folio.openlegalstandard.org/R9jpIjzc10qdgl78jXOZtfm) | label_search | removed | 2 → — | 91.3 → — |
| E1 | [Yalova](https://folio.openlegalstandard.org/RA775144C678614Da0e22079) | semantic | changed | 5 → 4 | 75.0 |
| E1 | [Motion for Summary Judgment](https://folio.openlegalstandard.org/RGcqiLEe0IK8lPRt5mFC0D) | label_search | changed | 3 → 2 | 86.0 |
| E1 | [JJ10 Applications relating to originating process or Statement of Case or for default or summary Judgment (UK J-CODE)](https://folio.openlegalstandard.org/RODsAVADydNHhEWk3l57k1) | label_search | changed | 10 → 9 | 61.3 |
| E1 | [Dispositive Motions](https://folio.openlegalstandard.org/RUM0A3dwvk0A2dp3WHxNrj) | label_search | changed | 11 → 10 | 60.0 |

No structural deltas for E2, P1, P3, P4, P6, N1, N2.

**local: 5 delta rows.**

| Case | Candidate | Path | Change | Rank | Score |
| --- | --- | --- | --- | --- | --- |
| E1 | [Abstract of Judgment](https://folio.openlegalstandard.org/R2iMgtqApgam5KhZ1XpJ0s) | semantic | changed | 5 → 4 | 70.8 |
| E1 | [Summary Judgment in Lieu of Complaint](https://folio.openlegalstandard.org/R9RRyCC6w6V3oGeHdAUkdAo) | label_search | changed | 4 → 3 | 79.4 |
| E1 | [Judgment](https://folio.openlegalstandard.org/R9jpIjzc10qdgl78jXOZtfm) | label_search | removed | 2 → — | 91.3 → — |
| E1 | [Judgment](https://folio.openlegalstandard.org/R9jpIjzc10qdgl78jXOZtfm) | semantic | added | — → 5 | — → 65.3 |
| E1 | [Motion for Summary Judgment](https://folio.openlegalstandard.org/RGcqiLEe0IK8lPRt5mFC0D) | label_search | changed | 3 → 2 | 86.0 |

No structural deltas for E2, P1, P3, P4, P6, N1, N2.

All additions against the original are semantic: none for disabled, 23 for hashing, and five for local. The hashing N1 admissions are Ohangwena (53.0), Mwanza (53.0), Denmark (50.0), Riga (50.0), and Amambay (50.0). The local admissions are Judgment (65.3), Bid (54.7), Seller (47.8), Negligence (54.6), and Ratification (50.6). The tables above also enumerate the other hashing admissions and every displaced candidate. These are observed candidates; only the approved answer sets determine positive hits.

## Controlled protection probes

These eight constructed inputs are four prior guard scenarios exercised once per extraction path. They are not model retrievals and do not enter the eight-case benchmark or six-positive denominator. “Absent” is final survival, with the post-rank score shown when a gate demotes the candidate. Blocked inputs retain their input score because the blocklist skips them before scoring.

| Probe | Path | Original | Full bypass | Selective | Matches original survival |
| --- | --- | --- | --- | --- | --- |
| `short_fuzzy_law` | label_search | Absent; score 40.0 | Survives 88.0 | Absent; score 40.0 | Yes |
| `short_fuzzy_law` | semantic | Absent; score 40.0 | Survives 88.0 | Survives 88.0 | No |
| `short_exact_tax` | label_search | Survives 99.0 | Survives 99.0 | Survives 99.0 | Yes |
| `short_exact_tax` | semantic | Survives 99.0 | Survives 99.0 | Survives 99.0 | Yes |
| `uncorroborated_place` | label_search | Absent; score 40.0 | Absent; score 40.0 | Absent; score 40.0 | Yes |
| `uncorroborated_place` | semantic | Absent; score 40.0 | Absent; score 40.0 | Absent; score 40.0 | Yes |
| `blocked_action_auction` | label_search | Absent; score 90.0 | Absent; score 90.0 | Absent; score 90.0 | Yes |
| `blocked_action_auction` | semantic | Absent; score 90.0 | Absent; score 90.0 | Absent; score 90.0 | Yes |

Selective preserves all four lexical survival outcomes and seven of eight probe outcomes overall. It deliberately loses the semantic short-fuzzy-law suppression: that candidate survives at 88.0. Both paths retain exact Tax, tagged-place demotion, and the explicit Action/Auction block. Tagged-place protection does not govern the admitted place names with empty branch metadata in the frozen corpus; this experiment does not enrich that metadata.

## Decision and reproduction

The prespecified condition passes: local P1 remains top-five, both local exact targets remain first, and all lexical guard survival outcomes match the original. This supports the narrow claim that the recorded recall benefit can coexist with lexical short-label filtering. The remaining semantic admissions, including the semantic law guard loss and hashing negative result, prevent treating that condition as a general safety or relevance finding.

Production defaults, retrieval settings, fixtures, answer sets, and prior artifacts stay unchanged. Six positives and two weak negative controls do not establish precision, provider-wide behavior, statistical significance, or adoption readiness. The preserved misses for P3/P4/P6 are still visible above. This bounded experiment ends with the measured evidence; further policy work requires a separate proposal.

Run from the repository root with its development environment:

```bash
.venv/bin/python -m benchmarks.embedding_semantic_gate_replay \
  --output docs/benchmarks/embedding-semantic-gate-replay.json
```

The command exited 0 and reported `preserves_observed_benefit_and_lexical_protection: true`. Report verification independently recomputed source/artifact hashes, exact controls, target ranks, hit rates, negative counts, delta membership/order, and the guard decision from the saved JSON. No additional tests are needed for generated prose; the real-ranker and drift tests belong to the replay runner, while this report is checked against its raw evidence.
