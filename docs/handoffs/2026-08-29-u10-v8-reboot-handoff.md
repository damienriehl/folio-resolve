---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-29T15:22:37Z"
title: "U10 v8 reboot-safe comparison-pilot handoff"
summary: "Resume the fingerprint-bound U10 v8 comparison pilot after a host reboot, then continue the evidence-gated U9/U12/U13 queue."
keywords: ["u10", "comparison-pilot", "checkpoint", "reboot", "ce-work"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Revalidate and resume U10 v8 from its existing checkpoint without changing any pinned worktree, then interpret the finalized aggregate report and continue the established autonomous queue."
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb820c0cee6aa2721b872790c09ad0c5"
branch: "docs/u10-v8-reboot-handoff-2026-08-29"
head: "e7e08702733e46b668032a7ee1b1be9330a0c1d9"
worktree_path: "/home/damienriehl/Coding Projects/folio-resolve/.worktrees/docs/recent-plan-audit"
---

# U10 v8 reboot-safe comparison-pilot handoff

## Objective and user intent

The user asked the agent to finish the established recent-plan autonomous queue, push and merge
appropriate changes, preserve checkpoints, and save owner-only judgment calls for later. The user
now needs to reboot the host and asked for durable continuity using `ce-handoff` best practices.

The current dependency order remains:

1. finish and interpret U10 v8;
2. use its aggregate result to select the U9 shared-scope iteration or the U12 attribution path;
3. continue U9 through its guarded stop/checkpoint;
4. assemble U13 up to its owner-only gates;
5. validate, ship, and update the completion audit and Decision Sheet.

## Authoritative state at capture

- The live serial U10 v8 run had **at least 27 of 60** completion receipts and 27 reports when this
  handoff was captured. Shard 28 had initialized. The live runner may advance further before the
  reboot.
- The v8 run passed the historical shard-24 failure frontier with no checkpoint, comparison,
  leak-scan, or collision errors.
- The authoritative checkpoint is
  `eval/data/u10-comparison/pilot-checkpoint-v8` in the execution worktree below. It is ignored,
  fingerprint-bound, corruption-detecting, and durable across reboot.
- The canonical final target remains `eval/reports/synthetic-comparison-v1.json`. It should not
  exist until all 60 shard receipts are valid and finalization succeeds.
- A reboot kills only the active process and any incomplete shard. Every published completion
  receipt remains reusable; rerunning the same command validates the fingerprint and skips completed
  shards.
- The machine-local `/tmp/u10-v8-init.h1ijoh/` logs are diagnostic only and may disappear on reboot.
  They are not recovery state.

Do not print or copy synthetic item IDs, protected surfaces/passages, salts, matched digests, or raw
report rows. Structural counts and finalized aggregate metrics are sufficient.

## Pinned machine-local worktrees

All three were clean when this handoff was captured. Do not update, checkout, merge, or otherwise
mutate them before v8 finalizes.

- Candidate/execution worktree:
  `/home/damienriehl/Coding Projects/folio-resolve/.worktrees/fix/u10-public-metadata-exemption`
  at detached commit `db1cf4c99be859de15bd40d7497a445c3b48c4c8`.
- Mapper worktree:
  `/home/damienriehl/Coding Projects/folio-resolve/.worktrees/external/folio-mapper-u10-canonical-iris/.worktrees/external/folio-mapper-deterministic-cpu`
  at commit `626412bbec571308a0ef9293923c39de54d65c5f`.
- Enrich worktree:
  `/home/damienriehl/Coding Projects/folio-resolve/.worktrees/external/folio-enrich-u10-runner`
  at commit `bb576ac0830308091623018aa1546559ac4fa1a9`.

The local leak-check salt file is machine-local at
`/home/damienriehl/Coding Projects/folio-resolve/eval/data/leakcheck-salt`. Never print its contents
or digest.

## Resume procedure after reboot

First confirm that the execution and both consumer worktrees still match the commits above and have
empty `git status --porcelain=v1 --untracked-files=all` output. Count checkpoint progress only by
counting `complete.json`, `report.json`, and `items.jsonl` files; never print their parent directory
names.

Then run this command with ordinary owner filesystem access. It intentionally unsets `PYTHONPATH`,
uses the repository wrapper, and reuses the existing v8 checkpoint:

```bash
cd '/home/damienriehl/Coding Projects/folio-resolve/.worktrees/fix/u10-public-metadata-exemption'
env -u PYTHONPATH \
  PYTHONHASHSEED=0 \
  PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -B eval/run_comparison_pilot.py \
  --corpus-manifest eval/synthetic/corpus_v1.manifest.json \
  --config eval/synthetic/answer_rule_config_synthetic_v1.json \
  --out eval/reports/synthetic-comparison-v1.json \
  --checkpoint-dir eval/data/u10-comparison/pilot-checkpoint-v8 \
  --leak-manifest eval/synthetic/firm-surface-manifest-v1.json \
  --salt-file '/home/damienriehl/Coding Projects/folio-resolve/eval/data/leakcheck-salt' \
  --public-metadata eval/synthetic/public_comparison_metadata_v1.json \
  --mapper-root '/home/damienriehl/Coding Projects/folio-resolve/.worktrees/external/folio-mapper-u10-canonical-iris/.worktrees/external/folio-mapper-deterministic-cpu' \
  --enrich-root '/home/damienriehl/Coding Projects/folio-resolve/.worktrees/external/folio-enrich-u10-runner' \
  --limit 60
```

The consumer adapters may emit optional-provider-key tracebacks in the deterministic no-LLM lane;
those are expected when the runner exits successfully. Treat `PilotCheckpointError`,
`ComparisonError`, `LeakcheckError`, `collision`, or a nonzero runner exit as real failures. Do not
launch a duplicate runner.

## Completed repair and validation

- PR [#20](https://github.com/damienriehl/folio-resolve/pull/20) merged the structured-artifact
  leak-scan repair as `db1cf4c99be859de15bd40d7497a445c3b48c4c8`.
- The exact previously failing shard replay passed end to end. The merged change passed the full
  suite (`1156 passed, 2 skipped`), 72 focused tests, Ruff, changed-module mypy, and diff checks.
- v6 and v7 remain preserved failed evidence and must not be resumed. v7 stopped after 23 valid
  receipts because its next shard exposed the stage-snapshot whole-serialization false-positive.
- The broader plan status, residual queue, and owner Decision Sheet are in
  `docs/handoffs/2026-08-20-recent-plan-completion-audit.md`; PR
  [#21](https://github.com/damienriehl/folio-resolve/pull/21) records the v8 recovery decision.

## After v8 finalization

Inspect only finalized aggregate metrics:

- clear pilot go: begin U9 with synonym/definition-side matching and ranking only; do not widen
  retrieval;
- in-band hold or material incumbent win: add the exact aggregate comparison to the Decision Sheet;
- incumbent-winning cohort: perform the U12 controlled replay/ablation before naming a port
  candidate.

Owner-only items remain deferred: the U7 consensus-audit sitting, U11 provider-key lanes or explicit
skip decisions, the U9 interim firm checkpoint, the U13 final firm exam, and protected-originals
disposal.

## Continuity warnings

- The checkpoint is durable only on this host unless separately backed up; it is intentionally
  ignored and is not pushed to GitHub.
- The handoff is durable in Git after its focused branch is pushed/merged, but it does not contain
  checkpoint payloads or secrets.
- Resume must fail closed if any fingerprint-bound input or pinned worktree changed. Do not bypass
  that validation or start a fresh checkpoint merely to make the command run.
