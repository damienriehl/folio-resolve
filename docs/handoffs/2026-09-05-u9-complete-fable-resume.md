---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-05T16:38:22Z"
title: "U9 attempts 0003-0004 complete; orchestrator returning to Fable 5.1"
summary: "The U9 iteration lane is closed out and fully merged with nothing in flight; this hands a clean tree to a Fable orchestrator and lists what is genuinely still open."
keywords: ["u9", "experiment-ledger", "eligible-anchor", "scoring-semantics", "handoff", "orchestrator-change"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Nothing is in flight. Pick up owner-gated U9/U13 work, or start fresh; do not re-run attempts 0003 or 0004."
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb820c0cee6aa2721b872790c09ad0c5"
branch: "main"
head: "91363416a77cb6875b0a6de1e920d72d38c8ee68"
---

# U9 attempts 0003-0004 complete; orchestrator returning to Fable 5.1

## Why this exists

The owner's weekly usage reset and he is moving the orchestrator seat back to Fable 5.1.
This session ran on Fable, hit the Fable limit mid-run, continued on Opus 5 at the owner's
direction, and finished the work. Nothing is in flight and no process is running, so the
next session starts from a clean tree rather than mid-experiment. That is the main thing
to know: unlike the handoff this session resumed from, there is no unfinished measurement
to pick up.

## State: complete and merged

`main` is at the HEAD above. Four PRs merged this session:

| PR | What it did |
|---|---|
| #34 | Landed the predecessor handoff (so #37 could retire it through the documented path) |
| #35 | The substantive work — see below |
| #36 | `docs/solutions/2026-09-04-u9-iteration-traps.md`, three durable traps |
| #37 | Retired the 2026-09-03 resume handoff, naming what absorbed it |

The experiment ledger `eval/reports/synthetic_experiments.jsonl` now holds four lines:
attempt-0001 `keep`, attempt-0002 `park`, attempt-0003 `park`, attempt-0004 `park`, each
pinned to the scorer commit that produced its report. Scoring semantics version is 4.

## What PR #35 actually settled

Three code changes, in order, plus two ledger records:

1. `_casefolded_span` in `src/folio_resolve/scoring.py` — `definition_context_score` was
   finding the anchor in a casefolded string and slicing the *original* with that offset.
   Casefolding is not length-preserving, so an expanding character before the anchor
   shifted the definition window.
2. `DocumentAdapter.adapt` in `eval/folio_eval/synthetic_score.py` — the context score was
   computed after same-IRI dedup, so the surviving anchor was chosen without regard to
   context strength. Semantics version 3, measured as attempt-0003.
3. Eligible-anchor selection, semantics version 4, measured as attempt-0004. Change (2)
   exposed a latent defect: selection ran before the gate chain, but the alias blocklist,
   place gate and short-label gate all key on `surface_term`, the field the dedup discards.
   A strong-context blocked anchor could win selection and delete the concept entirely.
   `adapt` now walks anchors in key order and stops at the first *eligible* one, with the
   gate chain extracted unchanged into `_gate_candidate`.

**The result worth carrying forward.** Attempt-0004 measured that defect exactly: the
`blocklist` suppression counter fell 22 to 0 and `survivor_count` rose by exactly 22, with
every other counter unchanged. So 22 concepts corpus-wide were being deleted despite having
an eligible sibling anchor, and all 22 are recovered — while every scored aggregate stayed
identical (micro-F1 0.017513, predictions 1350) because those concepts rank below the cut or
are absent from gold. A real correctness bug was invisible to the headline metric. That is
lesson 3 of the solutions entry and the most transferable thing this session produced.

## Decisions and who made them

- **Owner-directed, carried from the predecessor handoff and still standing:** a winning U9
  iteration merges to `main` only — no release tag, no consumer pin PRs — until the adoption
  verdict. Both attempts merged under that rule.
- **Owner-settled rule, applied by this session:** keep if micro-F1 >= the kept state's
  0.017513 with the no-match FP rate not worse; park if identical; revert if lower. Both
  attempts were identical to the kept attempt-0001 on every scored aggregate, so both are
  `park`. The protocol's own semantics agree — `finish_attempt` parks a flat result for
  human judgment. `--decision auto` is not usable on the synthetic slice; it requires tune
  scores.
- **This session's call, not the owner's:** `park` still merges the code, on the attempt-0002
  precedent. These are correctness fixes; park records that the lever bought nothing on the
  metric, not that the change is unwanted.
- **This session's call:** the P3 from the second review (a missing short-label fallback
  regression) was applied rather than deferred, scoped to tests only so the recorded
  attempt-0004 report stayed valid.

