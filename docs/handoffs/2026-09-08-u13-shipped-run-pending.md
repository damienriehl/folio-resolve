---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-08T00:40:59Z"
title: "U13 shipped; comparison v2 run pending"
summary: "The U13 campaign-report unit is merged, while its long comparison v2 run remains live and blocks verification and close-out."
keywords: ["u10", "comparison-v2", "u13", "campaign-report", "watchdog", "close-out"]
cwd: "."
resume_focus: "Wait for the watchdog-owned comparison v2 run, verify its artifacts directly, then finish U4, U5, and U6 under KD1-KD6."
repository: "folio-resolve"
repo_root_sha: "4fb82423cb820c0cee6aa2721b872790c09ad0c5"
branch: "docs/2026-09-08-successor-handoff"
head: "c07d0201c9e4a1a6d54fed8dce109d36fe7f3657"
---

# U13 shipped; comparison v2 run pending

## Why this exists

The U13 report unit is shipped and merged. A long comparison run is still in flight, and every
downstream step waits on it. This handoff is durability insurance: the run outlives any single
session.

## What is live

Worker `folio-resolve-u10-v2-fullrun-20260907c` supervises a detached comparison run resuming
checkpoint v9. At `2026-09-08T00:37Z` it was at shard 29/90. The observed rate was about 12.5
minutes per shard, leaving roughly 13 hours from that timestamp.

The run writes to a git-ignored path; its committed copy is produced afterward. The watchdog owns
monitoring. Do not poll the worker or relaunch the run.

## What shipped

- PR #42, merged as `7cff900`: the campaign-report generator, its tests, and
  `eval/reports/campaign-report-v1.md`. Until a v2 comparison is supplied, the report correctly
  renders that verdict block as pending. The unit passed four Codex review rounds.
- PR #43, merged as `c07d020`: the #42 review receipt in `CLAUDE.md` and
  `docs/solutions/2026-09-07-review-gate-catches-self-inflicted-regressions.md`. The receipt took
  two review rounds.

Before touching this lane, read these entries:

- `docs/solutions/2026-09-07-review-gate-catches-self-inflicted-regressions.md` — why every repair
  needs both-direction regressions and a fresh publication scan.
- `docs/solutions/2026-09-07-u10-comparison-rerun-traps.md` — ignored output placement, required
  environment preparation, and matching a report to the candidate it measured.
- `docs/solutions/2026-09-04-u9-iteration-traps.md` — pristine checkpoint trees, metric-family
  baselines, and gate-before-dedup ordering.

## What remains

Follow the plan's U4, U5, and U6 sections in order. U4 must verify, leak-scan, and commit the v2
comparison artifact. U5 must regenerate and commit the v2-backed campaign report with its report
leak scan, AE4 or AE5 verification, and Codex review receipt; `briefs/on-deck.json` remains
orchestrator-owned. U6 must close the lane.

U5 is partially shipped: PR #42 merged its generator and a report whose v2 verdict block is
deliberately pending. Completing U5 requires supplying the actual v2 comparison, recording the
KD2-conditioned verdicts, and keeping the owner-run slots explicit before the verification and
review gates above. That adoption-gate verdict is not the later owner-run firm exam or adoption
decision. Those two steps follow and belong to the owner alone; an agent must not run them.

The governing plan is
`docs/plans/2026-09-06-001-eval-u10-comparison-v2-and-u13-closeout-plan.md`. Read KD1-KD6 before
acting. KD2 pre-authorizes a v2 loss to close U13 as no-adopt without another ask; a pass or an
in-band hold stops for a fresh decision. Do not reconstruct the remaining decisions from this
handoff; use the plan.

## Verify the run when it lands

Do not trust the worker's report. Check the process exit code, the `final-complete.json` marker,
all 90 shard receipts, clean `git status --porcelain`, and a finalize-only replay that is
byte-identical to automatic finalization. Then regenerate `eval/reports/campaign-report-v1.md`
with `eval/build_campaign_report.py --comparison-v2`.

The generator now requires `--surface-manifest` and `--salt-file`; there is no bypass. The salt is
owner-local and absent inside worktrees, so pass its location as an argument without copying it
into the worktree.

## Traps already paid for

- The runner fingerprints the launching shell's mapped files, so locale affects checkpoint reuse.
  Before every launch, set `LANG`, `LC_ALL`, and `LC_CTYPE` to `en_US.UTF-8`.
- Running pytest in a sibling worktree is safe while the run is live. Do not run it in either of
  the two worktrees used by the comparison.
- The worker wrapper overwrites a self-reported `blocked` state with `completed` on clean exit.
  Read `state` and `current_step` together.
- A Codex job outlives its watcher. If a watcher dies, check job status before re-dispatching or
  two workers can write the same worktree.
- Codex worker sandboxes cannot write git metadata. The orchestrator must own commits.

The three solutions entries above support the comparison-rerun, iteration, and review-gate
learnings. The locale/mapped-file fingerprint, wrapper state rewrite, watcher/job lifetime, and
Codex sandbox git-metadata items are observations recorded in this handoff.

## Retire this handoff when

The comparison v2 outcome has been acted on under KD2, U4, U5, and U6 are complete, and any new
learning has landed in `docs/solutions/`.
