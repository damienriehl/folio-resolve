---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-03T10:48:48Z"
title: "Autonomous resume 2026-09-02/03: UAT layer, provider fix, U9 attempts 0001-0003"
summary: "Seven PRs merged in one autonomous session; U9 attempt-0003 is the only in-flight item and this records exactly how to finish it."
keywords: ["u9", "persona-uat", "provider-rescoring", "experiment-ledger", "leak-gate", "handoff"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Finish U9 attempt-0003 (finalize-only replay, record, PR, review, merge to main only), then write the owner report; do not start U13 or any owner-run lane."
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb820c0cee6aa2721b872790c09ad0c5"
branch: "main"
head: "1d7940142a5a516ce4d751b0a0bac33ee26aad9e"
---

# Autonomous resume 2026-09-02/03

## Objective and owner intent

The owner asked (2026-09-02, while traveling) to resume every interrupted plan and handoff, add
persona-driven user acceptance testing, and push everything to production autonomously, asking only
taste questions. The owner answered four questions inline the same day:

- A winning U9 iteration is **merged to main only**; no release tag and no consumer pin PRs until
  the adoption verdict (owner-directed).
- Prune merged, evidence-free worktrees and branches; keep every checkpoint-bearing worktree and the
  external consumer clones (owner-directed).
- The UAT work ships as a **committed suite plus report** (owner-directed).
- **D1–D5 ratified** as recommended; the cockpit answers receipt and Decision Sheet are deferred
  until the owner lifts the cockpit freeze (owner-directed guardrail for that day).

## Work completed (all on main, each reviewed by a Codex worker and re-verified by the orchestrator)

| PR | What landed | Where to look |
|---|---|---|
| #27, #28 | Reboot handoff merged; audit updated; D1–D5 receipt; seven consumed handoffs retired; worktree hygiene | `docs/handoffs/2026-08-20-recent-plan-completion-audit.md` "Execution update — 2026-09-02" |
| #29 | Persona UAT: 8 personas, 24 stories, `tests/uat` suite, generated report; two README drift fixes | `docs/plans/2026-09-02-1110-feat-persona-uat-plan.md`; `docs/uat/`; `tests/uat/build_report.py` |
| #30 | **Library defect**: `FolioPythonProvider.search_by_label` passed folio-python fuzzy scores through as relevance (Nigeria at 90.0 for "Findings of Fact"); both providers now score through one helper | `src/folio_resolve/ontology.py` `_score_label_candidates`; README "Reading that table precisely" |
| #31 | U9 attempt-0001 (definition-context tie-break): micro-F1 0.017513 vs 0.007005, decision **keep**; ledger leak gate scans mapping keys | `eval/reports/synthetic_experiments.jsonl` line 1; `eval/reports/synthetic-attempt-0001-definition-context-v1.json` |
| #32 | U9 attempt-0002 (provider fix alone): identical to baseline, decision **park**; synthetic `start` now honors the supplied report; "[]" sequence sentinel in the leak gate | ledger line 2; `eval/folio_eval/experiment.py` `main`, `_manifest_record_collisions` |
| #33 | Synthetic CLI loads the surface manifest with `allow_stale=True` (gold_version 0 slice); hermetic regression | `eval/folio_eval/experiment.py` synthetic branch of `main` |

Main at the HEAD above verifies **1228 passed, 4 skipped** from the owner checkout with the real
gold data present.

## Corrections the successor should not re-learn

- The eval lane did **not** inherit the provider score-scale bug: the synthetic adapter uses
  `search_by_label` only for recall membership and `MultiStrategyRecall._score` computes primary
  scores itself. Attempt-0002 proved it (every aggregate identical). PR #30 was still a real defect for
  library consumers using `MatchPipeline` on the live ontology.
- The persona suite's real-ontology stories are behind `FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1`; the
  extras run needs folio-python's cache present. The path audit allows the interpreter, the repo
  root, and folio-python's cache/config parents only when that opt-in is set.
- The Codex sandbox cannot write worktree git metadata (`index.lock` under the parent `.git`), so
  workers leave changes uncommitted and the orchestrator stages by explicit path. Workers also cannot
  create `~/.alea/logs`, so two CLI checkpoint tests fail only inside the sandbox.
- Never chain `pytest ... | tail -1 && git commit`: the pipe masked two failures once; use
  `set -o pipefail` and check the exit.
- Launching eight scoring shards simultaneously raced folio-python's cache load (three shards died
  with an XML parse error); stagger shard starts by ~20 seconds.

## In flight: U9 attempt-0003

- Branch `feat/u9-attempt-0003-prep` at `79256e2` (pushed): offset-safe definition windows and
  the adapter keeping the strongest same-IRI context; scoring semantics version 3. These were the two
  scoring-semantic findings deferred from the PR #31 review.
- Worktree `.worktrees/feat/u9-attempt-0003-prep` (machine-local), checkpoint
  `eval/data/u9-attempt-0003-checkpoint` inside it (ignored, machine-local), at 226 of 255 items
  when this handoff was written. Eight detached shards write `eval/reports/synthetic-attempt-0003-context-dedup-v1.json`
  with the only approved public label `synthetic-baseline-v1`.
- The branch does **not** yet contain the PR #31–#33 experiment-CLI fixes; merge `main` into it
  before recording.

Finishing sequence, as the orchestrator intended it (not yet the owner's instruction):

1. When the last shard auto-finalizes, move the untracked report out, run `--finalize-only` from a
   clean tree to the same path, and `cmp` the two (attempts 0001 and 0002 were byte-identical).
2. `git merge main`, then record with `eval/run_experiment.py --slice synthetic`: `start` against
   `eval/reports/synthetic-baseline-v1.json`, `finish` with the attempt report and the scorer
   commit `79256e2`. Decision rule relative to the kept state (attempt-0001, F1 0.017513): keep if
   F1 is at or above it with the no-match FP rate not worse; park if identical; revert if lower.
3. PR, Codex review, merge to main only. Keep the worktree (checkpoint evidence).

## Owner-only items still open

U7 consensus sitting; U11 provider-key lanes; the U9 interim aggregate-only firm checkpoint; U13
final exam and adoption verdict; protected-originals disposal; the cockpit answers file for D1–D5 and
the Decision Sheet once the freeze lifts. Two flags for the owner: the synthetic ledger's leak scan
is no longer bound to local firm-gold freshness (PR #33), and a per-tag verdict *application* API
does not exist in `folio_resolve.annotate` (US-AA-01 tests what the public API provides).

## Evidence-bearing local state (do not prune)

Worktrees `finalize/u10-v8-repair` (attempt-0001 checkpoint), `eval/u9-attempt-0002`,
`feat/u9-attempt-0003-prep`, `fix/u8-resumable-scoring`, `fix/u10-public-metadata-exemption`
(U10 v5–v8), `feat/u10-resumable-pilot*`, `fix/u10-venv-interpreter`,
`eval/u8-baseline-v1-report`, and `external/*`. A private, unredacted companion with
machine-local launcher and log paths exists outside the repository.

## Verification performed

Every worker claim was re-run by the orchestrator: full suite per PR (1169 → 1228 passing), Ruff,
mypy on `src` and `eval`, `git diff --check`, both UAT lanes (24 of 24 stories), finalize-only
byte-identity for attempts 0001 and 0002, and live-ontology probes before and after PR #30.
