---
artifact_contract: "ce-handoff/v1"
title: "OpenGloss + FOLIO span tagging: plan shipped, U1-U4 in progress"
summary: "folio-resolve R6/R7/R10 span tagging; plan committed and reviewed, a worker builds U1-U4, U5 and shipping remain"
keywords: ["opengloss", "folio", "span-tagging", "sssom", "lexplicit", "ce-work"]
resume_focus: "Inspect the U1-U4 output, commit per unit, then U5, Codex review, PR, merge, tag v0.5.0, tell lexplicit"
repository: "damienriehl/folio-resolve"
---

# OpenGloss + FOLIO span tagging — handoff

## Objective

Damien asked (2026-11-08) for folio-resolve's part of the cross-repo OpenGloss and FOLIO anchoring plan (lexplicit's R6, R7, R10), built autonomously through review, PR, merge, and release, with the release tag sent to the lexplicit session.

## State

- **Plan:** `docs/plans/2026-11-08-1216-feat-opengloss-folio-span-tagging-plan.md`, reviewed (lite doc review, five fixes applied, none open).
- **Dependencies:** `pyproject.toml` gained the optional `opengloss` extra (pyarrow, build-time only) and a dev pin; `uv.lock` refreshed.
- **Units U1-U4:** being implemented by a worker; output lands uncommitted, then the orchestrator inspects, tests, and commits per unit.
- **Not started:** U5 (real-data budget run, README, version 0.5.0), the Codex review receipt required by `CLAUDE.md`, PR, merge, tag, and the lexplicit notice.

## Agreements with other sessions

- **FOLIO session:** confirmed the SSSOM crosswalk contract recorded in the plan's Sources and U3 (file `mappings/opengloss/folio-opengloss.sssom.tsv` in the FOLIO repo, not yet merged; `OGS:` expansion read from `curie_map`). It will report the merge commit. Plan R6 extends links to senses on contained spans; tell that session.
- **lexplicit session:** receives the release tag.

## Hazards

- **Empty-branch worktrees disappear:** a worktree on a branch with no commits beyond `main` was removed twice within minutes. Commit early.
- **Heavy host load:** under memory pressure the host's pacing guard freezes new jobs, which killed the first worker attempt and the baseline test runs; the worker was relaunched once pressure cleared. Run targeted tests locally and the full suite in CI.
- **Parquet reads:** read projected light columns only; converting whole tables to Python objects is very slow.
