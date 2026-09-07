---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-07T19:15:12Z"
title: "U10 comparison v2 running (~16h); U13 report committed awaiting its review receipt"
summary: "Two units of the comparison-v2 plan are live at once — a long scoring run resuming checkpoint v9 and a committed-but-unmerged campaign-report branch — with the owner's closing ruling already pre-authorizing the no-adopt path."
keywords: ["u10", "comparison-v2", "u13", "campaign-report", "fingerprint-locale", "watchdog", "orchestrator-change"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Land the U13 report branch once its Codex review receipt exists, then act on the comparison-v2 run's outcome per the owner's pre-authorization."
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb820c0cee6aa2721b872790c09ad0c5"
branch: "main"
head: "f55dc59a268145c6c3a987ef550e8050042c4da0"
---

# U10 comparison v2 running (~16h); U13 report committed awaiting its review receipt

## Why this exists

The orchestrator seat moved from Fable 5.1 to Opus 5 mid-session at the owner's direction when
Fable's weekly usage reached 99%. Unlike the handoff this session resumed from, **work is in
flight**: a ~16-hour scoring run and a committed-but-unmerged branch. This is durability
insurance — the run outlives any single session.

## The owner's decisions today (his, not this session's)

Answered inline 2026-09-06, receipts written to the cockpit before any action:

- **Review gate:** a recorded Codex reviewer pass (prompt + verdict, both retained) *is* the
  `ce-code-review` receipt for this repo. No Claude reviewer subagents. Now in the repo's own
  `CLAUDE.md`.
- **Stop gate:** the U6/KTD12 diminishing-returns rule is a firm-lane instrument. On the synthetic
  slice a `park` ledger record is the stop signal and U13 terminates the campaign. No code change.
  Recorded as dated amendments under KTD12 and U9 in the campaign plan.
- **Next lane:** assemble U13 up to the owner-run exam. He added: *"make my answering questions as
  easy and as fast as possible."*
- **Cockpit freeze:** lifted. The long-deferred D1–D5 answers receipt was written the same day.
- **R17 closing path (the load-bearing one):** rerun the comparison as v2 on the kept candidate;
  **if v2 is still a loss, close U13 as no-adopt without asking again**; a pass or an in-band hold
  stops for a fresh ask. He answered "do whichever follows best practices," which selected the
  recommended option.

## What is live right now

Both are Codex workers launched through `agents/worker-wrapper.sh`, so the watchdog owns
monitoring and reaches the session through `agents/events.log` and ntfy.

| Worker | State at capture | What it is |
|---|---|---|
| `folio-resolve-u10-v2-fullrun-20260907c` | `running`, shard 5/90, 19:13Z | U3: supervises the detached comparison run, heartbeating `shard N/90` into its status file |
| `folio-resolve-u13-campaign-report-20260907b` | `review_ready` (integrated) | U5: the campaign-report generator, already verified and committed |

The run resumes checkpoint **v9** (do not discard it — shard 1 was already complete before the
resume). Expect ~11 minutes per shard, so roughly 16 hours from its 18:40Z start. It writes to a
git-ignored path; the committed copy is produced afterwards.

## State by maturity

**Complete and merged to `main` (`f55dc59`):**

- PR #39 — the repo's first `CLAUDE.md` (review-gate ruling), the two campaign-plan amendments, and
  the plan this session is executing:
  `docs/plans/2026-09-06-001-eval-u10-comparison-v2-and-u13-closeout-plan.md`. Read its Key
  Decisions KD1–KD6 before touching either unit; KD2 carries the pre-authorization above.
- PR #40 — `docs/solutions/2026-09-07-u10-comparison-rerun-traps.md`. **Read this before any
  relaunch.** Its three traps are the ones already paid for.

**Committed, not merged** — branch `feat/u13-campaign-report`, worktree
`.worktrees/feat/u13-campaign-report`, commit `243d60a`:

- `eval/folio_eval/campaign_report.py`, `eval/build_campaign_report.py`,
  `tests/test_eval_campaign_report.py`, and the generated `eval/reports/campaign-report-v1.md`.
- The report renders the v2 verdict block as `pending` until a v2 comparison is supplied, so it is
  correct and committable *now* and regenerates with `--comparison-v2` when the run lands.
- **What remains inside it:** a Codex review receipt (required by the owner's gate), then push and
  PR. A review was dispatched and its first attempt stalled; `codex-run.sh` re-dispatched once.
  If no receipt exists, re-dispatch rather than merging without one.

**Not started:** U4 (verify + commit the v2 report) and U6 (close-out) — both depend on the run.

## Verification actually performed by the orchestrator

Every number below was re-run by the orchestrator, not relayed from a worker:

- Full suite `1247 passed, 5 skipped`; `mypy src` and `mypy eval` clean; `ruff check` clean;
  `git diff --check` clean.
- Leak scan `collisions=0` on the generated report and all three new source files, run through the
  repo's manifest-bound checker.
- Regeneration byte-compare: identical **except** the provenance footer, which embeds the `--out`
  path. That is a real (minor) determinism wart and was put to the reviewer explicitly; if the
  receipt flags it, excluding the out path from the footer is the fix.
- The docs unit's cited line numbers and its `.gitignore` / `pyproject.toml` claims were checked
  against the files before that commit.

## The trap that cost this session the most

**The runner fingerprints the launching process's mapped files, so the shell's locale is part of a
checkpoint's identity.** A canary launched from one shell and a full run launched from a Codex
worker's shell disagreed on `LC_ALL` / `LC_CTYPE`, which mapped an extra locale image and produced
`pilot checkpoint fingerprint does not match this run` at startup. Every launcher must pin
`LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 LC_CTYPE=en_US.UTF-8`.

The same diagnosis established what is **safe** while a run is live: creating sibling worktrees,
running `uv sync` in a sibling worktree, and running pytest in a sibling worktree do **not** trip
the per-shard boundary checks. Running pytest inside the execution or incumbent worktrees would,
because it creates fingerprinted ignored bytecode. Two earlier launch failures — a non-ignored
`--out` path and a worktree venv missing the optional `folio` extra — are written up in the
solutions entry.

## Two defects outside this repo, both awaiting the owner

- **`agents/worker-wrapper.sh` clobbers `blocked`.** On clean process exit the wrapper overwrites
  the worker's self-reported state with `completed`, preserving only `review_ready`. A worker that
  correctly reports `blocked` is rewritten to `completed`, so the watchdog raises no event and the
  failure is invisible. This happened three times this session and cost roughly a day the first
  time. Read `state` **and** `current_step` together until it is fixed. The owner has not yet said
  whether to dispatch a cockpit-root fix.
- **The board publish pipeline is down.** `BOARD.json` last generated 2026-09-03; both
  `cockpit-answers-pull.timer` and `coding-projects-sync.timer` are `masked-runtime`, inactive
  since 2026-08-31, with the sync log ending in repeated "fast publish orchestration error
  (exit 78)". Lifting the freeze sentinel was necessary but not sufficient. Consequence: this
  session's two Decision Sheets never reached the owner's phone, though every answer is durably
  recorded in `briefs/qa/`. Cockpit-scope; not fixed from this repo.

## Judgment calls this session made (not the owner's)

- Ran `ce-work`'s discipline — bounded unit packets, proof-first evidence, orchestrator-owned
  verification and canonical commits — over the cockpit's dispatch mechanism, rather than the
  skill's own bundled cross-model controller, because the cockpit's Codex worker policy is the
  higher authority.
- Committed the U13 report while the v2 block still reads `pending`, on the reasoning that a report
  correct at its inputs is worth landing and regenerating.
- Treated the stale `folio-resolve` on-deck card as safe to rewrite, and added an In Motion card
  for the run.

## Plausible continuation

One path, in order: confirm the U13 review receipt exists and land that branch; when the run's
worker reaches a terminal state, verify the artifacts directly (exit code, `final-complete.json`,
90 shard receipts, clean porcelain, and a `--finalize-only` byte-compare) rather than trusting the
report; then follow KD2 — a loss means regenerating the campaign report with `--comparison-v2`,
committing it with the no-adopt verdicts, updating the deferred-round reminder, and telling the
owner the verdict once. A pass or in-band hold means stopping and filing a fresh ask.

Do not relaunch the comparison from scratch, do not discard checkpoint v9, and do not run the firm
exam — that step and the adoption decision are the owner's.
