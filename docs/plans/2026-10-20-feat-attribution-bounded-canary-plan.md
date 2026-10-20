---
title: "feat: Bounded one-item canary for recall attribution collection"
date: 2026-10-20
status: completed
artifact_contract: ce-unified-plan/v1
execution: code
product_contract_source: sweep-2026-10-20
---

# Bounded one-item canary for recall attribution collection

## Problem

U8 of `2026-09-27-1922-feat-retrieval-recall-loss-attribution-plan.md` starts with "canary one
item, then run U1 on 8 shards". `eval/run_recall_attribution.py` had no item limit, so the
2026-09-30 live-completion plan left the canary as an open choice: reuse a whole-shard procedure or
build a reviewed bounded canary. A whole shard is not one item, and the U9 traps note requires a
canary that proves startup before fan-out.

## Decision

Add `--canary-items N` to collection. It adapts at most N missing items of the assigned shard into
the same fingerprint-bound checkpoint store, reports `adapted <n> item(s) in shard <i>/<n>` on
stderr, and returns without finalizing, even with one shard. The full run resumes over canary items,
so the canary spends no duplicate work. The flag rejects zero, negatives, and `--finalize-only`
before any collection. No checkpoint schema, fingerprint, or report semantics change.

## Verification

- New tests: bounded canary then resume (2 adapter calls total, report only after the full run);
  canary on a completed shard adapts zero; invalid requests touch no checkpoint.
- Two canary tests fail on the prior code and pass after; the full core suite, ruff, and mypy pass.
- Live use stays with the U8 orchestrator lane on the home box; this unit runs no corpus.
