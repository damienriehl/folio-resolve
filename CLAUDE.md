# CLAUDE.md — folio-resolve-specific rulings; the inherited cockpit root `CLAUDE.md` carries fleet-wide policy

## Review gate — Damien's call, 2026-09-06

A recorded Codex reviewer pass is the `ce-code-review` receipt for this repository.
The review prompt and returned verdict must both be retained as artifacts.
No Claude reviewer subagents are spawned for the shipping tail.

A valid receipt line is:

`ce-code-review receipt: Codex review <artifact path or PR comment>, verdict <merge|fix-first|...>`

PR #35 received two Codex review passes: first `fix-first`, then `merge` with one P3
that was applied. Artifact locations not recorded.

PR #42 (`feat/u13-campaign-report`) merged 2026-09-07 as `7cff900`; commits `243d60a`,
`f3fef82`, `7072e45`, `a61988a`, `c280f58`. Four Codex review rounds returned `fix-first`,
`fix-first`, `fix-first`, then `merge`. ce-code-review receipt: Codex review
`review-u13-report-round4.md`, verdict `merge`. All four prompts, all four verdicts, and five
worker reports are retained off-tree on the review host, outside the repository. The review text
itself was not published because it scans with non-zero collisions against the firm-surface
manifest and this repository is public. Future receipts here should record verdict and artifact
name without reproducing review prose.

## Eval lane — read before touching

Read `docs/solutions/2026-09-04-u9-iteration-traps.md` before touching the eval lane.
Then read the U6/KTD12 stop-gate ruling recorded in
`docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md`, under
KTD12 **Stop-rule defaults, statistically guarded** and U9 **Iteration loop and guarded
stop rule**.

The diminishing-returns counter is a firm-lane instrument. On the synthetic slice, a
`park` experiment record is the stop signal for that lever, and the campaign terminates
through U13's report and adoption verdict. No stop-gate code change follows from this ruling.
