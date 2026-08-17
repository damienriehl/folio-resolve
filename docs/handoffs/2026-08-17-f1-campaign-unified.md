---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-17T14:05:00Z"
title: "F1 campaign unified — gold v7 folded, corpus build 5 in flight, calibration next"
summary: "Single continuation doc merging the 08-16 runtime and 08-17 gold-adjudication handoffs: adjudication is DONE (gold v7), corpus-v1 build attempt 5 is running detached, next work is answer-rule calibration on tune, then corpus commit -> synthetic baseline -> 30-item pilot -> recall against the v7 fn pool."
keywords: ["folio-resolve", "f1-campaign", "gold-v7", "calibration", "recall", "synthetic-corpus", "leak-gate", "baseline", "pilot"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Calibrate the answer rule on tune against gold v7 while corpus build 5 finishes; then commit corpus v1, run the synthetic baseline and 30-item pilot, then start pipeline recall work"
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb82"
branch: "main"
head: "9dd20d5"
---

# F1 campaign — unified continuation (2026-08-17)

**Supersedes and merges** `docs/handoffs/2026-08-16-f1-campaign-runtime.md` and
`docs/handoffs/2026-08-17-gold-adjudication-v7-continuation.md`. Their one open
dependency on each other is resolved: the runtime handoff's "blocked on Damien's gold
analysis" is DONE — the full adjudication is folded into **gold v7**. When this handoff
has been consumed (new session resumed, tasks done, learnings landed), the receiving
session retires all three per the two-copy handoff rule.

State below was re-verified on this box at 2026-08-17 ~09:00 CDT by the orienting
session. Concurrent sessions are active in this checkout — re-verify before trusting.

## Where the campaign stands

- The human-adjudication phase is **complete and folded**: `eval/data/gold/gold_v7.jsonl`,
  id `v7-1e8e06748af2`, `damien_corrected: 119`. Lineage v5→v6 (atomic-unit contamination
  pass: 54 removes / 106 elevates / 82 adds) →v7 (69 pairing rulings via the contract
  adapter). Split manifest: `split_manifest_v6.json`.
- **Score baselines committed**: `eval/reports/score-v6-*-tune-v6-post-fold.json` and
  `score-v7-*-tune-v7-post-pairing.json`. v6→v7 reads P +0.5 / R −1.4 / F1 −0.5 — **not a
  regression**: the rulings added ~115 tune-slice gold entries the pipeline never predicts
  (fn 968→1076 while tp rose and fp fell). That fn pool is the recall target.
- **Campaign fully merged to `main` in all three repos**: folio-resolve PR #8 → `81da330`;
  folio-enrich PR #34 → `0dcce95`; folio-mapper PR #8 → `af4a7649`. `main` here is at
  `9dd20d5`; the resolved-label provenance fix that unblocked the corpus builder is
  `51b5360`.
- **Corpus v1 build attempt 5 is RUNNING detached** (verified via pgrep at snapshot time).
  Machine-local: log `~/worktrees/build5.log` (0 bytes so far — output is buffered;
  empty ≠ dead, check the process), driver `~/worktrees/refilter-build.py`, live-data
  worktree `~/Coding Projects/.worktrees/folio-resolve-f1` (detached HEAD at origin/main).
  Attempt 4 failed on incomplete resolved-label provenance, fixed at `51b5360`; the leak
  gate has passed clean since attempt 4. On success it writes `corpus_v1.jsonl`,
  `nomatch_v1.jsonl`, `corpus_v1.manifest.json` into that worktree's `eval/synthetic/`
  (not present yet — only `firm-surface-manifest-v1.json` is).

## Next steps, in order

1. **Calibrate the answer rule** (does NOT wait on the corpus build): `eval/run_score.py`
   prints `UNCALIBRATED (threshold=…, k=…) — U4 fits it on tune items only` on every run.
   Fit on tune against gold v7. This gates every downstream number.
2. **When build 5 completes**: verify outputs, copy the three files to the canonical
   checkout, commit to `main` together with the filtered `firm-surface-manifest-v1.json`.
   Fold profile for reference: 255/270 provisional gold, 30 no-match confirmed;
   close-call queue = 15 set-mismatch + 680 singleton concepts
   (machine-local `~/worktrees/close-calls-v2.json`) → render as synthetic-lane sittings
   only after Gate 1b clears (KTD8 precedence).
3. **Synthetic baseline** via `eval/run_synthetic.py` → committed
   `synthetic-baseline-v1.json`; then the **30-item pilot** comparison via the downstream
   harness before any U9 iteration.
4. **Pipeline recall work** against the fn pool v7 exposed. Compare per-stratum blocks in
   the v6/v7 score summaries to find where the added gold concentrates.
5. **Open provisional marks**: runtime-decisions ask
   `folio-resolve-2026-08-16-corpus-v1-runtime-decisions`, q5–q8.

## Folding future sittings (recipe)

- Non-pairing decisions fold directly:
  `uv run python eval/run_audit.py --mode fold-v2 --gold <latest> --split-manifest <latest>
  --clusters eval/data/reports/clusters_v2.jsonl --lane firm --decisions <file>`.
- Pairing verdicts from a checkbox-format sheet must first pass through
  `eval/adapt_v3_diff_verdicts.py` (checkbox export → `edited_iris` contract; rows folding
  to nothing means re-affirmation — correct, not a bug).
- After ANY fold: re-score with `--build-splits` on a NEW split manifest (the invariant
  refuses a stale one); regenerate the surface manifest (KTD4 gold-version binding) and
  **re-apply the leak allowlist ledger manually** — `leakcheck generate` does not yet take
  `--allowlist`. Ledger (machine-local, gitignored): `eval/data/leak-allowlist-v1.txt` in
  the f1 worktree (126 entries). Then `eval/publish-gold-evaluation.sh` and re-render with
  labels.
- Leak-gate allowlist rulings are settled — do not re-litigate: 105 generic words + 4
  doc-type phrases + all public-FOLIO-label digests + 17 gold-label sub-grams, all ruled
  generic/public by Damien. Committed manifest holds 3,047 firm-specific digests.

## Working knowledge NOT in code comments

- **The review sheets have no server.** Decisions live only in the reviewer's browser
  localStorage, keyed `folio-eval-draft:<packet-key>` (gold id + row count + content
  fingerprint). Republishing over a re-derived packet mints a new key and strands the open
  sitting — the sheet's recovery button and `eval/consolidate_answers.py` exist for this.
  Damien reviews from multiple machines; the only durable copy is his Download-JSON
  export. Live workspace:
  https://dashboard.damienriehl.com/folio-resolve-gold-evaluation.html — packet key
  `v7-1e8e06748af2|ontology-unknown|310|05e58d1617953c68`, folded rulings arrive
  pre-filled.
- **Renderer-only changes** ship via `eval/rerender_sheet.py <packet.json> <out>
  [labels.json]` — byte-identical packet key, so open drafts survive.
  `eval/build_folio_labels.py` regenerates the 18k-label index after an ontology bump.
- **Publish path**: copy sheet to the cockpit board dir (machine-local
  `~/Coding Projects/cockpit/briefs/board/<name>.html`), run `~/.local/bin/sync-cockpit.sh`
  from the cockpit repo, verify at origin and compare md5 server-vs-local. Hand Damien only
  the stable root URL, never a `/releases/<id>/` link (evicted).
- **Add-concept flow** is IRI-only (label auto-fills from the embedded index; typed label
  wins; leaf level pre-selects per the atomic-unit rule).
- **Decision provenance**: `eval/reports/gold_decisions.jsonl` (committed);
  `eval/data/gold/folded_v6.json` / `folded_v7.json`; raw sittings under machine-local
  gitignored `eval/data/reports/audit_packet_v2/collected-answers-2026-08-16/`.
- **Runtime inputs** (machine-local `~/worktrees/`): `all-passages.jsonl` (270 passages,
  generator manifests in `gen-a..d/`), grader vote files
  (`grader-cx1|cx2|claude/votes.json`), fold/build drivers, `folio-dictionary.jsonl`.

## Traps prior sessions hit (do not re-derive)

1. **Concurrent-session clobber**: a landed commit was later overwritten wholesale by a
   session on a stale base; recovery was cherry-pick. After any push, grep a distinctive
   marker of your change on the remote ref (`git show origin/<branch>:<file>`), not just
   `git log`.
2. **Silent no-op push**: after the PR merge switched this checkout to `main`, a
   `git push origin feat/…` said "Everything up-to-date" while three commits sat unpushed
   on `main`. Check `git branch --show-current` before trusting a push.
3. **Machine self-assessed confidence is inverted here**: an LLM audit pass over gold
   scored 100% precision on its "low"-confidence flags and 59% on "high". Rank machine
   proposals by human-authored signal, never raw counts (a 104-entry sitting held zero
   human decisions — `collect()` emits machine state for every unfolded row).
4. **Corpus build logs buffer**: `build5.log` sat at 0 bytes while the process ran fine.
   Judge liveness by pgrep/output files, not log growth.
5. Known pre-existing on `main`: mypy fails on four gold-analysis helper scripts from a
   parallel session — not this thread's to fix unless adopted deliberately.

## Read before acting

- `eval/folio_eval/packet_render.py` — the whole workspace (JS in `_SCRIPT_V2*`
  constants); tests assert over generated strings, so behavioral claims need a browser
  check.
- `eval/folio_eval/audit.py:3086` — `fold_granular_decisions`, esp. the pairing branch
  (`edited_iris` replaces a row's own per-item contribution; silence never deletes gold).
- Plan of record: `docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md`
  (all KDs/KTDs settled and annotated).
- Cockpit `docs/solutions/` — release-URL and push-verification learnings.

## Out of scope for this thread

- Cockpit decision-receipt sweep failures in the sync log (recurring as of 2026-08-16) —
  another session's to own.
- folio-enrich proposition-system Phase A work — lives in its own session/branch
  (`feat/proposition-system-phase-a`).
