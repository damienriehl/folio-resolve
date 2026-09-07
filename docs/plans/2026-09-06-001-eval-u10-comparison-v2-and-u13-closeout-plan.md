---
title: U10 comparison v2 on the kept candidate, and the U13 campaign close-out - Plan
type: eval
date: 2026-09-06
topic: u10-comparison-v2-u13-closeout
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: owner-decision
execution: code
---

# U10 comparison v2 on the kept candidate, and the U13 campaign close-out - Plan

## Goal Capsule

- **Objective:** Close the Synthetic Benchmark F1 Campaign honestly: re-measure the R17 comparison
  against the candidate that actually exists today, then assemble the U13 campaign report and put
  the adoption verdict in front of the owner once, with the owner-run firm exam prepared to the
  command line.
- **Means:** One fresh U10 comparison run (`v2`) on the kept attempt-0001 candidate in a clean
  execution worktree; the campaign report assembled from the four experiment records, the v2
  verdicts, and the U12 attribution; the deferred-round reminders updated to the verdict; the
  handoff retired and the learning compounded.
- **Product authority:** `docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md`
  (R13, R14, R17, U10, U13) keeps authority over what counts as a pass. This plan only executes
  its close-out and records the owner rulings that route it.
- **Open blockers:** None. The owner ruled on 2026-09-06 (cockpit ask
  `folio-resolve-2026-09-06-1406-r17-loss-closing-path`): rerun the comparison on the kept
  candidate; if still a loss, close U13 as no-adopt without a further round-trip; a pass or an
  in-band hold stops for a fresh ask.

## Product Contract

### Summary

The finalized U10 v8 comparison (`eval/reports/synthetic-comparison-v1.json`, 2026-08-30) is a
CI-bound loss for the candidate against both deterministic incumbents. It measured the candidate
*before* attempt-0001 (local-window definition tie-order), which U12 attribution identified as the
causal lever for the loss and which is now the kept state. R17 says a loss blocks U13 until a
candidate passes. The report U13 must cite therefore has to be a comparison of the kept candidate,
not a superseded one.

### Key Decisions

- **KD1. Rerun, do not reinterpret.** `v1` is not re-read as if it measured the kept candidate.
  A `v2` run with identical inputs (corpus v1, synthetic answer-rule config v1, the same pinned
  incumbent worktrees, `--limit 60`) is the measurement. (owner-settled 2026-09-06)
- **KD2. Pre-authorized outcome branches.** `v2` loss → assemble U13 with a no-adopt verdict per
  consumer and update the deferred-round reminders; the owner sees the final verdict once. `v2`
  pass or in-band hold → stop, file a fresh ask. (owner-settled 2026-09-06)
- **KD3. Preserve v8; new checkpoint sequence number.** The v8 checkpoint stays untouched as
  evidence in `.worktrees/fix/u10-public-metadata-exemption`. `v2` runs in a new execution
  worktree with `--checkpoint-dir eval/data/u10-comparison/pilot-checkpoint-v9` and
  `--out eval/reports/synthetic-comparison-v2.json`; the checkpoint number continues the v4–v8
  series, the report number continues the committed v1.
- **KD4. Same verification shape as the U9 attempts.** Automatic finalization, then a clean-tree
  `--finalize-only` replay that must be byte-identical; leaf-by-leaf leak scan; the report is
  LFS-tracked like v1 (`.gitattributes`).
- **KD5. Owner steps stay owner steps.** The firm exam on the frozen 79 (R13) and the adoption
  verdict are Damien's. Agents prepare the exact exam command and the report; they never run the
  exam or score the frozen 79.
- **KD6. Stop-gate ruling applies.** Per the 2026-09-06 amendment under KTD12/U9, the
  diminishing-returns counter is not consulted; the four ledger records are the synthetic
  trajectory as-is.

### Requirements

- R1. `v2` runs from a clean, committed execution worktree whose `src/folio_resolve` is the kept
  attempt-0001 state (current `main`), with the runner's fingerprint accepting the tree.
- R2. `v2` uses byte-identical inputs to v8 except the candidate tree: corpus manifest, config,
  leak manifest, public-metadata file, salt file, mapper root at `626412b`, enrich root at
  `bb576ac`, `--limit 60`.