## Wrong paths already taken — do not repeat

All three are written up in `docs/solutions/2026-09-04-u9-iteration-traps.md`; read it before
touching the eval lane. The short version:

- **Comparing a stacked attempt to the wrong neighbour.** This session did it twice and had
  to correct the record both times, including once in a published PR description. Levers here
  are orthogonal across metric families: the provider fix moves candidate volume but no score,
  the tie-break moves scores but no volume. The valid isolation is per metric family.
- **Launching shards into a tree the scorer rejects.** `build_checkpoint_fingerprint` refuses
  any non-empty `git status --porcelain`, untracked files included. An untracked `.codex-out/`
  killed all eight shards at startup. A launcher pre-check that filters anything the scorer
  does not is worse than no check.
- **`pkill -f` matching the orchestrator's own command line.** Hit three times. Use a bracket
  in the pattern, or kill by PID.

## Verification performed

Both attempts completed all 255 checkpoint items with all eight shards exiting 0, and both
committed reports are clean-tree `--finalize-only` replays verified byte-identical to their
automatic finalizations. Every worker claim was re-run by the orchestrator rather than
relayed: full suite (1238 passed, 5 skipped at the final commit), Ruff, mypy on `src` and
`eval`, `git diff --check`, plus leaf-by-leaf leak scans of both new ledger lines and a check
that prior lines stayed byte-unchanged. The new regression tests were verified to fail
against the pre-fix code, not merely pass against the new.

Two Codex review passes ran on PR #35. The first returned `fix-first` on the eligible-anchor
defect. The second verified the fix across all three gates plus the trace, counter,
accounting, determinism and cost invariants, and returned `merge` with one P3, now applied.

## Open — owner-gated, unchanged by this session

U7 consensus sitting; U11 provider-key lanes; the U9 interim aggregate-only firm checkpoint;
U13 final exam and the adoption verdict; protected-originals disposal; and the cockpit
answers receipt plus Decision Sheet for D1-D5, still deferred behind the cockpit freeze
sentinel, which is still present.

## Two questions this session raised and the owner has not answered

1. **The `ce-work` review gate versus the Codex-only worker policy.** `ce-work`'s shipping
   tail requires a `ce-code-review` receipt, but that skill in `mode:agent` always runs its
   full multi-agent path with Claude-side reviewer subagents, which contradicts the standing
   rule that review personas are Codex work. This session followed the owner's rule, used a
   Codex reviewer, and reported the receipt honestly rather than using one of the gate's skip
   phrases, none of which describes that situation. A one-line policy answer settles it.
2. **The stop gate's baseline.** `start_attempt` records the caller-supplied baseline and
   `stop_status` consumes that delta, so an attempt with zero incremental gain over the
   current *kept* state still registers as a +0.0105 improvement against `baseline-v1` and
   cannot advance the diminishing-returns counter. Every synthetic record also carries a
   zero-unit bootstrap interval, which independently blocks the counter. So the U6 stop gate
   cannot currently fire on this lane at all. That is a protocol question, not a bug to fix
   quietly.

## Fragile and machine-local

Evidence-bearing worktrees under `.worktrees/` must be kept: `finalize/u10-v8-repair`
(attempt-0001 checkpoint), `eval/u9-attempt-0002`, `feat/u9-attempt-0003-prep` (holds both
the attempt-0003 and attempt-0004 checkpoints), `fix/u8-resumable-scoring`,
`fix/u10-public-metadata-exemption`, `feat/u10-resumable-pilot*`, `fix/u10-venv-interpreter`,
and `eval/u8-baseline-v1-report`. These are machine-local and not recoverable from the remote.
A private unredacted companion to this handoff holds the launcher and scratch paths.

## Plausible next steps

Nothing is required. If work resumes on this lane, the natural sequence is: answer the two
questions above, then take up whichever owner-gated item is wanted first. A further U9
attempt is not indicated — three consecutive parks with the metric immovable suggests the
remaining levers are not in the ranking path, which is what U13 exists to settle.

Do not re-run attempts 0003 or 0004; both are recorded with byte-verified reports.
