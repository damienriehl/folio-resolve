# Recall iteration owner measurement

This is the owner-run gate for folio-resolve PR #2 at commit `26087d1` or a descendant that changes documentation only. The implementation session did not open `eval/data/**`, read gold rows, inspect packets, or run firm scoring.

## Preconditions

- Work from `feat/f1-eval-loop` with a clean tracked tree.
- Confirm `git rev-parse HEAD` is `26087d1` or review every later code change before using these commands.
- Keep `PYTHONHASHSEED=0` for the experiment commands.
- Use gold v3, the version used by attempt 0001. Do not score the frozen slice.
- If gold, ontology, config, or split hashes drift, stop. Do not pass `--rebaseline` or `--allow-ontology-bump` merely to get a run through.

## Procedure

Start attempt 0002 against the existing pipeline. This captures the before scores and opens the experiment record:

```bash
PYTHONHASHSEED=0 uv run python eval/run_experiment.py start \
  --gold eval/data/gold/gold_v3.jsonl \
  --hypothesis "Multi-strategy label, prefix, stem, definition, and ancestor recall will move candidate-gap-unreachable concepts into the committed answer set without a Firm-2 regression." \
  --cluster-targeted candidate_gap_unreachable \
  --cluster-size 337
```

Measure the recall-enabled code on the two allowed iteration slices. These commands write aggregate summaries plus gitignored per-item reports. Do not use `--frozen-final`:

```bash
PYTHONHASHSEED=0 uv run python eval/run_score.py \
  --gold eval/data/gold/gold_v3.jsonl \
  --slice tune \
  --multi-strategy-recall \
  --label attempt-0002-recall

PYTHONHASHSEED=0 uv run python eval/run_score.py \
  --gold eval/data/gold/gold_v3.jsonl \
  --slice firm2 \
  --multi-strategy-recall \
  --label attempt-0002-recall
```

Inspect only the aggregate comparison needed to choose the decision:

- Keep is eligible only if tune F1 improves over `0.208931` and the Firm-2 tripwire remains clear.
- Any Firm-2 correct-to-incorrect item blocks an as-is keep under AE4.
- A flat or lower tune F1 means the recall machinery is a measured no-op or regression at its current defaults. Park or revert it; do not claim improvement.
- Do not score the frozen slice to break a tie. The frozen slice remains reserved for the final report.

Finish the attempt with the chosen decision and an evidence-based reason. The finish command re-scores both slices through the recall-enabled pipeline and computes the authoritative AE4 tripwire:

```bash
PYTHONHASHSEED=0 uv run python eval/run_experiment.py finish \
  --gold eval/data/gold/gold_v3.jsonl \
  --multi-strategy-recall \
  --decision KEEP_REVERT_OR_PARK \
  --reason "REPLACE WITH THE AGGREGATE RESULT AND AE4 RATIONALE" \
  --commit-sha 26087d1
```

Replace `KEEP_REVERT_OR_PARK` with exactly `keep`, `revert`, or `park`. Do not commit a report until the harness surface-leak assertion passes. Share only aggregate results with Codex; keep row-level reports and item identifiers on the approved private surface.

## Release gate

PR #2 is not release-ready merely because the commands complete. A keep decision requires the positive tune movement and clean Firm-2 tripwire above. A final release still follows the F1 plan's check-in and frozen-slice discipline.
