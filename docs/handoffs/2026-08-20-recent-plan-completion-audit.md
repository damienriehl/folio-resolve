---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-20T00:00:00-05:00"
title: "Recent-plan completion audit, residual queue, and Decision Sheet"
summary: "Audits every plan created in the 2026-07-30 through 2026-08-20 window plus the active 2026-07-27 carryover plan; separates completed work, autonomous residuals, owner-run gates, and five decisions."
keywords: ["plan-audit", "ce-plan", "ce-work", "f1-campaign", "decision-sheet", "residual-work"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Continue the autonomous queue from the first unfinished item; do not reopen completed release work or settled q1-q4 decisions."
repository: "damienriehl/folio-resolve"
branch: "docs/recent-plan-audit-2026-08-20"
---

# Recent-plan completion audit, residual queue, and Decision Sheet

## Audit boundary and method

- **Window:** 2026-07-30 through 2026-08-20, inclusive.
- **Canonical plans created in the window:**
  `docs/plans/2026-08-06-post-hardening-integration-plan.md` and
  `docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md`.
- **Active carryover included for completeness:**
  `docs/plans/2026-07-27-001-feat-f1-improvement-loop-plan.md`. It predates the
  window by three days, but its execution, owner gates, and successor campaign all continued
  inside the window.
- **Plan-like artifacts reviewed as evidence or continuations:** every handoff, migration note,
  and plan-related commit created or changed in the window, including the active successor
  `docs/handoffs/2026-08-17-f1-baseline-restart.md`.
- **Not treated as additional plans:** migration analyses and parity maps that are outputs of a
  canonical plan, completed release handoffs, and `docs/migration/SCHEDULE.md` (its current plan
  predates the window). They were still checked for residuals and contradictions.
- **Evidence rule:** plan checkboxes and prose claims were not accepted alone. Completion was
  cross-checked against files, tests, commit ancestry, merged PRs, release state, and generated
  artifacts. Plan bodies remain unchanged; this handoff owns the audit status.

## Execution update — 2026-08-21

- U8's public-metadata preflight and runtime recovery are committed through `bfbd4bf`. The local
  bounded-cache containment is committed as `f9ee23d` and pushed on
  `fix/synthetic-baseline-runtime`.
- The real one-passage path now leaves folio-python's affected caches at zero and peaks at
  715,524 KiB (about 699 MiB), versus roughly 1.29 GiB before containment. The complete
  225-passage deterministic rerun is active; U8 remains partial until that run passes and its
  report is committed.
- The upstream cache policy is proposed in
  [alea-institute/folio-python#20](https://github.com/alea-institute/folio-python/pull/20). The PR is
  ready for review, mergeable, and green on all six CI architectures, but is not merged or
  released. A separate pushed handoff branch records the post-release reassessment procedure.
- U10's discovered folio-python version skew is repaired in
  [alea-institute/folio-mapper#9](https://github.com/alea-institute/folio-mapper/pull/9): mapper now
  proposes the same exact 0.3.6 pin already used by enrich. The lock check, focused runner tests,
  and full mapper backend suite (`525 passed, 10 skipped`) pass. The PR is open and mergeable but
  has no configured checks. The live gate waits for both U8 and this PR's merge followed by an
  exact lock/environment alignment check; the campaign's version-skew gate requires aborting on
  drift.
- U10 preflight also found that `--limit 1` currently selects one scoreable row plus all 30
  no-match rows, and the committed summary omits required Git/cohort/invocation provenance and
  integrity receipts for the gitignored stage snapshots. Those deterministic-receipt gaps are
  now an autonomous hardening item before the pilot can count as the plan's reproducible artifact.

## Execution update — 2026-08-22

- U8's bounded full-corpus run remains healthy at roughly 23 hours elapsed: the scorer holds one
  CPU at 99.9%, RSS is stable near 687 MiB, and the atomic report path is unchanged pending
  completion. Do not start a competing scorer or consumer suite while it runs.
- U10's local reproducibility prerequisite is implemented on pushed branch
  `fix/u10-comparison-provenance` through commits `732325b` and `f64f08c`. It now fails closed on
  dirty source repositories, unknown/version-skewed consumers, mapper embedding fallback,
  consumer config/stage drift, non-canonical rows, shared-input mutation, and invalid
  scoreable-only runs. The committed artifact records exact execution/config/Git/input/stage
  evidence, gold and no-match IRI sets, and candidate lifecycle attribution. Focused evidence is
  `77 passed`; Ruff, mypy, and diff checks are clean. The branch is pushed but has no PR yet
  because it depends on the active U8 branch, which is not ready for review until the baseline
  artifact finishes.
- [alea-institute/folio-mapper#9](https://github.com/alea-institute/folio-mapper/pull/9) now also
  contains commit `c413a417`: the deterministic runner synchronously builds the production
  embedding index and aborts on an unavailable index or local-score fallback instead of measuring
  a keyword-only strawman. Its focused runner suite passes (`7 passed`).
- [alea-institute/folio-enrich#38](https://github.com/alea-institute/folio-enrich/pull/38) proposes
  commit `b039a86`: the deterministic lane actively pins, emits, and restores every known
  environment-overridable behavioral setting, including proposition extraction and property POS
  mismatch penalty. Its focused runner suite passes (`6 passed`).
- Both consumer PRs remain upstream prerequisites. After merge, refresh both checkouts and prove
  the exact released/locked `folio-python` version and deterministic config contracts before the
  one-item U10 gate. Abort on any drift; do not weaken the new fail-closed checks.

## Plan-level verdicts

| Plan | Verdict | Evidence | Remaining authority |
|---|---|---|---|
| Post-Hardening Integration (2026-08-06) | **Complete** — U1–U5 | folio-resolve PRs #2/#4 merged; mootloop PRs #31 then #30 merged; v0.4.0 release published; all three v0.4.0 consumer-pin PRs merged; post-release handoff records full verification | None. Do not reopen this release from stale handoffs. |
| Synthetic Benchmark F1 Campaign (2026-08-16) | **Partial** — implementation scaffolding mostly landed; runtime campaign tail open | folio-resolve PR #8 and consumer runner PRs merged; U1–U6 and U9 code complete; U3 gold v7/calibration complete; corpus v1 committed; U8 recovery commits `a6e87ef`, `ae16a08`, and review fix `88a0732`; the first recovered baseline failed closed on four public/static metadata collisions after scoring | Autonomous queue below, five decisions, and later owner-run metric gates |
| F1 Improvement Loop (2026-07-27 carryover) | **Implemented/superseded, with live gates carried forward** | U1–U10 implementation and measured v0.4.0 iteration/release evidence landed; Gate 1b folded through gold v7; its gated synthetic and close-out work was restructured into the 2026-08-16 campaign | Follow the newer campaign plan; do not implement old U11/U12 as a parallel design |

## Unit audit — 2026-08-06 plan

| Unit | Status | Verification |
|---|---|---|
| U1 owner-run evaluation gate | **Complete** | `docs/handoffs/2026-08-06-recall-owner-measurement.md`; aggregate measurement later accepted the change |
| U2 mootloop reopen merge | **Complete** | PR #31 merged at `90c68f7` before PR #30 |
| U3 mootloop hardening merge | **Complete** | PR #30 merged at `341125c` after U2 |
| U4 coordination artifacts + lock policy | **Complete** | durable handoffs published; release verification includes `uv lock --check`; current `uv.lock` policy is tracked release state |
| U5 measured release + consumers | **Complete** | folio-resolve PR #2 `c5699e5`, release PR #4 `6648143`, release v0.4.0, consumer pins `29987a6` / `599f977` / `dce4a90` |

## Unit audit — 2026-08-16 plan

| Unit | Status | What remains |
|---|---|---|
| U1 branch reconciliation | **Complete** | Nothing; PR #8 contains both histories |
| U2 Gate 1b sitting assembly | **Complete** | Nothing; human adjudication is folded through gold v7 |
| U3 fold + firm iterations | **Complete for the campaign entry gate** | Gold v7, calibrated answer rule, and the measured no-widening verdict landed. Later interim/final firm exams remain U9/U13 gates. |
| U4 leak manifest/checker | **Complete after recovery** | HMAC v3, reproducible allowlist/public-label filters, approved 3,047-digest manifest, zero corpus collisions |
| U5 corpus schema/builder | **Complete** | Corpus v1: 225 scoreable, 15 needs-review, 30 no-match |
| U6 label-blind generation | **Complete** | Generation artifacts and corpus v1 landed |
| U7 grader/close-call lane | **Partial** | Code is complete. The first consensus-audit sitting, confidence-floor calibration, and human ratification into corpus v2 wait on D1/D2 and owner adjudication. |
| U8 synthetic scoring | **Partial; bounded rerun active** | Recovery/review code and the local bounded-cache containment are committed and pushed. The one-passage real path leaves affected upstream caches empty and peaks near 699 MiB. The complete deterministic run is active and must finish, validate, and commit `eval/reports/synthetic-baseline-v1.json`. |
| U9 guarded iteration loop | **Partial** | Code is complete; no real corpus-v1 improvement iteration is recorded yet. Pilot must run first. |
| U10 deterministic comparison | **Partial; local hardening complete** | Local live-gate selection, reproducibility receipts, stage attribution, and fail-closed consumer contracts are implemented and pushed. Merge/verify mapper PR #9 and enrich PR #38, then run the live gate, 30-item pilot, and final full comparison. |
| U11 owner-run LLM lanes | **Partial / owner-run** | Flags and seams are merged. Run both shipped configurations with owner-held keys and record explicit skip markers where unavailable. |
| U12 parity + attribution | **Partial** | Static parity map is complete. Add controlled replay/ablation only where U10 reveals an incumbent win, subject to D4. |
| U13 campaign report/verdict | **Not started** | Assemble after U9/U10/U12; final firm exam and adoption decision are owner gates. |

## Autonomous queue (`ce-work`)

Execute in dependency order. A later item is queued even when an earlier owner gate prevents it
from running in the same session.

1. **Finish and validate the active U8 baseline.** The bounded-memory probe path, public-metadata
   preflight, and D5 policy are implemented and pushed. Let the active 225-item plus no-match run
   finish; then verify corpus/config hashes, zero non-public leak collisions, deterministic report
   content, depth-probe metrics, and resource evidence before committing the report.
2. **Merge and verify both consumer prerequisites.** Land
   [alea-institute/folio-mapper#9](https://github.com/alea-institute/folio-mapper/pull/9) and
   [alea-institute/folio-enrich#38](https://github.com/alea-institute/folio-enrich/pull/38), update
   both checkouts, and confirm their locks and active environments resolve the exact common
   `folio-python` version. Exercise the exact deterministic config/stage contracts. Abort U10 on
   any version drift, unavailable mapper embedding rerank, or enrich setting drift.
3. **Publish the completed U10 deterministic-receipt hardening after U8.** The implementation is
   committed and pushed on `fix/u10-comparison-provenance`; once the parent U8 branch contains its
   validated report and is ready for review, open the correctly scoped folio-resolve PR without
   dropping the true one-scoreable-item gate, exact process receipts, input fingerprints, full
   stage snapshots, gold/no-match sets, or candidate lifecycle attribution.
4. **Run U10 live gate and 30-item pilot.** Use the pinned v0.4.0 incumbent lanes in the two
   sibling repos, leave both trees byte-identical to their starting status, and commit the pilot
   artifact or a clearly named pilot report if the final report path is reserved.
5. **Interpret the pilot without changing settled policy.** Continue when the result is a clear
   go; route an in-band hold or material incumbent win to the Decision Sheet with exact metrics.
6. **Run the first U9 shared-scope iteration.** Target synonym/definition-side matching and
   ranking only; retrieval widening is ruled out by attempt-0004. Baseline to beat: tune F1
   0.243372, with the synthetic baseline as the agent-runnable development metric.
7. **Complete U12 runtime attribution when needed.** For any incumbent-winning cohort, perform
   the controlled replay/ablation required by D4 before naming a port candidate.
8. **Continue U9 until its guarded stop/checkpoint.** Shared-scope, same-cohort iteration records
   only; then prepare the exact aggregate-only interim firm command for the owner.
9. **Assemble U13 up to owner gates.** Join synthetic trajectory, comparison bands, parity
   evidence, and prepared firm-exam command. After owner results, commit the final report and route
   the adoption verdict.
10. **Run all repository gates and clean campaign-only temporary state.** Preserve unrelated
   `.codex-out/` and `.codex/` files. Delete protected originals only through the owner-side
   close-out procedure after the final exam.

## Owner-run actions (not judgment calls)

These cannot be performed by a worker because they require protected data or owner-held keys.

- Run the U7 consensus-audit sitting and return the downloaded decision JSON.
- Run the U11 enrich/mapper LLM-on lanes with owner-held provider keys, or confirm a provider lane
  should carry an explicit `skipped` marker.
- Run the U9 interim aggregate-only firm checkpoint when the guarded stop logic requests it.
- Run the U13 final firm exam, including the frozen 79 only at final close-out.
- Execute the protected-originals disposal recipe after the final report is accepted.

## Decision Sheet — answer these five now

All five were implemented provisionally with the recommended option, but the prior plan-review
artifact never received a durable answer receipt for q5–q8. Ratifying the recommendations avoids
rework and preserves the implementation-ready plan as written.

### D1. Audit machine-consensus gold each corpus version?

**Recommended: Yes — one fixed random ~25-row sitting per corpus version.** This measures errors
where all graders agree but are jointly wrong; the correction rate becomes the trust bound for
provisional gold. Choosing No means only disagreements receive human review.

Answer: `D1 = Yes` or `D1 = No`

### D2. How should the grader-confidence floor be set?

**Recommended: Calibrate from the first adjudicated sample.** This ties the threshold and queue
size to observed owner corrections. The alternative is to name a fixed numeric floor now.

Answer: `D2 = Calibrate` or `D2 = Fixed <number>`

### D3. Keep the no-match slice in corpus v1?

**Recommended: Yes.** Thirty no-match passages already exist and expose false positives from
always-answer behavior. Choosing No requires revising the committed corpus/config and makes the
cross-stack metric less honest.

Answer: `D3 = Yes` or `D3 = Defer to v2`

### D4. Require replay/ablation before naming a port candidate?

**Recommended: Yes.** Stage snapshots establish correlation; a controlled replay is required to
show that a missing/divergent component actually closes the measured gap. Choosing No permits
stage-diff evidence alone.

Answer: `D4 = Yes` or `D4 = No`

### D5. How should public/static report metadata interact with the firm leak gate?

**Recommended: Versioned public-metadata exclusions plus an early preflight.** Build the exclusion
source only from independent committed inputs and fixed schema constants (report kind, run label,
synthetic config rationale, determinism target), never from the generated report or corpus under
test. Continue scanning every other dynamic string and fail closed. The alternative is to redesign
the report into opaque/minimal metadata so those strings never appear.

Answer: `D5 = Public metadata exclusions` or `D5 = Redesign report metadata`

### Copy/paste answer block

```text
D1 = Yes
D2 = Calibrate
D3 = Yes
D4 = Yes
D5 = Public metadata exclusions
Notes (optional):
```

## Later evidence-bound decisions (do not answer yet)

- **Pilot go/redirect:** answer only after the three-stack 30-item metrics and bands exist.
- **Interim stop/redirect:** answer only if the firm checkpoint does not corroborate the synthetic
  trajectory or if the guarded stop condition fires.
- **Final per-consumer adoption:** answer only from the U13 report's R17 verdicts and final firm
  exam, never from synthetic F1 alone.

## Verification state at audit time

- `.venv/bin/python -m pytest -q`: **1014 passed, 1 skipped** after the bounded-cache containment.
- `uv run ruff check`: **passed**.
- `uv run mypy src`: **passed (26 files)**.
- `uv run mypy eval`: initially exposed 10 residual errors in four post-gold helper scripts;
  repaired in `ae16a08`, then **passed (41 files)**.
- Leak check over `corpus_v1.jsonl` and `nomatch_v1.jsonl`: **zero collisions** against the
  corrected HMAC-v3 3,047-digest manifest.
- First recovered baseline: **failed closed after ~4h27m** with four collisions, isolated by
  count-only diagnosis to public/static aggregate paths (`kind`, `label`, config `rationale`,
  determinism `target`). No report was written; no private surfaces were printed or inspected.
- Peak observed baseline RSS: **~30.5 GB**. The full-result per-passage adapter cache is the
  confirmed source; the existing ranked top-200 item results are sufficient for the depth probe.
- Bounded one-passage evidence: affected folio-python caches return to zero; peak RSS is
  715,524 KiB (about 699 MiB). The complete bounded rerun emits no per-item progress and is still
  active; the measured one-item path implies a conservative roughly 27-hour total including the
  no-match slice.
- Synthetic baseline/comparison/final-report receipts: still pending.

## Shipping state

- Audit branch: `docs/recent-plan-audit-2026-08-20`.
- Recovery commit: `a6e87ef`.
- Strict-type gate commit: `ae16a08`.
- Structured-review fix commit: `88a0732`.
- This handoff is committed alone on its focused documentation branch.
- Commit `4ad694f` and subsequent audit-only updates are pushed on the focused branch; the branch
  is not merged.
