---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-29T15:22:37Z"
title: "U10 v8 reboot-safe finalization-failure handoff"
summary: "U10 v8 completed all 90 shards, failed during finalization, and is preserved for an explicit post-reboot RESUME."
keywords: ["u10", "comparison-pilot", "checkpoint", "finalization-failure", "reboot", "ce-work"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "After the user explicitly says RESUME, diagnose the preserved U10 v8 finalization failure without rerunning or interpreting the comparison results first."
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb820c0cee6aa2721b872790c09ad0c5"
branch: "docs/u10-v8-finalization-failure-handoff-2026-08-29"
head: "eae82ddca9a71013e79278e80897fcf25483471a"
worktree_path: "/home/damienriehl/Coding Projects/folio-resolve/.worktrees/docs/recent-plan-audit"
---

# U10 v8 reboot-safe finalization-failure handoff

## Paused boundary

The user directed the active session to monitor the existing U10 v8 process through all of its
shards and finalization, then pause for reboot. The process completed every configured shard but
exited unsuccessfully during finalization. The user also directed that a failed run be preserved,
not repaired or restarted, and that no other plan work begin.

The session is therefore paused. Do not diagnose, repair, resume, rerun, interpret comparison
results, or begin U9, U12, U13, or any other `ce-plan` work until the user explicitly says
`RESUME`.

## Authoritative preserved state

- The runner's authoritative configuration and progress log established an actual denominator of
  **90 shards**, superseding the earlier 60-shard assumption recorded in this handoff.
- **90/90** top-level shard completion receipts, **90/90** shard reports, and **90/90** shard item
  files are present in `eval/data/u10-comparison/pilot-checkpoint-v8/items` in the execution
  worktree.
- Finalization created nonempty `final-items.jsonl` and the `final-stages/` directory in
  `eval/data/u10-comparison/pilot-checkpoint-v8`.
- The original runner then exited with code **1**. Its terminal exception class was
  `folio_eval.comparison.ComparisonError`.
- Finalization did **not** publish either required success artifact:
  `eval/reports/synthetic-comparison-v1.json` is absent, and
  `eval/data/u10-comparison/pilot-checkpoint-v8/final-complete.json` is absent.
- This is a preserved failure boundary, not a successful comparison result. No comparison metrics
  or protected data were inspected or interpreted.

The ignored checkpoint is durable on this host and survives reboot. The machine-local logs at
`/tmp/u10-v8-init.h1ijoh/` are diagnostic only and may not survive reboot; they are not required to
establish the durable 90/90 shard state or the absence of final success artifacts.

## Pinned machine-local worktrees

Do not update, checkout, merge, or otherwise mutate these inputs before post-`RESUME` diagnosis:

- Candidate/execution worktree:
  `/home/damienriehl/Coding Projects/folio-resolve/.worktrees/fix/u10-public-metadata-exemption`
  at detached commit `db1cf4c99be859de15bd40d7497a445c3b48c4c8`.
- Mapper worktree:
  `/home/damienriehl/Coding Projects/folio-resolve/.worktrees/external/folio-mapper-u10-canonical-iris/.worktrees/external/folio-mapper-deterministic-cpu`
  at commit `626412bbec571308a0ef9293923c39de54d65c5f`.
- Enrich worktree:
  `/home/damienriehl/Coding Projects/folio-resolve/.worktrees/external/folio-enrich-u10-runner`
  at commit `bb576ac0830308091623018aa1546559ac4fa1a9`.

The local leak-check salt file is machine-local at
`/home/damienriehl/Coding Projects/folio-resolve/eval/data/leakcheck-salt`. Never print its contents
or digest. Never print or copy synthetic item IDs, protected surfaces or passages, salts, matched
digests, raw report rows, or directory-name fingerprints.

## Completed repair and prior validation

- PR [#20](https://github.com/damienriehl/folio-resolve/pull/20) merged the structured-artifact
  leak-scan repair as `db1cf4c99be859de15bd40d7497a445c3b48c4c8`.
- The exact previously failing shard replay passed end to end. The merged change passed the full
  suite (`1156 passed, 2 skipped`), 72 focused tests, Ruff, changed-module mypy, and diff checks.
- v6 and v7 remain preserved failed evidence and must not be resumed.
- The broader plan status, residual queue, and owner Decision Sheet are in
  `docs/handoffs/2026-08-20-recent-plan-completion-audit.md`; PR
  [#21](https://github.com/damienriehl/folio-resolve/pull/21) records the v8 recovery decision.

## Post-RESUME orientation

After explicit user authorization, first revalidate the three pinned worktrees and structural
checkpoint counts. Diagnose the finalization failure from the preserved checkpoint and code path
without launching a runner. The missing canonical report and missing final receipt must remain
fail-closed evidence until a separately authorized repair plan is chosen.

Do not infer authorization to rerun from this handoff. Any repaired or fresh run must be a later,
explicitly authorized action and must preserve the current checkpoint as evidence.

## Continuity warnings

- The checkpoint is durable only on this host unless separately backed up; it is intentionally
  ignored and is not pushed to GitHub.
- This handoff is durable in Git after its focused branch is pushed and merged, but it contains no
  checkpoint payloads, secrets, protected data, or comparison metrics.
- A reboot may remove the `/tmp` logs, but it does not remove the repository-local checkpoint.
