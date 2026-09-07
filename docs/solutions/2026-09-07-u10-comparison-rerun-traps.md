---
title: U10 comparison rerun traps — ignored outputs, optional dependencies, and candidate identity
lane: evaluation
tags: [eval, synthetic-benchmark, comparison, checkpointing, reproducibility]
status: active
related: [docs/plans/2026-09-06-001-eval-u10-comparison-v2-and-u13-closeout-plan.md, docs/solutions/2026-09-04-u9-iteration-traps.md]
---

# U10 comparison rerun traps (2026-09-07)

Three problems this repo paid for once; do not pay again.

## 1. A new published-report name is not a writable run output

**Symptom:** The runner rejects `--out eval/reports/synthetic-comparison-v2.json` before any shard
runs. **Cause:** `_assert_write_paths_are_safe` requires outputs inside each repository to be
Git-ignored (`eval/folio_eval/comparison_pilot.py:1005-1045`), and the only candidate-report
exemption is the hardcoded v1 path (`eval/folio_eval/comparison_pilot.py:54,1024-1029`). **Fix:** Run
with the ignored output at `eval/data/reports/synthetic-comparison-v2.json` (`.gitignore:21`), then
copy the byte-verified report to
`eval/reports/synthetic-comparison-v2.json` during finalization and mirror the v1 LFS rule in
`.gitattributes`. **How to tell next time:** Before launch, run `git check-ignore` on every in-repo
writable path and confirm that any claimed exemption exactly matches the constant in the runner.

## 2. A plain environment sync omits a fingerprint dependency

**Symptom:** A fresh execution worktree reaches fingerprint construction and fails because the
`folio-python` distribution is absent. **Cause:** `folio-python` belongs to the optional `folio`
extra (`pyproject.toml:32-34`), while the fingerprint unconditionally reads its installed version
(`eval/folio_eval/comparison_pilot.py:1465-1467`). **Fix:** Prepare comparison worktrees with
`uv sync --extra folio`. **How to tell next time:** Before launch, query the distribution version
with the execution worktree's Python and require that probe to pass alongside the import preflight.

## 3. A finalized comparison belongs to the candidate commit it measured

**Symptom:** A finalized report is reproducible and internally valid, yet cannot support the current
candidate's adoption verdict. **Cause:** v1 measured the candidate before the kept local-window
tie-order change, even though later attribution identified that change as the causal lever for the
loss (`docs/plans/2026-09-06-001-eval-u10-comparison-v2-and-u13-closeout-plan.md:36-47`). **Fix:** Rerun
the same comparison inputs against the kept candidate and cite that new report; do not reinterpret
v1. **How to tell next time:** Before using a report for attribution or adoption, match its recorded
candidate commit to the exact kept state whose behavior the conclusion names.
