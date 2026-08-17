---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-17T21:05:00Z"
title: "F1 campaign — corpus v1 landed, synthetic baseline needs a restart"
summary: "Successor doc to the 08-17 unified handoff (retired): calibration, the R5 probe, and the corpus-v1 release are all DONE and pushed; the synthetic baseline run was killed at ~1h22m and must be restarted, then the 30-item pilot, then the synonymy-scoped recall round."
keywords: ["folio-resolve", "f1-campaign", "synthetic-baseline", "pilot", "recall", "synonymy", "leak-gate", "owner-plaintext"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Restart the synthetic baseline (run_synthetic.py), commit its report, run the 30-item pilot, then start the synonymy-scoped recall round"
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb82"
branch: "main"
head: "9a5e5ea"
---

# F1 campaign — corpus landed, baseline pending (2026-08-17 evening)

**Supersedes and retires** `2026-08-16-f1-campaign-runtime.md`,
`2026-08-17-gold-adjudication-v7-continuation.md`, and
`2026-08-17-f1-campaign-unified.md` (this session consumed the unified doc and
executed its steps 1–2; deletions are recoverable via
`git log --diff-filter=D -- docs/handoffs/`). Another agent is active in this
checkout — re-verify state and `git pull` before acting.

## Done today (all on `main`, all remote-verified)

- **Answer rule refit on gold v7** — `0065f4b`. threshold=0.05, top_k=4; tune F1
  .221→.243, recall-side. Reports: `eval/reports/baseline-v7.json`,
  `score-v7-…-tune-v7-calibrated.json`.
- **R5 retrieval-widening re-probe** — `47c26a1`, KTD8 attempt-0004 (rebaseline
  boundary opened the v7 window). Damien-authorized; verdict **revert**: depth
  10→25 left tune recall flat, precision down. **R5 stands, re-validated on v7.**
  Trail receipt folded (`trail-receipt-20260817t153055-q7xcfn`).
- **Corpus v1 released** — `9a5e5ea`. 225 scoreable + 15 needs_review + 30
  no-match; non-lexical fraction 1.0; synthetic-lane answer rule pinned top_k=6
  (`eval/synthetic/answer_rule_config_synthetic_v1.json`, sha ac413db6 matches
  the corpus manifest). Surface manifest **regenerated v7-bound** at the settled
  3,047 digests; owner-run scan clean. Read
  `docs/solutions/2026-08-17-leak-gate-owner-scans-and-manifest-regeneration.md`
  BEFORE touching the leak gate or regenerating a manifest — it records the two
  filter classes and the scan-cost traps that ate most of today.

## Concurrent agent on `feat/synthetic-baseline-u8` — read this first

A second agent took over the baseline/leak-gate mechanics the same evening
(uncommitted on its branch at snapshot time): scrypt → **HMAC-SHA256** manifest
v3 (sound: the private salt is the key, so blind worker checks survive at ~1s
per scan — this satisfies Damien's owner-plaintext/workers-hashed ruling for
both audiences), a `--allowlist` flag on `generate`, an `--allow-public-file`
overlap exclusion, an adapter cache deduplicating the baseline's two
document-adapter passes, and a restarted baseline run. Coordinate before
duplicating any of that. Two corrections that agent (and any successor) needs:

- **The 379 manifest overlaps were adjudicated exclusions, not concealment.**
  Damien ruled the public-FOLIO-label class + the 126-entry ledger
  generic/public on 2026-08-16. Regenerating from v7 gold minus both classes
  lands on exactly 3,047 digests and the corpus scans clean — independently
  verified twice today.
- **Feed `--allow-public-file` the public FOLIO label dictionary, never the
  corpus under test.** Corpus-occurrence exclusion is circular pre-release: a
  genuine leak that slips into a draft corpus would become "already public" and
  permanently unchecked. Class-based exclusion (dictionary + ledger) is
  reproducible and restores the settled 3,047 firm-specific semantics (the v3
  manifest's 3,464 keeps ~400 public labels that will phantom-collide on any
  future corpus).

## Next steps, in order

1. **Synthetic baseline** — now owned by the u8 agent's restarted run (adapter
   cache, HMAC gate). Verify its report against the corpus manifest pins
   (content_sha256 a417085a, config sha ac413db6), then commit it. The built-in
   depth probe (10/50/200) in that report is the input R5's synthetic-lane
   carve-out needs. If the run needs re-running: expect ~100% CPU with NO
   stdout (buffered) — judge liveness by `ps -o etime,cputime`, never log
   growth. Round one was SIGTERM'd externally (u8 agent, deliberate, manifest
   v3 migration).
2. **30-item pilot** via the downstream harness (`eval/run_downstream.py`) —
   the U10 comparison, required before any U9 iteration. The u8 agent's
   "cross-stack live gate" run (1 scoreable + 30 no-match) may already be a
   piece of this — check its branch before duplicating.
3. **Recall round, scoped by the probe verdict**: synonymy (212 tune misses,
   zero token overlap — synonym/definition-side matching) + ranking levers
   only. No retrieval widening (R5, re-validated by attempt-0004). Baseline to
   beat: tune F1 0.243372.
4. **Open items on the board**: runtime-decisions ask q5–q8 provisional marks
   (close-call queue rendering waits on Gate 1b / KTD8 precedence).

## Working knowledge

- **Fold recipe** (unchanged except the manifest lesson): after ANY fold,
  re-score with `--build-splits` on a NEW split manifest; regenerate the surface
  manifest against the new gold applying BOTH settled filter classes (see the
  solutions doc); republish sheets via `eval/publish-gold-evaluation.sh`.
- **Sheets/localStorage, renderer-only rerenders, publish path, add-concept
  flow**: unchanged from prior handoffs — see
  `eval/folio_eval/packet_render.py`, `eval/rerender_sheet.py`, and the cockpit
  board conventions. Decision provenance in `eval/reports/gold_decisions.jsonl`.
- **Machine-local inputs** (runtime files, drivers, vote files, close-call
  queue, salt): paths in the private handoff copy
  (`~/.local/state/ce-handoffs/folio-resolve/`), not here.
- **Plan of record**: `docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md`.
  KD7/R5 annotations should note attempt-0004 when next edited.

## Traps (today's additions to the standing list)

1. **Background waiters that pgrep their own pattern never fire** — quote-proof
   and self-match-proof any liveness check (`pgrep -f` matches the checking
   shell's own command line).
2. **`xargs -I{} bash -c` strips quotes** — paths with spaces (`Coding
   Projects`) make every job die at exit 126. Use a bash runner with `&`/`wait`.
3. **`leakcheck check` exits 0 with collisions found** — read stdout, not the
   exit code.
4. **The baseline SIGTERM is explained** — the u8 agent killed it deliberately
   during the manifest-v3 migration (the run would have failed at report-write
   against the changed manifest). Not the stall-escalation killer this time;
   with concurrent agents, expect deliberate cross-agent kills and check the
   other agent's activity before blaming infrastructure.
5. Standing traps from prior handoffs remain live: buffered logs (0 bytes ≠
   dead), concurrent-session clobber (verify pushes via
   `git show origin/main:<file>`), branch-check before trusting a push, machine
   confidence inversion on gold audit flags.
