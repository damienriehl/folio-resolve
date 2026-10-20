---
artifact_contract: "ce-handoff/v1"
created_at: "2026-10-20"
title: "Sweep 2026-10-20 cloud lane: U10 finalize blocker, spend request, bounded attribution canary"
summary: "Cloud re-verification of main, the documented U10 finalize blocker, a spend-approval request for paid recall arms, and a new bounded canary for U8 attribution awaiting its Codex review receipt."
keywords: ["sweep-2026-10-20", "u10", "finalize", "u8", "canary", "spend"]
repository: "damienriehl/folio-resolve"
branch: "feat/attribution-bounded-canary"
base: "9dc8f9e"
merged_as: "faf9bba"
---

# Sweep 2026-10-20 cloud lane — folio-resolve

## 1. Re-verified state (cloud container, 2026-10-20)

- `main` = `9dc8f9e` (PR #68). No open PRs.
- `uv sync --frozen --extra dev` on Python 3.11, then the CI commands:
  - `pytest`: **2114 passed, 6 skipped, 3 deselected** on `main`.
  - `ruff check .`: all checks passed. `mypy`: no issues in 26 source files.
  - U10 repair regressions (`tests/test_eval_comparison_pilot.py -k 'repair_entrypoint_binds or finalization_extends'`): 4 passed.
- folio-python on PyPI: latest release still `0.4.0` (2026-08-18), which predates the upstream
  cache PR's merge. The 2026-08-21 cache handoff trigger remains unmet; the lock stays at 0.3.6.
- Remote branches `feat/u10-resumable-pilot`, `feat/u10-resumable-pilot-v2`, and
  `test/persona-uat-rerun` hold commits absent from `main` by ancestry. None were deleted (D3 rule:
  no deletion without a verified backup).

## 2. U10 finalize — blocker documented

The leak-scan collision named in the sweep card is already fixed and merged: `2e9c34f`
(`fix(eval): bind v2 finalization output metadata narrowly`, PR #62) admits the exact v2 output
path in repair admission and in the public-metadata binding. Its regressions pass on today's `main`.

What remains is the **live finalize replay**, and it cannot run in a cloud container. It needs the
preserved candidate checkout and its interpreter, the 90 completed shard receipts, the restricted
corpus manifest, leak manifest, salt, and pinned app roots. None of these exist in the public
repository, by design. The leak salt is git-ignored, so the cloud lane also cannot run any leak scan.

**Home-box steps (orchestrator, in order):**

1. Prepare a clean repair checkout of `main` (`9dc8f9e` or later) separate from the candidate
   checkout. Back up the original checkpoint directory first and verify the backup.
2. Run the finalize-only command from
   `docs/plans/2026-09-30-u10-finalization-recovery-plan.md`, step 3, with variables resolved from
   the preserved run configuration. Let fingerprint and leak checks fail closed; on a runtime
   mismatch, stop and record it rather than rewriting the stored fingerprint.
3. Verify zero collisions, then regenerate independently for a byte comparison (step 5 of that plan).
4. Regenerate the campaign report, route the measured verdict, publish the leak-clean aggregate
   with LFS tracking, and record a Codex review receipt per `CLAUDE.md`.
5. Check whether the U10 v3 lane worker launched 2026-09-30 finished; the cloud lane cannot see it.
   Reconcile its outputs with this replay before publishing either.

## 3. Next plan unit started — bounded attribution canary (PR on this branch)

U8 of the retrieval-recall plan begins "canary one item, then run U1 on 8 shards", but the
attribution CLI had no item limit. This branch adds `--canary-items N` to
`eval/run_recall_attribution.py` (plan: `docs/plans/2026-10-20-feat-attribution-bounded-canary-plan.md`)
and updates step 2 of `docs/plans/2026-09-30-retrieval-loss-live-completion-plan.md`.

- Evidence: two new canary tests fail on the prior code and pass after; full suite **2118 passed,
  6 skipped**; ruff and mypy clean; `git diff --check` clean.
- **Merged 2026-10-20 as `faf9bba` (PR #69).** CI Core 3.11/3.12 green on `3282d77`.
  ce-code-review receipt: Codex review PR #69 comment 6298516387 (GitHub integration, trigger
  "PR opened"), verdict merge, no findings. The receipt comment on the PR records this.

**Remaining home-box step for this PR:** leak-preflight the three changed docs and this handoff with
`eval/run_leakcheck.py check --manifest eval/synthetic/firm-surface-manifest-v1.json --salt-file eval/data/leakcheck-salt <files>`
under the H1 disposition rule. The cloud lane has no salt. The prose was written without access to
restricted surfaces, so any match should be generic vocabulary; if not, reword and follow up.

## 4. Open questions for Damien

### Q1 — Spend approval for paid recall-consumer arms (quick-decision)

The U8 consumer arms `enrich:gemini-3-flash-preview`, `enrich:gpt-6-luna`,
`mapper:gemini-3-flash-preview`, and `mapper:gpt-6-luna` need paid model calls. The earlier
authorization is a **$25 cumulative ceiling**, and consumption against it is unverified
(2026-09-30 plan). The harness already enforces a hard cumulative guard and a five-passage canary
projection before any full run.

- **Ask:** confirm the $25 cumulative ceiling still stands for these four arms, or set a new one.
- **Recommendation:** keep the $25 ceiling; require the orchestrator to reconcile the spend ledger
  first and to run `--canary-only` before any full paid batch. If the canary projects over the
  remaining balance, the run stops and the projection comes back to you (AE5).
- **Setting hint:** quick-decision.

### Q2 — None other

The release (H6) stays held on the adoption verdict per standing direction; nothing new to decide.

## Safety and scope

No credentials, corpus text, restricted matches, salts, item identifiers, checkpoint digests, or
machine-local paths appear here. Counts and public commit references only.
