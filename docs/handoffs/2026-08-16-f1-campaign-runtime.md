---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-17T11:10:00Z"
title: "F1 campaign - merged to main; corpus-v1 build in flight; Gold fold pending"
summary: "All campaign PRs merged (resolve 81da330, enrich 0dcce95, mapper af4a7649); corpus v1 building through the leak gate after three allowlist rounds; next: build result -> commit corpus -> baseline -> pilot; Damien still finishing the Gold analysis in another session."
keywords: ["folio-resolve", "f1-campaign", "synthetic-corpus", "leak-gate", "allowlist", "baseline", "pilot", "gold-fold"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Verify corpus-v1 build attempt 4, commit the corpus, run the synthetic baseline and 30-item pilot; then fold Damien's finished Gold analysis and regenerate the surface manifest, split manifest, and evaluation page."
repository: "folio-resolve"
branch: "main"
head: "81da330"
---

# F1 campaign - runtime handoff (updated after merge)

Plan: `docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md` (all KDs/KTDs settled and annotated). Prior version of this handoff is in git history; this update supersedes it.

## Merged and durable (verify with git, not this doc)
- Campaign merged to `main` in all three repos: folio-resolve PR #8 -> `81da330`; folio-enrich PR #34 -> `0dcce95` (Railway DEV auto-deploys from enrich main - additive eval tooling, no served-path impact); folio-mapper PR #8 -> `af4a7649`. folio-resolve PR #7 closed as superseded. Old eval branches (`feat/f1-eval-loop`, `feat/visual-eval-review`) and the in-repo visual worktree deleted per the plan's DoD.
- Grader amendments (identity-level self-grading exclusion, >=2 families; per-concept agreement fold) are on main - both Damien-approved (cockpit ask `folio-resolve-2026-08-16-corpus-v1-runtime-decisions`).
- Evaluation surface: the Cockpit board page is canonical; `eval/publish-gold-evaluation.sh` re-renders + republishes it after every fold.

## Leak-gate allowlist saga (resolved; do not re-litigate)
Three collision rounds, all ruled by Damien as generic/public vocabulary: 105 generic words + 4 doc-type phrases + all public-FOLIO-label digests (425, class ruling: public-standard labels are definitionally public) + 17 gold-label sub-grams ("contract", "privacy", "power of attorney", ...). Committed manifest now holds 3,047 firm-specific digests. The durable ledger is machine-local: `eval/data/leak-allowlist-v1.txt` (126 entries, gitignored, in the live-data worktree). **Residual:** `leakcheck generate` must learn `--allowlist` so manifest regeneration (required after every gold fold, KTD4 binding) re-applies the ledger; until then, re-filter manually after regenerating.

## In flight (machine-local; paths are on this box)
- Corpus v1 build attempt 4 RUNNING detached: log `~/worktrees/build4.log`, driver `~/worktrees/refilter-build.py`, live-data worktree `~/Coding Projects/.worktrees/folio-resolve-f1` (detached HEAD, campaign code). On success it writes `corpus_v1.jsonl`, `nomatch_v1.jsonl`, `corpus_v1.manifest.json` into that worktree's `eval/synthetic/` -> copy to the canonical checkout, commit to `main` with the filtered `firm-surface-manifest-v1.json`. Fold profile: 255/270 provisional gold, 30 no-match confirmed, close-call queue = 15 set-mismatch + 680 singleton concepts (`~/worktrees/close-calls-v2.json`) -> render as synthetic-lane sittings after Gate 1b clears (KTD8 precedence).
- Then: synthetic baseline via `eval/run_synthetic.py` -> committed `synthetic-baseline-v1.json`; 30-item pilot comparison via the downstream harness before any U9 iteration.
- Runtime inputs (machine-local `~/worktrees/`): `all-passages.jsonl` (270 passages, 4 generator manifests in `gen-a..d/`), three grader vote files (`grader-cx1|cx2|claude/votes.json`), fold/build drivers, `folio-dictionary.jsonl`.

## Blocked on Damien (in progress in his other session)
Gold analysis fold: when he finishes, run fold-v2 -> gold vNext, regenerate the split manifest, regenerate the surface manifest (KTD4 gold-version binding) and re-apply the allowlist ledger, then `eval/publish-gold-evaluation.sh`. Provisional marks still open: runtime-decisions q5-q8.
