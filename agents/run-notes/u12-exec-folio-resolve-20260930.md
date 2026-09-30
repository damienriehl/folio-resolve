# U12 folio-resolve local results — 2026-09-30

Branch: `chore/u12-exec-20260930`, base `964791c`. Nothing pushed or merged by this worker. All changes belong on the default branch, separately from the older documentation branch named in the dispatch. Existing task material was retained. Original checkpoint and other checkouts were read-only.

The four rows correspond, in order, to the four on-deck cards in the dispatch. DONE-LOCAL means the bounded local work is finished; it does not certify adoption or live replay.

| Title | Outcome | Commits | Verification (command + result) | Exact next step |
| --- | --- | --- | --- | --- |
| Downstream recall reminder | DONE-LOCAL | 551b145, 3ea8625 | V1: 386 passed; V5: plan scan 0 | Keep Waiting; complete U8, then require R11 per app and flag-off parity. |
| U10 finished shards, failed finalization | SKIPPED | dc6368b | V2: 90/90 valid records, three stacks of 90 rows, no final record | Orchestrator runs the recovery plan in the original candidate checkout; this worker cannot write there. |
| Retrieval-recall loss report | NEEDS-NETWORK | d68101c, 2f06aff | V1: 386 passed; V3: mapper pin verified locally; V5: plan scan 0 | Reconcile run ownership and cumulative spend; follow the U8 plan for model download, authorized arms, and aggregate release. |
| U10 leak triage and replay | DONE-LOCAL | 2e9c34f; dc6368b context | V4: 222 passed; reconstructed scan 1 then 0 excluding only output argument | Review the exact-slot repair, integrate it, then perform the pending replay from the recovery plan. |

The U10 SKIPPED status applies to the real finalize operation, not its completed local inspection. It is a scope boundary, not a new owner-judgment request. No hands-on card was operated. No new approval or spend allowance is inferred.

## Deliverables

- `docs/plans/2026-09-30-downstream-evidence-gated-adoption-plan.md`: durable Waiting gate and shared U8 dependency.
- `docs/plans/2026-09-30-retrieval-loss-live-completion-plan.md`: pin evidence, missing outputs, ownership/budget reconciliation, and exact CLI sequence constraints.
- `docs/plans/2026-09-30-u10-finalization-recovery-plan.md`: stored-artifact evidence, repair semantics, finalize-only command template, restart caveat, wrapper fix context, and rollback.
- `eval/folio_eval/comparison_pilot.py`, `eval/folio_eval/comparison_pilot_repair.py`, `tests/test_eval_comparison_pilot.py`: exact v2 producer-path admission and metadata binding; expanded existing regressions.

## Commands and observed output

V1 / V4 use the already installed project interpreter (`$PROJECT_PYTHON`) with this worktree's source. No package installation was performed. The command wrapper below avoids the pilot's intentional refusal of a mutable import-path environment override:

```bash
PYTHONDONTWRITEBYTECODE=1 "$PROJECT_PYTHON" - <<'PY'
import os, sys
sys.path[:0] = ['src', 'eval']
os.environ.pop('PYTHONPATH', None)
import pytest
raise SystemExit(pytest.main([
    '-p', 'no:cacheprovider', '-q', '--tb=short',
    'tests/test_eval_comparison_pilot.py',
    'tests/test_eval_comparison.py',
    'tests/test_eval_leakcheck.py',
    'tests/test_eval_campaign_report.py',
]))
PY
```

V4 is the exact file list above after the repair: `222 passed in 68.16s`.
V1 used the pilot, leakcheck, campaign-report tests plus the five recall modules (attribution, consumers, report, embedding ceiling, LLM ceiling), before the repair: `386 passed in 74.21s`. Recall code was unchanged. The initial invocation with a PYTHONPATH environment override returned `24 failed, 362 passed`; removing that override resolved those harness failures. No project defect was inferred from that invocation.

Proof-first subset: `pytest tests/test_eval_comparison_pilot.py -k 'repair_entrypoint_binds or finalization_extends' -q`, through the same wrapper:

```text
Before repair: 2 failed, 2 passed, 87 deselected
After repair: 4 passed, 87 deselected
```

The two failures were the rejected v2 output path and its publication scan. Tests still reject custom paths, duplicate output arguments, misplaced copies of approved strings, and metadata mismatch. The final larger run includes the added misplaced-output regression.

V2, read-only Python inspection: parse the original checkpoint manifest; compare it to `_checkpoint_manifest(fingerprint=..., item_ids=...)`; invoke `_load_completed_shard` for every expected item; call `_merge_stack_runs`.

```text
manifest internally consistent; validated shard records: 90
merged stack count: 3
rows per stack: [90, 90, 90]
final record present: False
```

Read-only reconstruction used `build_comparison`, the original bound corpus/config, all validated shard rows, existing stage files, and `_checkpoint_finalization_public_metadata`. It inspected the value passed to `preflight_comparison_publication` without writing any report:

```text
Reconstructed publication collision count: 1
Collision count excluding only output-path field: 0
Runtime not revalidated; no report published.
```

This establishes the failure's location in producer metadata. It does not establish the live runtime binding, independent byte-identical regeneration, or a final R17 verdict. The stored run remains incomplete.

V3: `git log -1 --oneline` on the existing pinned app worktrees showed mapper `162bcd43` and enrich `bb576ac`. `git merge-base --is-ancestor 162bcd43 origin/main` in mapper returned 0. Both required U8 outputs were absent in this branch and the inspected recall-run worktree. That worktree is at the older `191857a`; do not run current U8 from it without orchestrator preparation. These are local-history observations, not remote checks. No budget or active-run claim is made.

Cockpit context: `git merge-base --is-ancestor c3d59a89 master` returned 0. Its committed wrapper-fix note records two failing blocked/failed cases before repair and `55 passed` afterward. Those external tests were not rerun here; durable notification/restart/dedup validation is not newly claimed.

Other verification:

```text
ruff check <two changed modules> tests/test_eval_comparison_pilot.py
All checks passed!

mypy --python-version 3.13 <two changed modules>
Success: no issues found in 2 source files

mypy <two changed modules> (configured Python 3.11 target)
Blocked before project checks: installed NumPy stubs require newer type syntax.

git diff --check <owned paths>
exit 0
```

V5 uses `load_manifest` and `scan_text` with the existing salt passed only in process memory. The three final plan documents and this note are scanned individually. Final counts are recorded below after the last scan. Earlier local documentation commits triggered prose matches; their follow-up commits correct them. Integrate the final documentation snapshots together, not the earlier versions alone. No restricted source passages or matching strings were logged. This scan does not certify the pending real report. No new tests were needed for the documentation itself.

## Return to orchestrator

Native Codex; no subagents or external model. The orchestrator owns review, live replay, U8, and release. Local fix and evidence are ready. No stop request found. Required heartbeat updates succeeded using scoped sandbox escalation.

Final document scans: all four files report collisions=0.
