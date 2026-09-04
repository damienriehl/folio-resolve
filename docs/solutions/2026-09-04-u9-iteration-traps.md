---
title: U9 iteration traps — pristine checkpoints, isolated comparisons, and predicate-aware deduplication
lane: evaluation
tags: [eval, synthetic-benchmark, iteration, checkpointing, deduplication]
status: active
related: [docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md, docs/solutions/2026-08-17-leak-gate-owner-scans-and-manifest-regeneration.md]
---

# U9 iteration traps (2026-09-04)

Three problems this repo paid for once; do not pay again.

## 1. The synthetic scorer demands a pristine tree — a filtered pre-check is worse than none

`build_checkpoint_fingerprint` runs `git status --porcelain` and rejects any non-empty output, including ordinary untracked files. On 2026-09-04 a launcher filtered `.codex-out/` from its own dirty check, declared the tree clean, then fanned out eight shards; all eight died at startup because the scorer did not share that exclusion. **Rules:** keep coordination artifacts outside the scoring worktree; make pre-flight cleanliness byte-for-byte equivalent to the scorer's check; and start one shard as a canary, confirm it is past startup, then fan out. The watcher found this within ten minutes only because it searched for `Traceback` and `CheckpointError` as well as progress. A success-only monitor cannot distinguish a crash loop from silence.

## 2. Choose the baseline per metric family, not per experiment

| Comparison | Scored aggregates | Candidate, survivor, and suppression counters |
| --- | --- | --- |
| Provider fix isolated: baseline vs. attempt-0002 | Identical: overall, no-match FP, every depth probe, and every slice | Different: candidates 792136 to 901250 (+13.8%); survivors 409743 to 449399 (+9.7%); suppression counters differ |
| Definition-context tie-break isolated: baseline vs. attempt-0001 | Different: micro-F1 0.007005 to 0.017513 | Identical: candidates 792136; survivors 409743; suppression counters identical |

Each lever is invisible to the metric family the other moves, so one “closest” attempt cannot explain every delta. The stacked attempt-0003 incident exposed the trap: carrying both levers looked flat against one predecessor and not flat against another, which credited a candidate-volume rise already owned by the provider fix to a later change. **Rule:** for each metric family, identify which changes can move it and compare against the attempt that holds every other such change constant; prefer a single-change isolation over a stacked neighbour; and never let one adjacent attempt serve as the baseline for every metric. Protocol wrinkle: `start_attempt` can reuse caller-supplied baseline scores regardless of the kept state, and `stop_status` consumes that recorded delta. A zero-gain change against the kept candidate can therefore look positive against the supplied baseline and fail to advance diminishing returns.

## 3. Filter before deduplicating when eligibility depends on the discarded dimension

Before the repair, `DocumentAdapter.adapt` chose one candidate per IRI before running the alias blocklist, place gate, and short-label gate; all three inspect `surface_term`, which the IRI dedup discarded. A definition-context tie-break could therefore promote a strong-context blocked surface and delete the entire concept even when a sibling anchor would pass. The tie-break did not create the ordering bug; it exposed it. The landed repair groups anchors by IRI, orders each group with `_candidate_key`, runs the gate chain in `_gate_candidate`, and stops at the first eligible anchor. It preserves exactly one trace and at most one suppression-counter increment per IRI, with `raw_candidate_count` still counting unique IRIs; `SCORING_SEMANTICS_VERSION` is 4.

Attempt-0004 versus attempt-0003 measured the defect's full corpus-wide cost: `blocklist` fell from 22 to 0 and `survivor_count` rose by exactly 22, from 449399 to 449421, while `place_gate`, `score_floor`, and `short_label_gate` were unchanged. Exactly 22 concepts had lost their best-ranked anchor to the alias blocklist and then been dropped despite an eligible sibling; all 22 are now recovered. On this corpus the defect manifested only through the blocklist, although all three gates were structurally exposed.

Every scored aggregate nevertheless stayed unchanged: micro-F1 was 0.017513 and predictions were 1350 in both attempts, and both attempts recorded `park`. The recovered concepts either ranked below the cut or were absent from gold, so the headline metric could not see a real correctness fix that restored 22 concepts. **Rule:** a flat benchmark is not evidence that a correctness fix did nothing; judge correctness fixes on mechanism counters and targeted regressions, and record the mechanism evidence when the headline metric cannot see it.
