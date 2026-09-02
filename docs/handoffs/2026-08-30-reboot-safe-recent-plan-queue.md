---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-30T16:13:17.874Z"
title: "Reboot-safe recent-plan queue after U12 merge"
summary: "Records the completed U10/U12 evidence, the interrupted U9 attempt, exact reboot recovery boundaries, autonomous queue, and owner Decision Sheet."
keywords: ["reboot", "recent-plan-audit", "u9", "u10", "u12", "decision-sheet"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Revalidate process and repository state, resume U9 attempt 0001 without duplication, then continue only the evidence-eligible campaign queue."
repository: "damienriehl/folio-resolve"
branch: "docs/reboot-handoff-2026-08-30"
worktree_path: "/home/damienriehl/Coding Projects/folio-resolve/.worktrees/docs/reboot-handoff-2026-08-30"
---

# Reboot-safe recent-plan queue

## Objective and authority

The user asked that every recent plan task be either finished or durably queued, with autonomous
work completed where possible and true judgment calls saved for a Decision Sheet. The user later
authorized autonomous commits, pushes, and merges, then explicitly requested a pause after this
handoff so the machine can reboot.

This document records observed state. On resume, verify the live repository and process state
before acting. Do not treat a stale process claim as authority to start a duplicate run.

## Plan audit status

The authoritative plan audit remains
`docs/handoffs/2026-08-20-recent-plan-completion-audit.md`. It covers the original 21-day audit
window and the active carryover plan. Current status advances that audit as follows:

| Plan or unit | Current state | Durable evidence or remaining gate |
|---|---|---|
| 2026-08-06 Post-Hardening Integration | Complete | Do not reopen completed release work. |
| 2026-07-27 F1 Improvement Loop | Implemented and superseded | Its live measurement gates continue only through the 2026-08-16 campaign. |
| U1-U6 and U8 | Complete | Existing merged implementation, gold/corpus, leak, and baseline artifacts remain authoritative. |
| U7 | Code complete; owner-gated | Consensus audit, confidence calibration, and human ratification remain owner actions. |
| U10 | Complete | The finalized comparison is merged through PR #25. It is a clear candidate loss against both deterministic incumbents; adoption remains blocked. |
| U12 | Complete | Runtime attribution and controlled replay evidence merged through PR #26 at merge commit `94baca4`. See `docs/migration/2026-08-component-parity-map.md`. |
| U9 | In progress | Attempt 0001 implementation is committed locally; the authoritative checkpoint-bound scoring run was active at reboot pause. |
| U11 | Owner-gated | Shipped-configuration lanes require owner-held provider keys or explicit skipped markers. |
| U13 | Blocked | Requires the U9 trajectory, a passing comparison gate, and owner-run final firm evidence. |

## U9 attempt 0001 at the pause boundary

Machine-local worktree:
`/home/damienriehl/Coding Projects/folio-resolve/.worktrees/finalize/u10-v8-repair`

- Branch: `feat/u9-u12-loss-recovery`
- Exact scoring commit: `4e0407e4a8f4feef8e7694078aa3fd2e9094e4c3`
- Tracked tree: clean at capture
- Attempt: `attempt-0001`
- Lever: local definition context as an equal-primary-score ranking tie-break; retrieval and
  calibration semantics remain unchanged
- Runner: `eval/data/u9-attempt-0001-runner.sh`
- Checkpoint: `eval/data/u9-attempt-0001-checkpoint`
- Logs: `eval/data/u9-attempt-0001-logs`
- Planned report: `eval/reports/synthetic-attempt-0001-definition-context-v1.json`
- Runtime at capture: tmux session `folio-u9-attempt-0001`, two active scorers, about 47 minutes
  elapsed, about 1.4 GiB aggregate RSS, and approximately two CPUs fully utilized
- Durable completion receipts at capture: **0**
- Final report at capture: absent

A reboot stops tmux and the two current scorer processes. No completed receipt is lost because the
count is zero, but the two in-progress shard computations must restart. The checkpoint directory,
runner, logs, worktree, branch, implementation commit, and experiment-pending record are durable.

### Exact recovery boundary

1. Confirm reboot ended the old tmux session and scorer children. Never start a duplicate.
2. Confirm the U9 worktree is still on exact commit `4e0407e4a8f4feef8e7694078aa3fd2e9094e4c3`
   with a clean tracked tree.
3. Validate the existing runner with `bash -n`; do not edit the runner, implementation, branch, or
   scoring worktree while shards are active.
4. Start the existing runner in tmux session `folio-u9-attempt-0001`. It owns eight shards with
   parallelism two and resumes only from durable receipts.
5. Monitor aggregate process health and receipt count without printing logs, item identifiers,
   passages, surfaces, salt material, manifests, raw report rows, or fingerprints.
6. Continue until all 255 item receipts and finalization are durable, or preserve and report the
   first fail-closed state. Do not start a fresh attempt over a failed checkpoint.
7. After success, verify the report and leak gate using aggregate-only output, finish the experiment
   receipt, run the required code review, and ship the U9 implementation only if the measured
   decision supports it.

## Completed U12 shipping evidence

PR [#26](https://github.com/damienriehl/folio-resolve/pull/26) merged on 2026-08-30.

- Internal structured review found two documentation defects; both were corrected.
- GitHub review found that the full-corpus and pilot baselines were not distinguished clearly.
  Commit `b76a3fa` now documents two cohort-specific replays and limits the causal claim to
  within-cohort evidence.
- Aggregate F1 arithmetic, added-line protected-surface collision scan, and `git diff --check`
  passed.
- The final head was mergeable and CLEAN with no actionable thread, CI failure, branch-currency
  item, or human decision. It remained quiet for more than 16 minutes before merge.
- The automated reviewer did not post a second completion marker for the post-fix head. Its prior
  review round took under three minutes; this was reported as a bounded cautious-ready condition,
  not as reviewer approval.

## Autonomous queue after reboot

1. Resume and finish U9 attempt 0001 from the exact state above.
2. Record the attempt decision and verify whether it improves the synthetic development metric
   without worsening the no-match control.
3. Review, commit, push, and merge any evidence-supported U9 change.
4. Continue the guarded U9 loop only within the plan's attempt and stop rules; do not widen
   retrieval or bypass the owner interim firm checkpoint.
5. Assemble U13 only up to its remaining owner gates after U9 evidence is complete.
6. Update this queue or the authoritative audit with aggregate-only status.
7. Reassess the local folio-python cache containment only after a released folio-python version
   contains the merged upstream policy. As of this handoff, the latest release is still v0.4.0 and
   predates that upstream merge, so the local containment remains required.

## Owner actions and Decision Sheet

Owner-run actions that remain:

- U7 consensus audit, confidence-floor calibration, and ratification
- U11 provider-key lanes or explicit skipped markers
- U9 interim aggregate-only firm checkpoint when the guarded logic requests it
- U13 final firm exam and adoption decision
- Protected-original disposal only after accepted close-out

The five provisional decisions remain recorded in the authoritative audit. Recommended defaults
already used by the implementation are:

```text
D1 = Yes
D2 = Calibrate
D3 = Yes
D4 = Yes
D5 = Public metadata exclusions
Notes (optional):
```

These are saved for owner confirmation; they are not blockers to resuming the already-approved
attempt. Do not invent answers to later evidence-bound adoption decisions.

## Preserved unrelated state

The primary checkout remains on `fix/synthetic-baseline-runtime`. Its unrelated untracked files
and local report were not staged, committed, or modified by this handoff. The U9 scorer worktree,
other historical worktrees, and ignored checkpoint/log state were likewise preserved.

## Pause instruction

After this handoff is committed and pushed, stop all further agent work. Do not resume U9, start a
new scorer, interpret results, or advance another plan unit until the user explicitly asks to
resume after reboot.