- R3. A one-item canary (`--max-new-items 1`) completes and publishes a shard receipt before the
  full run starts.
- R4. The full run finalizes automatically; a `--finalize-only` replay from a clean tree is
  byte-identical to the automatic report.
- R5. The committed report and every new ledger or doc line pass the leak scan; no synthetic item
  ID, protected surface, salt, digest, or checkpoint fingerprint appears in any committed or
  published text.
- R6. The U13 report joins: the four experiment records with lever scopes; the v2 verdicts with
  bands per consumer; the U12 attribution summary; the interim/final firm-exam slots left
  explicitly `owner-run: pending` with the exact command to run.
- R7. `briefs/on-deck.json`'s deferred-round reminder for the downstream adoption round is updated
  to carry the per-consumer verdicts (R14).
- R8. Retire `docs/handoffs/2026-09-05-u9-complete-fable-resume.md` and its private companion only
  after U13 is assembled, naming what absorbed each learning; run `ce-compound` on anything new.

### Acceptance Examples

- AE1. Running the canary in a tree with an untracked `.codex-out/` fails at fingerprint time; the
  same command in the clean execution worktree publishes one shard receipt.
- AE2. `sha256sum` of the automatically finalized `synthetic-comparison-v2.json` equals that of the
  `--finalize-only` replay.
- AE3. The leak scan over `synthetic-comparison-v2.json` and the campaign report reports zero
  collisions against `firm-surface-manifest-v1.json`.
- AE4. With both `v2` verdicts `loss`, the campaign report's verdict block reads no-adopt for each
  consumer and the on-deck reminder card names both verdicts; no ask is filed for the verdict
  itself, only the close-out notice.
- AE5. With either verdict `pass` or `hold`, no report is committed and a fresh ask is filed.

### Scope Boundaries

- No new U9 attempt. No change to `src/`, `eval/folio_eval/`, or the runner.
- No scoring of the frozen 79; no owner-run exam by an agent.
- U7 consensus sitting and U11 owner-run LLM lanes remain open and untouched.

### Dependencies / Assumptions

- The two pinned incumbent worktrees still exist at their recorded commits (verified 2026-09-06).
- v8 took ~21 hours wall-clock for 90 shards on 2026-08-29; assume the same for `v2`.
- `git-lfs` 3.5.1 is installed; the consumer venvs resolve `folio-resolve==0.4.0` (verified
  2026-09-06).

## Planning Contract

### Key Technical Decisions

- KTD1. **Execution worktree, not the main checkout.** The main checkout carries untracked
  `.claude/`, `.codex/`, `.codex-out/`, which the fingerprint refuses. `v2` runs in
  `.worktrees/eval/u10-comparison-v2` on branch `eval/u10-comparison-v2` from `main`, with its own
  `uv sync` venv. The salt file is read from the main checkout's absolute path (it does not exist
  in worktrees).
- KTD2. **Detached full run, event-driven wait.** The launcher runs the full command under
  `nohup` with `PYTHONHASHSEED=0`, `PYTHONDONTWRITEBYTECODE=1`, `env -u PYTHONPATH`, logging to a
  file inside the worktree's `.codex-out/`. The orchestrator waits on process exit, never polls.
- KTD3. **Codex prepares and verifies; the orchestrator launches and commits.** Worker policy:
  workers write the launcher and the preflight report and run the canary; the orchestrator starts
  the long run, verifies the finalization, and commits. Reviewers are Codex (2026-09-06 review-gate
  ruling in `CLAUDE.md`).

### Sequencing

U1 → U2 (canary) → U3 (full run, ~21h) → U4 (verify + commit) → U5 (U13 assembly) → U6 (close-out).

## Implementation Units

### U1. Execution worktree and preflight

- **Goal:** A clean execution worktree the runner's fingerprint accepts, with a launcher script
  that reproduces the v8 command exactly except for checkpoint dir and output path.
- **Requirements:** R1, R2.
- **Files:** `.worktrees/eval/u10-comparison-v2/` (machine-local), its `.codex-out/launch-v2.sh`
  (git-excluded).
