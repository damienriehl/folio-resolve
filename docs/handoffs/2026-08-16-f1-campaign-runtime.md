---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-16T23:45:00Z"
title: "F1 campaign — implementation complete, corpus-v1 runtime mid-flight"
summary: "All 12 units built/reviewed/pushed with 3 PRs open; corpus v1 is folded (255/270 provisional gold) and building through the leak gate; next: commit corpus, baseline, 30-item pilot."
repository: "folio-resolve"
branch: "feat/f1-campaign-execution (c53c7ec)"
resume_focus: "Finish corpus-v1 build -> commit -> synthetic baseline -> pilot comparison; fold Damien's completed Gold analysis and regenerate the surface manifest + evaluation page."
---

# F1 campaign — runtime handoff

Plan: `docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md` (implementation-ready; all KDs/KTDs settled and annotated).

## Done and durable
- U1–U12 implemented via Codex workers, reviewed (5 personas + independent cross-model pass), 23 review fixes applied; gates green (979 passed). Branch `feat/f1-campaign-execution`; PRs: folio-resolve #8 (supersedes #7), folio-enrich #34, folio-mapper #8 — Known Residuals recorded in the PR bodies (module splits, scrypt throughput, segments-echo, generate --allowlist flag).
- Grader amendments (Damien-approved, cockpit ask `folio-resolve-2026-08-16-corpus-v1-runtime-decisions`): identity-level self-grading exclusion + ≥2 families; per-concept agreement fold.
- Leak-collision ruling: all 105 collisions allowlisted as generic legal vocabulary; committed manifest filtered to 3,493 digests. `generate` does not yet accept an allowlist — re-apply the filter (or add the flag) after any manifest regeneration.
- Evaluation surface: the Cockpit board page is canonical; `eval/publish-gold-evaluation.sh` re-renders + republishes it. Re-run after every gold fold.

## Mid-flight (machine-local; paths in the private handoff copy)
- Corpus v1 build running detached (fold + build through the leak gate; slow scrypt path). On completion: corpus/nomatch/manifest land in `eval/synthetic/` of the live-data worktree → copy to the canonical checkout, commit to the campaign branch.
- Fold state: 255/270 items provisional gold, 30 no-match confirmed, close-call queue = 15 set-mismatch items + 680 singleton concepts (additive) — render as synthetic-lane sittings after Gate 1b priority clears (KTD8).
- Then: `eval/run_synthetic.py` baseline → committed `synthetic-baseline-v1.json`; pilot comparison via the downstream harness `--limit 30` before any U9 iteration.

## Damien-state
- Gold analysis finished in another session: fold those decisions (fold-v2), which bumps gold → regenerate split manifest, **regenerate the surface manifest (KTD4 gold-version binding, then re-filter the allowlist)**, republish the evaluation page.
- Provisional marks open: runtime-decisions q5–q8 (audit sitting, floor calibration, no-match slice, ablation gate) — folded into the plan on recommendations.
