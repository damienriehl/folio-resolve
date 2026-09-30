---
title: "Frozen benchmark evidence must be proven by authenticated replay, not by matching the live library source"
lane: evaluation
module: benchmarks
date: 2026-09-30
problem_type: test_failure
component: testing_framework
severity: high
symptoms:
  - "Any edit under src/ fails core CI with `Ablation library_source_sha256 differs`, even when scoring is untouched"
  - "13 failures and 16 errors across the embedding lexical-boundary, precision, precision-relations and semantic-gate-replay suites"
root_cause: logic_error
resolution_type: test_fix
tags: [frozen-evidence, receipts, provenance, replay, sha256, ci, benchmarks]
status: active
related:
  - docs/solutions/2026-09-07-review-gate-catches-self-inflicted-regressions.md
  - docs/uat/2026-09-30-uat-report.md
---

# Frozen benchmark evidence must be proven by authenticated replay, not by matching the live library source

## Problem

The committed embedding benchmark receipts (`docs/benchmarks/embedding-*.json`) each record a
`library_source_sha256`: a digest of every `.py` and `.json` file under `src/folio_resolve/`
(`benchmarks/embedding_semantic_gate_replay.py:87`, `source_identity`). The offline validators
compared that recorded value with the digest of the **current** tree. Between 2026-09-19, when the
receipts were frozen, and 2026-09-30 no library edit landed, so nobody noticed. The first one
(PR #65: a `py.typed` marker and clearer missing-extra errors) failed core CI.

## Symptoms

- `ValueError: Ablation library_source_sha256 differs` from tests that never load a model.
- 13 failures and 16 errors in `tests/test_embedding_{lexical_boundaries,precision,precision_relations,semantic_gate_replay}.py`.
  They run in core CI, so every library PR would be blocked.

## What didn't work

- **Refreshing the receipts' hashes.** This would falsely attest that the historical measurements were
  collected from the new source. Codex workers correctly refused.
- **Editing the validators.** The receipts also pin the bytes of the benchmark, diagnostic,
  ablation and validator sources themselves (`benchmark_source_sha256` and siblings). Changing a
  validator trips those identity checks too. The worker stopped and asked, rather than weakening them.
- **Replay equality alone.** A first version replayed the frozen inputs with the current library and
  accepted the receipt when the outputs matched. Review round 1 (P1) showed that this trusts whatever
  receipt is on disk: change selective ranking, regenerate a receipt under the recorded identity,
  replace the file, and the new outputs "reproduce". Rehashing a tampered pool's `pool_sha256`
  passed the same way.

## Solution

A **new** module, `benchmarks/evidence_replay.py`, that no receipt hashes, so it can change freely:

1. **Authenticate first.** `FROZEN_SHA256` pins the SHA-256 of every committed historical input the
   offline entry points read. That covers the receipts, the collections and judgments, the
   baselines, and the fixtures: 13 paths in all. `_authenticated` checks the bytes before parsing
   and raises `Frozen artifact SHA-256 differs: <path>`. Dictionaries passed in memory must equal
   the authenticated artifact's content.
2. **Then prove by replay.** Replay the frozen inputs with the current library. The outputs must
   equal the authenticated receipt exactly. A mismatch raises a source-drift error that names the
   recorded and current digests.
3. **Only then hand the validators the recorded identity.** The unchanged validators take the
   library identity as a parameter (for example `verify_saved(saved, fixture, variant, library_sha)`
   at `benchmarks/embedding_semantic_gate_replay.py:98`). The offline view passes the receipt's
   recorded digest, so every other identity check still runs byte-for-byte.
4. **Collection stays strict.** `offline(module)` rebinds only the offline entry points
   (`_OFFLINE_FUNCTIONS`). `run_collect`, `run_ablation`, `verify_pins` and `main` keep their
   original globals and still reject current-source drift.

## Why this works

A frozen receipt answers "what did this measurement produce, from which inputs?" Its authenticity
comes from pinned bytes, and its continued truth comes from exact reproduction. Neither needs the
library to be byte-identical to the collection-time source. Requiring that confused *provenance*
(recorded, immutable) with *validity under today's code* (checked by replay). Pinning the digests
in a module that the receipts don't hash breaks the circularity that made the validators uneditable.

## Prevention

- When a receipt pins a digest of mutable source, decide explicitly whether offline validation
  checks **provenance** (the recorded digest's integrity) or **reproduction** (replay equality).
  Never check "equals the live tree" in a path that runs on every PR.
- Replay equality needs an authenticated expectation. Pin the expected artifact's bytes somewhere
  the artifact itself cannot rewrite.
- Keep a test that the pin table covers every path the offline entry points open, and single-file
  corruption tests for each pinned input (`tests/test_evidence_replay.py`).
- When a validator hashes its own source, put new policy in a new, unhashed module. Don't edit the
  hashed one.
- Adding a new frozen artifact means adding its digest to `FROZEN_SHA256` in the same PR.

## Related: authored prose and the firm-surface scan

The raw n-gram firm-surface scan (`eval/folio_eval/leakcheck.py`) also flags ordinary words in
authored docs. On `main` the README, a merged plan, and `docs/migration/SCHEDULE.md` all scan non-zero.
The disposition used in `docs/plans/2026-09-30-1730-chore-repo-hygiene-plan.md` (H1): classify
matches locally, block only non-generic ones, and **never list the matched n-grams in a commit, PR
or doc**. Listing them would reveal which terms are in the confidential manifest. The zero-collision
gate for synthetic artifacts and generated reports is unchanged.