- **Approach:** Worktree from `main`; `uv sync`; assert `.venv/bin/python` is 3.13 and
  `folio_eval` imports; `git status --porcelain` empty; incumbents at the pinned commits;
  `folio-resolve==0.4.0` resolved in both consumer venvs. Write the launcher with the v8 command
  from the retired reboot handoff (recoverable via `git show 315871c^:docs/handoffs/2026-08-29-u10-v8-reboot-handoff.md`),
  substituting `pilot-checkpoint-v9` and `synthetic-comparison-v2.json`.

> **Execution note (2026-09-07, from the run).** Two launches failed before any shard ran.
> `_assert_write_paths_are_safe` requires every candidate-repository output to be Git-ignored; its
> only published-report exemption is the hardcoded v1 path. Use
> `--out eval/data/reports/synthetic-comparison-v2.json` for the working launcher. A fresh worktree's
> plain `uv sync` also omits the optional `folio-python` dependency that the fingerprint queries;
> prepare it with `uv sync --extra folio`. With that shape, the third, one-item canary completed
> shard 1/90 in 11 minutes.

- **Verification:** Preflight report lists every assertion with its output.

### U2. Canary

- **Goal:** One shard completes end to end before committing ~21 hours of compute.
- **Requirements:** R3.
- **Approach:** Launcher with `--max-new-items 1`; confirm one shard receipt under
  `pilot-checkpoint-v9/items`; confirm the manifest fingerprint was created.
- **Verification:** AE1.

### U3. Full run

- **Goal:** All 90 shards and automatic finalization.
- **Approach:** Same launcher without the allowance, detached. On exit, the orchestrator reads the
  exit code and the presence of `final-complete.json` and `synthetic-comparison-v2.json`.
- **Verification:** exit 0; both artifacts present.

### U4. Verify, scan, commit

- **Goal:** The v2 report is a verified, leak-clean, LFS-tracked commit on `main`.
- **Requirements:** R4, R5.
- **Approach:** `--finalize-only` replay to a scratch path; byte-compare; leak scan; add the
  `.gitattributes` line; PR with a Codex review receipt.
  Produce the committed copy by copying the byte-verified ignored-path report to
  `eval/reports/synthetic-comparison-v2.json` and add the mirrored LFS line
  `eval/reports/synthetic-comparison-v2.json filter=lfs diff=lfs merge=lfs -text` to `.gitattributes`.
- **Verification:** AE2, AE3.

### U5. U13 campaign report

- **Goal:** One committed report per the campaign plan's U13, with owner-run slots explicit.
- **Requirements:** R6, R7.
- **Files:** `eval/reports/campaign-report-v1.md` (new), `briefs/on-deck.json` (orchestrator).
- **Approach:** Codex assembles from `synthetic_experiments.jsonl`, `synthetic-comparison-v2.json`,
  and `docs/migration/2026-08-component-parity-map.md`; verdict block per KD2; the exact firm-exam
  command and expected inputs in an "Owner-run steps" section. Leak scan the report.
- **Verification:** AE4 or AE5.

### U6. Close-out

- **Goal:** Nothing on the board asks about finished work; the learning is durable.
- **Requirements:** R8.
- **Approach:** Retire the 2026-09-05 handoff (both copies) naming this plan and the report;
  `ce-compound` the "comparison measured a superseded candidate" trap; update on-deck.

## Verification Contract

| Gate | Applies to | Check | Done signal |
|---|---|---|---|
| Fingerprint | U1, U2 | runner accepts the tree | canary shard receipt exists |
| Byte-identity | U4 | automatic vs `--finalize-only` report | identical sha256 |
| Leak gate | U4, U5 | manifest-bound scan of report and campaign report | zero collisions |
| Review gate | U4, U5 | Codex review receipt per `CLAUDE.md` | verdict `merge` |
| Board freshness | U6 | on-deck and asks reflect the verdict | no open ask on finished work |

## Definition of Done

`synthetic-comparison-v2.json` and `campaign-report-v1.md` are on `main`, leak-clean and
byte-verified; the adoption verdict per consumer is recorded in the report and on the deferred-round
reminder; the owner has the firm-exam command; the 2026-09-05 handoff is retired.
