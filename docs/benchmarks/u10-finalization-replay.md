# Verify U10 finalization replay

This procedure supports the offline portions of units 3–6 in
[`2026-09-30-u10-finalization-recovery-plan.md`](../plans/2026-09-30-u10-finalization-recovery-plan.md).
The generated fixture proof does not establish original-input replay, current original-runtime
agreement, a measured R17 verdict, retrieval R11 adoption, or campaign completion.

## Two operations

An ordinary `--finalize-only` invocation builds a report when its final receipt is absent.
When that receipt exists, the ordinary invocation validates it and returns without rebuilding.

Add `--verify-replay-dir` to the isolated repair invocation to independently regenerate the
items, all three stage artifacts, report and final receipt. Replay compares exact bytes with
the authenticated baseline. The original logical invocation and artifact paths remain in
provenance; only the physical write destinations change. Metrics equality alone is insufficient.

Replay requires the same clean repair identity that produced the first finalization. It does
not reinterpret an older repair's receipt as new execution. If a baseline was produced by
another repair commit, preserve it and reconcile through the controller; do not edit its
identity, stored fingerprint, or receipt to admit the new code.

## Controller prerequisites

Resolve the original values from the preserved run configuration, privately. Required roles:

- Original candidate checkout and interpreter, locale, input manifests, answer-rule config,
  metadata, salt-file reference and limit.
- Pinned consumer checkouts and unchanged runtime/model caches.
- Complete original checkpoint and baseline artifacts, with a verified backup.
- A clean, reviewed repair checkout separate from the candidate.
- A new replay destination whose existing parent is outside every candidate, repair, consumer
  and runtime root, the checkpoint, outputs and bound inputs. Neither it nor its ancestors may
  be symlinks. The destination must not already exist; each retry uses a new directory.

Before reserving that directory, read-only probes discover each candidate and consumer
interpreter's prefixes, import paths and model-cache root. Replay excludes those roots and
the shared FOLIO cache. This discovery does not change the stored fingerprint format or
load a model for scoring.

The lane does not acquire these inputs, inspect their contents, prepare consumers, run
providers, download models, or overwrite the preserved environment.

## Invocation

Use the September 30 command for first finalization. Before that command, inventory and back up
existing outputs. An orphan report without a final receipt remains a controller recovery case:
ordinary finalization can overwrite it, while explicit replay refuses it.

After the first successful finalization, run the same logical command with one additional option:

```bash
env -u PYTHONPATH PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 \
  LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 LC_CTYPE=en_US.UTF-8 \
  "$CANDIDATE_ROOT/.venv/bin/python" -I -B \
  "$REPAIR_ROOT/eval/run_comparison_pilot_repair.py" \
  --candidate-root "$CANDIDATE_ROOT" --finalize-only \
  --corpus-manifest "$CORPUS_MANIFEST" --config "$ANSWER_CONFIG" \
  --leak-manifest "$LEAK_MANIFEST" --salt-file "$SALT_FILE" \
  --public-metadata "$PUBLIC_METADATA" \
  --mapper-root "$MAPPER_ROOT" --enrich-root "$ENRICH_ROOT" \
  --checkpoint-dir "$CHECKPOINT_ROOT" --out "$V2_OUTPUT" --limit 60 \
  --verify-replay-dir "$REPLAY_DIR"
```

Do not point `--checkpoint-dir` or `--out` at scratch paths: doing so changes logical bindings.
Replay accepts only the repair launcher's existing canonical output paths. It does not add an
output exemption to the public-metadata policy.

## Terminal states

| State | Explicit replay result |
| --- | --- |
| Missing manifest or final report/receipt | Refuse before staging. |
| Damaged shard/report/receipt, incomplete artifact inventory, changed digest/size/path, or repair mismatch | Refuse before staging. |
| Existing, overlapping or symlinked destination | Refuse without overwriting it. |
| Byte mismatch, source/runtime drift, or interruption during regeneration | Fail; retain partial replay files for inspection and leave no replay completion receipt. |
| Exact regeneration and successful final integrity checks | Return success with a regenerated `final-complete.json` and byte-identical artifacts. |

Replay never writes to the baseline. Its checks detect source or baseline drift; they do not
restore evidence changed by another process. A `reconstructed-receipt.json` left in a failed
attempt is diagnostic output, not successful verification. Keep failures and original evidence
until the controller has verified a backup and decided their disposition.

## Offline fixture verification

Use a provisioned development interpreter; do not synchronize the original candidate runtime.

```bash
UV_OFFLINE=1 uv run --no-sync pytest -q tests/test_eval_u10_finalize_replay.py
UV_OFFLINE=1 uv run --no-sync ruff check \
  eval/folio_eval/comparison_pilot.py \
  eval/folio_eval/comparison_pilot_repair.py \
  tests/test_eval_u10_finalize_replay.py
UV_OFFLINE=1 uv run --no-sync mypy
```

The new module generates corpus/config/metadata/leak inputs and shard receipts. It uses the real
corpus loader, receipt validation, finalizer, serializers and scanner. Runtime/consumer identity
seams are substituted because the original pinned environments are not available. Its access
guard applies during collection and its own test execution, without changing other test modules.

Campaign tests feed replay-verified generated output through the real campaign loader, renderer
and CLI. Only fixture discovery paths and digest pins are replaced. The tests retain local-gold
freshness, manifest pin, collision and item-ID checks, and characterize loss/win/hold and rounded
zero boundary verdicts. No production campaign policy changes.

Do not collect `tests/test_eval_campaign_report.py` in this restricted lane: its import reads
committed evaluation inputs before test selection. The controller retains the full unchanged CI
suite and original-data publication scan.

## Acceptance beyond this branch

The [history receipt](../plans/evidence/fr-1-u10-reconciliation.md) identifies contained source and
unavailable branch tips. The controller supplies exact missing refs/history, verifies the chief
campaign authority, performs the preserved-input replay and original-data leak acceptance, then
regenerates and routes the actual aggregate campaign report. Future public v2 publication must
have the required LFS tracking, analogous to v1, before it is committed. No measured report or
adoption decision is published by the fixture tests.

On a local code rollback, revert the fr-1 implementation commits without deleting checkpoints or
replay directories. Baseline and partial evidence remain independent of the source rollback.
