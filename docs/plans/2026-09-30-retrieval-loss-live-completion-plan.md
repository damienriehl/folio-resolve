---
title: Complete retrieval loss measurement from merged implementation
type: eval
date: 2026-09-30
artifact_contract: ce-unified-plan/v1
product_contract_source: owner-dispatch
---

# Complete retrieval loss measurement from merged implementation

## Goal Capsule

Complete U8 of `2026-09-27-1922-feat-retrieval-recall-loss-attribution-plan.md` without repeating implementation or starting a duplicate paid campaign. This worker prepares and verifies locally; the orchestrator owns network, credentials, run ownership, and publication.

## Product Contract

All original R1–R15 gates remain. The card's “workers building U1/U3/U9” is stale: folio-resolve implementation and fixes are merged in stored history at 4790776 and 964791c. Mapper's override is available at 162bcd43 and is an ancestor of its locally stored origin/main; its pinned recall worktree is at that commit. The enrich recall pin is bb576ac. These observations are local evidence, not a remote freshness check.

Neither required report exists in this branch, the main checkout's benchmark directory, or the inspected `folio-resolve-recall-run` worktree. That run worktree is at 191857a, an earlier U2 commit, and its data directory contains no subdirectories. This does not prove that no other process or worktree owns a campaign. Budget consumption is unverified; the $25 authorization is a cumulative ceiling, not a new allowance.

## Planning Contract

Use the merged harness in a clean orchestrator-owned work checkout. Do not run from the older U2 checkout or change the existing app worktrees from this worker. Discover the existing campaign directory and spend ledger through the owning controller before launching anything. Preserve all checkpoints. Reuse one campaign directory across every arm and restart.

## Implementation Units

1. Reconcile active controller ownership and all completed run receipts; locate any existing attribution, arm outputs, spend ledger, and canary evidence. Verify app checkout cleanliness, the mapper override pin, and installed folio-resolve 0.4.0. No new spend until cumulative consumption and remaining budget are known.
2. Run the original U8 attribution canary and eight shards with `eval/run_recall_attribution.py`; finalize and require exact R4 reconciliation before dependent work. The original plan calls for a one-item canary, but this CLI has no item-limit option: use the established shard canary procedure or implement a reviewed bounded canary; do not invent a flag or claim one whole shard is one item.
3. Run `eval/run_recall_consumers.py` with `--mapper-commit 162bcd43`, both pinned checkout paths, the finalized attribution and its digest, the corpus/leak manifests, salt-file reference, and the same `--campaign-dir` on every invocation. Start with `--arms enrich:deterministic,mapper:deterministic`. Paid arms are `enrich:gemini-3-flash-preview,enrich:gpt-6-luna,mapper:gemini-3-flash-preview,mapper:gpt-6-luna`; first use `--canary-only`, then require the combined projection plus prior spend to fit the remaining authorization before removing that flag. Supply the CLI's required conservative token bounds for both consumers; derive them from inspected runner behavior rather than guessing.
4. Download and verify the pinned model through the authorized network lane; run the embedding ceiling and its offline integration test. Run the one-time LLM ceiling through the authorized orchestrator lane.
5. Finalize with `eval/run_recall_report.py --record-experiment`, supplying the finalized attribution, consumers, embedding, and LLM artifacts with their required digest arguments. Require both U8 output files, no restricted identifiers or absolute paths, zero leak collisions, and exactly one new park entry.

## Verification Contract

Local harness tests use fake runners and no paid calls; their results appear in the work out-file. They establish implementation behavior, not live P/R/F1, spend, model availability, or R4 reconciliation. The original U8 verification contract remains authoritative for live acceptance. No remote checks or fresh CI claims were made.

## Definition of Done

The original U8 report files, exact reconciliation, verified budget/canary evidence, zero leak collisions, one park entry, and a review receipt exist. Then publish through the orchestrator. The network step is the model download and authorized provider measurements after ownership/budget reconciliation; push/PR/publication follow verified results. No additional Damien decision is currently established as necessary.

Rollback: preserve machine-local checkpoints; revert the eventual report commit, not the merged harness or existing app flows. This preparation belongs on the default branch independently of the older documentation branch named in the dispatch.
