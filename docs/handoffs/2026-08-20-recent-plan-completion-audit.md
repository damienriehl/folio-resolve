---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-20T00:00:00-05:00"
title: "Recent-plan completion audit, residual queue, and Decision Sheet"
summary: "Audits every plan created in the 2026-07-30 through 2026-08-20 window plus the active 2026-07-27 carryover plan; records completed work, the autonomous residual queue, owner-run gates, and evidence-bound decisions."
keywords: ["plan-audit", "ce-plan", "ce-work", "f1-campaign", "decision-sheet", "residual-work"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Continue the autonomous queue from the first unfinished item; do not reopen completed release work or settled q1-q4 decisions."
repository: "damienriehl/folio-resolve"
branch: "docs/u10-v8-recovery-2026-08-29"
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
  contains reviewed head `c413a417`: the deterministic runner synchronously builds the production
  embedding index and aborts on an unavailable index or local-score fallback instead of measuring
  a keyword-only strawman. Its focused runner suite passes (`7 passed`).
- [alea-institute/folio-enrich#38](https://github.com/alea-institute/folio-enrich/pull/38) contains
  reviewed head `b039a86`: the deterministic lane actively pins, emits, and restores every known
  environment-overridable behavioral setting, including proposition extraction and property POS
  mismatch penalty. Its focused runner suite passes (`6 passed`).
- All three authorized upstream PRs merged on 2026-08-22: folio-python #20 as `1b4fb349`, mapper
  #9 as `a5bd0512`, and enrich #38 as `bb576ac`. The fetched mapper/enrich default-branch trees
  are byte-identical to the reviewed heads. Mapper's lock and active environment resolve
  `folio-python 0.3.6`; enrich's lock and active environment also resolve `0.3.6`, satisfying the
  fail-closed U10 version-equality prerequisite. The focused deterministic-runner contract tests
  passed before merge against those identical trees.
- The bounded-cache policy is now merged into folio-python source, but no release contains it yet:
  the latest release remains `v0.4.0` from 2026-08-18, before merge commit `1b4fb349`. Keep the
  local `f9ee23d` containment until a containing release can be installed and the durable
  post-release memory/determinism checklist passes.
- Runtime-ready clean worktrees now survive ordinary reboot/cleanup under
  `.worktrees/external/folio-mapper-u10-merged` at `a5bd0512` and
  `.worktrees/external/folio-enrich-u10-runner` at `bb576ac`. Their locked environments contain
  `folio-python 0.3.6` and `folio-resolve 0.4.0`; mapper also contains the locked FAISS and
  sentence-transformers dependencies. The clean U10 candidate worktree is provisioned at
  `.worktrees/fix/u10-comparison-provenance`. Only the live execution remains gated on U8.

## Power-loss recovery update — 2026-08-22

- The original single-process U8 rerun ended in a host power loss after more than 31 hours. It had
  no item checkpoint and produced no publishable report. The source worktrees, pushed branches,
  upstream merges, and unrelated main-checkout work all survived and were preserved.
- Power-loss recovery is now implemented on `fix/u8-resumable-scoring`. Commit `933c6ef` adds a
  fingerprint-bound, corruption-detecting, privacy-minimal item checkpoint, stable hash sharding,
  finalize-only replay, atomic durable writes, and direct/resumed/sharded report-equivalence tests.
  PR [#9](https://github.com/damienriehl/folio-resolve/pull/9) is open and mergeable. Review fixes
  through `dad7f4c` make the clean-checkout command install the `folio` extra and make shard ETA
  use a shard-local denominator.
- The authoritative validation after the progress fix is `1054 passed, 2 skipped`; Ruff, both
  mypy targets, and `git diff --check` pass. The PR watcher is active and has no unresolved
  feedback; the repository config currently exposes no GitHub status checks.
- The replacement U8 baseline began at 2026-08-22 23:17 America/Chicago as eight concurrent
  shards sharing `.synthetic-checkpoints/baseline-v1`. The generation is intentionally pinned to
  clean commit `933c6ef`; later PR commits do not change scoring semantics. If another interruption
  occurs, resume/finalize this checkpoint from an exact clean `933c6ef` checkout rather than from
  the moving PR head. Do not edit the accepted manifest or bypass its fingerprint check.
- Durable item files are landing and memory remains bounded with no swap use. All eight first-item
  samples were roughly 6.7–15.5 minutes, implying an early six-to-nine-hour eight-shard estimate;
  use item-file timestamps for this in-flight generation because `933c6ef` predates the shard-ETA
  display correction.
- The U10 live-gate preflight remains clean and ready behind U8. The durable consumer worktrees are
  still exactly `a5bd0512` (mapper) and `bb576ac` (enrich); both environments resolve
  `folio-python 0.3.6` and the released `folio-resolve 0.4.0`, and both deterministic runner seams
  are present. Their normal import path opens the owner's standard ALEA log, so the real gate must
  run with ordinary owner filesystem access rather than inside a read-only home sandbox.

## Execution update — 2026-08-23

- U8 is complete. All 255 fingerprint-bound checkpoint items finished (225 scoreable plus 30
  no-match), and finalize-only replay from exact scorer commit `933c6ef` produced the same bytes
  as automatic finalization. The accepted checkpoint fingerprint is
  `7dfc071fb98babb879cde7cd51d2d92eeffc035a36f00a72aa6adc2117cc3fd1`; the original report
  digest was `ab87ab5622bfd1a4ca17fad0a65007a6c02a729dbad394498da80f25b31823a6`.
- The baseline records F1 `0.007005`, precision `0.004444`, recall `0.016529`, TP `6`, FP `1344`,
  FN `357`, and no-match false-positive rate `1.0`. Candidate accounting reconciles exactly:
  792,136 raw candidates, 382,393 suppressions, and 409,743 survivors. Determinism and
  candidate/item-accounting receipts pass.
- Structured review found that the original 10/50/200 depth cutoffs all exceeded `top_k = 6`
  and therefore could not measure sensitivity. The corrected dynamic 1/3/6 probe produces F1
  `0.0`, `0.0`, and `0.007005`. That result does not reproduce the prior firm-lane retrieval
  dead-end, so the settled firm no-widening result must not be generalized to the synthetic lane
  without further evidence. The corrected generated report digest is
  `813f9d2efd21302f009c95a60a883c459e8e2a56cf0392c9472c70472fb8ffa7`.
- PR [#9](https://github.com/damienriehl/folio-resolve/pull/9) merged as `4e17072b`; corrected
  report PR [#10](https://github.com/damienriehl/folio-resolve/pull/10) merged as `4bd353d5`.
  Report validation is `1055 passed, 2 skipped`; Ruff, mypy (`src` and `eval`), and diff checks
  pass.
- U10 live-gate recovery exposed and repaired three independent fail-closed defects. Commit
  `96cb650` preserves the consumer virtualenv interpreter symlink instead of resolving it to the
  base Python. Mapper commit `79c323f` installs `folio-python[search] == 0.3.6` and locks
  `alea-llm-client == 0.3.3`; upstream PR
  [alea-institute/folio-mapper#10](https://github.com/alea-institute/folio-mapper/pull/10) merged as
  `433736c7`. Commit `ce3c918` removes redundant free-text labels from local stage snapshots so
  the protected-surface leak gate can remain fail-closed while retaining IRIs, scores, ranks,
  gate reasons, and full candidate accounting.
- The clean one-scoreable-item three-stack gate completed at local commit `d814881`. All three
  stage snapshots and the final comparison passed their leak gates; report SHA-256 is
  `65dccf4d0d45b86b38d177a1eba8632190941414526363ed867cbcdbcaf2a621`. Exact corpus/config,
  dependency-version, invocation, repository, items-file, and stage-file receipts are present.
- Required manual IRI inspection found a real comparability defect before the pilot: mapper emits
  bare `R...` hashes while gold, enrich, and folio-resolve use full
  `https://folio.openlegalstandard.org/...` IRIs. Mapper's apparent zero score is therefore not a
  valid pilot signal. Upstream mapper PR
  [#11](https://github.com/alea-institute/folio-mapper/pull/11) emits canonical full IRIs and fails
  closed on foreign namespaces; folio-resolve PR #11 rejects non-canonical consumer IRIs before
  scoring. Both fixes merged as mapper `35c9d307` and folio-resolve `659ca93`.
- The corrected one-item gate passed leak checks with report digest
  `1548b6d41e16246c9909025e4a3fb852174de65938b350c216b3ad5d1cb0d469`. All scored outputs plus
  mapper/enrich stage IRIs are canonical full FOLIO IRIs. The sole foreign local raw trace is
  `owl:Thing`; it is explicitly gated, unranked, and uncommitted. The one-item metrics are all zero
  and are plumbing evidence only, not a comparative verdict.
- The required pilot is 30 scoreable plus all 30 no-match rows and still projects to roughly a day.
  PR [#12](https://github.com/damienriehl/folio-resolve/pull/12) at `2435df2` shards it into 60
  independently published, leak-gated reports; fsyncs each completed shard; binds exact consumer
  interpreter/distribution/model and repository fingerprints; and emits explicit aggregate
  invocation receipts. The real durable checkpoint is initialized at 0/60 with fingerprint
  `1b7f98f0a75467866686d0f5b398039fe1c23e2dabea59f6a64dee9d9eac6539`; no shard started before
  the corrected gate passed.

## Execution update — 2026-08-24

- Outage-safe pilot PR [#12](https://github.com/damienriehl/folio-resolve/pull/12) merged as
  `a78f8fe9` after exact-head review of `f0c0fe1d`; final validation was `1126 passed, 2 skipped`,
  with the 94 focused tests, Ruff, mypy, and diff checks clean. Mapper receipt-hardening PR
  [#12](https://github.com/alea-institute/folio-mapper/pull/12) likewise merged as `626412bb`.
- The first authoritative v4 shard completed all three leak-clean stage snapshots, then failed
  closed before publishing its item receipt or final report. Privacy-safe structural diagnosis
  localized the only collision to the committed public-comparison metadata path recorded in the
  invocation receipt. The v4 checkpoint remains preserved as failed evidence and must not be
  resumed or treated as completed.
- Focused PR [#13](https://github.com/damienriehl/folio-resolve/pull/13) added the missing exact,
  contract-bound public path and ensured direct CLI callers record the resolved committed default.
  It merged as `331c507c` from reviewed head `f9443d2`; saved-stage reconstruction passes without
  rerunning models. Final validation was `1128 passed, 2 skipped`, 74 focused tests, with Ruff,
  mypy, and diff checks clean.
- The corrected v5 pilot checkpoint passed its one-item canary and reached 3/60 from exact reviewed
  head `f9443d2`,
  with fingerprint `c450a2b04d1fb4e2fbeb1433ecea832f36ef02c02de407083895f63777efa4a8`
  and manifest SHA-256 `7e00bb154d8753a66d0370cac89a3325957bedce9a4d652a5fbbb58e31dc4994`.
  A fourth shard finished its expensive computation but failed closed before publishing a receipt:
  17 newly generated standard-library bytecode caches changed the post-shard runtime fingerprint.
  The v5 checkpoint remains preserved with three valid receipts and must not be resumed after the
  fingerprint policy changes.
- PR [#14](https://github.com/damienriehl/folio-resolve/pull/14) contains the bounded local repair.
  The pilot launcher disables bytecode writes before importing the runner, while the fingerprint
  exempts only current-interpreter, source-backed caches whose header, recursive values, observable
  metadata, and exact normalized marshal representation match freshly compiled source. Malformed,
  stale, divergent, legacy, sourceless, symlinked, tool-specific, and unsupported-optimization
  caches remain fingerprint-bound or fail closed. Executable head `6574cdd` passes `1151 passed,
  2 skipped`, 78 focused pilot tests, Ruff, eval mypy, and diff checks, and its final exact-head
  automated review found no major issues. The PR merged as `f946613`; its later merge-parent change
  contained only already-reviewed documentation PR #15.
- Fresh v6 initialized successfully at 0/60 from merged main with fingerprint
  `800667da699a1fe6e845b954a8a20795e9fd3156570675d251a372b828edd35c` and corpus-manifest
  SHA-256 `9f473f6264db4e92d3e8b3093758562aad3b94e5e8a22711687d617e01687d44`.
  Fail-closed preflight identified three stale generated caches left by earlier branch/runtime
  transitions; only those exact regenerable files were removed. Candidate, mapper, and enrich
  environment probes then passed. The one-item canary completed end-to-end and published exactly
  one receipt with all fail-closed gates intact. The full resumable run then advanced to 4/60,
  crossing the exact three-receipt boundary where v5 failed; shard 5 is active.

## Execution update — 2026-08-28

- v6 ultimately advanced to 23/60. The twenty-fourth shard failed closed while constructing and
  leak-checking its serialized items JSONL, before its items file or any consumer run was published.
  The whole serialized JSONL file was scanned as one string, so two otherwise clean tokens on
  opposite sides of a JSON serialization boundary formed a protected n-gram that existed in no
  source value. Privacy-safe structural probes confirmed every item ID, passage, segment, key, and
  other string value passed independently; the sole collision was introduced by serialization
  itself.
- The v6 evidence remains intact and must not be resumed after the source change: 23 verified
  completion receipts, 24 item directories, 23 reports, 23 item files, and no final receipt or
  published comparison report.
- Focused PR [#18](https://github.com/damienriehl/folio-resolve/pull/18) fixes that representation
  mismatch by scanning each structured JSON string independently before serialization and by
  rejecting Python-only shapes that JSON would silently coerce. Mapping keys remain scanned. The
  reviewed change merged as `8e133bf`; 71 focused comparison/leak tests, `1155 passed, 2 skipped`,
  Ruff, changed-module mypy, and diff checks pass. Full-repository mypy still has only the unrelated
  pre-existing `src/folio_resolve/embedding.py:149` `no-any-return` finding.
- Fresh v7 initialized from exact merged main with the intended `eval/run_comparison_pilot.py`
  wrapper, zero receipts, and no final report. The wrapper supplies package discovery without the
  forbidden mutable `PYTHONPATH` override. Ordinary owner filesystem access remains required so the
  consumer environments can create their normal ALEA log and mapper CPU index artifacts. Its
  one-shard canary and zero-work replay both passed with exactly one durable receipt; the remaining
  59 shards and finalization are active. v6 remains untouched.

## Execution update — 2026-08-29

- v7 advanced cleanly to 23/60 and then failed closed on the twenty-fourth shard while writing its
  third stage snapshot. Item generation and both earlier consumer snapshots had completed. The
  stage-snapshot leak gate still scanned the fully serialized JSON text, so otherwise clean strings
  on opposite sides of a JSON value boundary formed a protected n-gram that existed in no source
  string. The same whole-subtree optimization remained in final comparison publication and would
  have exposed the identical false-positive class at finalization. v7 remains intact as failed
  evidence: 23 completion receipts and reports, 24 item files, and no final report.
- PR [#20](https://github.com/damienriehl/folio-resolve/pull/20) applies the structured-value rule
  consistently to stage snapshots and final comparison publication while preserving exact approved
  public-metadata exemptions. It merged as `db1cf4c` after `1156 passed, 2 skipped`, 72 focused
  comparison/leakcheck tests, Ruff, changed-module mypy, diff checks, and an isolated replay of the
  exact previously failing shard. That replay exited zero with all three stage snapshots, one final
  report, and zero comparison/leak errors.
- Fresh v8 initialized at 0/60 from exact clean merge commit `db1cf4c`, with a new fingerprint-bound
  manifest and no reuse or mutation of v6/v7 artifacts. The canonical output remains
  `eval/reports/synthetic-comparison-v1.json`. The full serial run is active; finish and interpret
  it before choosing the U9 shared-scope iteration or any U12 attribution work.

## Execution update — 2026-09-02 (resume after reboot)

- The reboot handoff written on 2026-08-30 merged through PR
  [#27](https://github.com/damienriehl/folio-resolve/pull/27). Main at `2191835` verifies
  `1168 passed, 1 skipped` under `PYTHONHASHSEED=0`.
- U9 attempt-0001 resumed on 2026-09-02 from its exact fingerprint-bound checkpoint (17 durable
  receipts at resume) at scoring commit `4e0407e` on the now-pushed branch
  `feat/u9-u12-loss-recovery`. Eight shards run in parallel under a detached launcher; the
  checkpoint store validated the unchanged fingerprint before any shard started. The planned
  report path from the original runner is honored at finalization through a finalize-only replay,
  which must be byte-identical to the automatic finalization.
- The owner answered the Decision Sheet inline on 2026-09-02: **D1 = Yes, D2 = Calibrate,
  D3 = Yes, D4 = Yes, D5 = Public metadata exclusions.** All five match the provisional
  implementation, so no rework follows. The cockpit answers receipt is deferred until the owner
  lifts the cockpit freeze; this handoff is the durable receipt meanwhile.
- The owner also settled three execution-policy questions the same day: a winning U9 iteration is
  merged to `main` only (no `v0.5.0` tag and no consumer pin PRs until the adoption verdict);
  merged, evidence-free worktrees and branches are pruned while every checkpoint-bearing worktree
  and the external consumer clones are preserved; and the persona/user-story acceptance work ships
  as a committed UAT suite plus a report.
- folio-python's latest release is still `v0.4.0` (checked 2026-09-02), so the cache follow-up in
  `docs/handoffs/2026-08-21-folio-python-cache-upstream-followup.md` remains open and inactive.
- Retired seven consumed handoffs in this update. Each was resumed by a later session, its tasks
  completed, and its learnings absorbed here or in `docs/solutions/`: the 2026-07-28 Gate 1b handoff
  (folded through gold v7); the three 2026-08-06 handoffs (Codex orientation, eval-loop transition,
  recall owner measurement — all closed by the v0.4.0 release and this audit); the 2026-08-09
  post-release handoff (release verified complete); the 2026-08-17 baseline restart (superseded by
  the completed U8 baseline); and the 2026-08-29 U10 v8 reboot handoff (consumed by PRs #24–#26).
  `git log --diff-filter=D -- docs/handoffs/` recovers any of them.
- Worktree/branch hygiene: eight merged, clean, evidence-free worktrees were removed and eighteen
  merged remote branches deleted. Preserved: the U8 baseline checkpoint, the U10 live-gate and
  pilot v1–v8 checkpoints, the live U9 attempt-0001 checkpoint, and the external mapper/enrich
  clones.

## Plan-level verdicts

| Plan | Verdict | Evidence | Remaining authority |
|---|---|---|---|
| Post-Hardening Integration (2026-08-06) | **Complete** — U1–U5 | folio-resolve PRs #2/#4 merged; mootloop PRs #31 then #30 merged; v0.4.0 release published; all three v0.4.0 consumer-pin PRs merged; post-release handoff records full verification | None. Do not reopen this release from stale handoffs. |
| Synthetic Benchmark F1 Campaign (2026-08-16) | **Partial** — U8 complete; deterministic comparison and guarded iteration tail open | folio-resolve PRs #8–#20 and consumer runners merged; U1–U6 and U8 code/report complete; U3 gold v7/calibration complete; corpus v1 committed; U10 v6/v7 preserved after distinct serialization-boundary false collisions; corrected v8 initialized from merged main | Autonomous queue below, evidence-bound decisions, and later owner-run metric gates |
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
| U8 synthetic scoring | **Complete** | All 255 checkpoints completed at scorer `933c6ef`; automatic and finalize-only reports were byte-identical. Corrected depth-probe report PR #10 merged as `4bd353d5`; accounting, determinism, leak, test, type, and lint gates pass. |
| U9 guarded iteration loop | **Partial** | Code is complete; no real corpus-v1 improvement iteration is recorded yet. Pilot must run first. |
| U10 deterministic comparison | **Partial; v8 full run active at 0/60** | Mapper #11/#12 and folio-resolve #11–#20 are merged. v4–v7 remain preserved fail-closed evidence. v7 reached 23/60 before its twenty-fourth shard exposed the remaining whole-JSON scan in stage snapshots; PR #20 repaired stage and final-publication scans, and the exact shard replay passed end-to-end. Finish and interpret fresh v8 before the final full comparison. |
| U11 owner-run LLM lanes | **Partial / owner-run** | Flags and seams are merged. Run both shipped configurations with owner-held keys and record explicit skip markers where unavailable. |
| U12 parity + attribution | **Partial** | Static parity map is complete. Add controlled replay/ablation only where U10 reveals an incumbent win, subject to D4. |
| U13 campaign report/verdict | **Not started** | Assemble after U9/U10/U12; final firm exam and adoption decision are owner gates. |

## Autonomous queue (`ce-work`)

Execute in dependency order. A later item is queued even when an earlier owner gate prevents it
from running in the same session.

1. **Complete — finish and validate U8.** All 255 checkpoint items completed at exact scorer
   `933c6ef`; automatic and finalize-only reports were byte-identical. The corrected 1/3/6 depth
   probe, report artifact, and full validation landed through folio-resolve PR #10.
2. **Complete — merge and verify both consumer prerequisites.** Mapper #9 and enrich #38 merged as
   `a5bd0512` and `bb576ac`. Their fetched default-branch trees match the reviewed heads; both
   locks and active environments resolve `folio-python 0.3.6`. Focused contract suites passed on
   the identical pre-merge trees. Preserve the fail-closed aborts for version drift, unavailable
   mapper embedding rerank, and enrich setting drift in every live run.
3. **Complete — publish the U10 deterministic-receipt hardening.** The implementation commits
   `732325b` and `f64f08c` are included in PR #9 together with the U8 recovery chain. Do not open a
   duplicate U10 PR. Preserve the true one-scoreable-item gate, exact process receipts, input
   fingerprints, full stage snapshots, gold/no-match sets, and candidate lifecycle attribution
   when running the live gate and pilot.
4. **In progress — run the outage-safe U10 pilot.** Mapper #11/#12 and folio-resolve #11–#18 are
   merged. The v4 and v5 checkpoints remain preserved after their distinct fail-closed defects.
   v6 passed its canary and advanced to 23/60; its twenty-fourth shard then exposed a false
   protected n-gram created only across JSON serialization boundaries. PR #18 now scans strict
   structured values before serialization. Fresh v7 initialized from exact merged main; its
   one-item canary and zero-work replay passed at 1/60, and the remaining 59 shards plus
   finalization are active. Continue/resume v7's independent receipts through 60/60; do not resume
   v6 after the source change.
5. **Complete — resumable/sharded scoring.** PR #9 checkpoints each surface-free post-adapter
   result atomically, replays through the unchanged score/depth-probe/report/leak-check path, and
   fails closed on fingerprint or record corruption. Direct, resumed, and differently ordered
   sharded reports are covered for equality. This was advanced ahead of U10 after the host outage
   proved that another non-resumable full baseline was the larger execution risk.
6. **Interpret the pilot without changing settled policy.** Continue when the result is a clear
   go; route an in-band hold or material incumbent win to the Decision Sheet with exact metrics.
7. **Run the first U9 shared-scope iteration.** Target synonym/definition-side matching and
   ranking only; retrieval widening is ruled out by attempt-0004. Baseline to beat: tune F1
   0.243372, with the synthetic baseline as the agent-runnable development metric.
8. **Complete U12 runtime attribution when needed.** For any incumbent-winning cohort, perform
   the controlled replay/ablation required by D4 before naming a port candidate.
9. **Continue U9 until its guarded stop/checkpoint.** Shared-scope, same-cohort iteration records
   only; then prepare the exact aggregate-only interim firm command for the owner.
10. **Assemble U13 up to owner gates.** Join synthetic trajectory, comparison bands, parity
   evidence, and prepared firm-exam command. After owner results, commit the final report and route
   the adoption verdict.
11. **Run all repository gates and clean campaign-only temporary state.** Preserve unrelated
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
  715,524 KiB (about 699 MiB). The complete bounded rerun subsequently finished all 255 items and
  finalized byte-identically from its exact checkpoint fingerprint.
- Synthetic baseline receipt: complete and merged. The original U10 live metric was invalidated by
  the bare-hash/full-IRI unit mismatch; the corrected leak-clean live receipt is complete. Pilot and
  final comparison receipts remain pending.

Latest pilot verification: PR #12 passed `1126 passed, 2 skipped`; PR #13 passed
`1128 passed, 2 skipped`; PR #14 passed `1151 passed, 2 skipped`. PR #18 passes 71 focused
comparison/leak tests and `1155 passed, 2 skipped`; Ruff, changed-module mypy, and diff checks pass.
The unchanged source tree currently has one pre-existing `no-any-return` mypy warning at
`src/folio_resolve/embedding.py:149`. v6 preserves 23 completion receipts, 24 item directories, and
no final receipt; corrected v7 is initialized from exact PR #18 merged main.

## Shipping state

- Current audit-update branch: `docs/u10-v7-recovery-2026-08-28`.
- Recovery commit: `a6e87ef`.
- Strict-type gate commit: `ae16a08`.
- Structured-review fix commit: `88a0732`.
- This update is isolated to this handoff on its focused documentation branch.
- The original audit and recovery chain merged through PR #16 as `473f59e`; the subsequent v6
  execution update merged through PR #17 as `a4bacb9`.
- U8 resumable-scoring PR #9 merged as `4e17072b`; corrected report PR #10 merged as `4bd353d5`.
- U10 implementation PR [#11](https://github.com/damienriehl/folio-resolve/pull/11) merged as
  `659ca93`; it includes review fixes for single-consumer reports, exact CLI/public-metadata grammar,
  safe template parsing, and the non-canonical-IRI abort. Final validation was `1058 passed,
  2 skipped` with Ruff and diff checks clean.
- Mapper dependency PR [alea-institute/folio-mapper#10](https://github.com/alea-institute/folio-mapper/pull/10)
  merged as `433736c7`. A separate batch-embedding prototype reproduced the scalar one-item stage
  object exactly but took about nine minutes, not a material enough improvement to ship.
- Mapper canonical-IRI PR [alea-institute/folio-mapper#11](https://github.com/alea-institute/folio-mapper/pull/11)
  merged as `35c9d307`; focused runner tests are `9 passed`, full backend is `529 passed, 10
  skipped`, lock and diff checks pass. A clean Python 3.11 runtime at that exact merge commit imports
  `folio-python 0.3.6`.
- Outage-safe pilot PR #12 merged as `a78f8fe9` from reviewed head `f0c0fe1d`; full verification is
  `1126 passed, 2 skipped`, with its focused, Ruff, mypy, and diff gates clean. The failed v4
  checkpoint is preserved with zero completion receipts and no final report.
- Public-metadata contract PR #13 merged as `331c507c` from reviewed head `f9443d2`; full
  verification is `1128 passed, 2 skipped`. Corrected v5 is fingerprint-bound at 3/60; its fourth
  item failed closed before receipt publication after new bytecode caches changed the runtime
  fingerprint. Its manifest digest is
  `7e00bb154d8753a66d0370cac89a3325957bedce9a4d652a5fbbb58e31dc4994`.
- Runtime-fingerprint PR #14 merged as `f946613`; executable head `6574cdd` received a clean
  exact-head automated review. Fresh v6 uses fingerprint
  `800667da699a1fe6e845b954a8a20795e9fd3156570675d251a372b828edd35c`; its one-item canary passed,
  and the full resumable run advanced to 4/60 without reproducing v5's post-shard runtime drift.
- Durable cache-reassessment handoff PR #15 merged as `04905da`. It records that upstream
  folio-python PR #20 is merged but remains absent from the latest `v0.4.0` release, so local
  containment stays until a containing release passes the memory and determinism checklist.
- Structured JSON leak-scan PR #18 merged as `8e133bf`. v6 is preserved at 23/60 with no final
  receipt; v7 initialized from that exact merge, passed its one-shard canary and zero-work replay,
  and is running the remaining 59 shards plus finalization.
