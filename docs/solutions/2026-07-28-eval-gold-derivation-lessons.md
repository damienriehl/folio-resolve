---
title: Eval design lessons from the F1 loop's Gate 1 — answer keys, join keys, and human gates
lane: evaluation
tags: [eval, gold-data, f1, human-in-the-loop, data-privacy]
status: active
related: [docs/plans/2026-07-27-001-feat-f1-improvement-loop-plan.md, docs/handoffs/2026-07-28-f1-loop-gate1b-handoff.md]
---

# Eval design lessons from Gate 1 (2026-07-28)

Three problems this repo paid for once; do not pay again.

## 1. Grade at the granularity the humans curated — never synthesize inherited expectations

The v1 gold cascaded parent-heading concepts into every leaf's answer key ("SALI 0 (cascade down)" read literally). Result: 80% of the strict-F1 denominator was gold the input text could never reach (2.4% recall on inherited vs 21.7% on own-cell gold), and baseline F1 measured .0846 — a number about the answer key, not the matcher. Rebuilding per curated cell (every label cell at any level = its own item, no inheritance, dedup identical text) moved the identical predictions to .2093. **Test:** if a whole origin-class of gold shows near-zero recall while another shows healthy recall, suspect the derivation before the matcher.

## 2. A join key must carry every identity field — and displays must render source-of-truth, not snapshots

Two sheet defects nearly corrupted human rulings: (a) gold rows keyed by input text alone bound Firm-1 rows to Firm-2 items (13/132 adjudication rows showed the wrong item's gold AND pipeline, and would have folded edits onto the wrong firm's gold); (b) "applied to gold today" panels rendered a pre-fold packet snapshot, contradicting the live gold file after folds. Fixes: key by (firm, normalized text) — the full item identity; render every gold panel from the newest `gold_vN.jsonl` at build time with the workbook curation as a provenance line. **Test:** for any human-facing evidence surface, diff a sample of rendered panels against the source-of-truth file in the same run.

## 3. Human-gate UX rules (Damien's, now standing for this repo)

- Never lock a human's past decisions — pre-fill them as the no-op baseline and accept amendments (append-only, provenance-tagged).
- Per-concept granularity (keep/remove per gold atom, elevate/reject per pipeline candidate), notes on every decision.
- Three labeled panels so the human always knows which system produced what: Gold (current version) / Current pipeline / Proposed.
- Show the original spreadsheet rows (mini-grid, real headers, row numbers).
- Machine principles pre-check defaults, but rows where the principle can't decide get badged for the human ("needs your eye"), never silently defaulted.
- Domain canon encoded from his rulings: per-cell atomic mapping (a child never implies its parent); molecules decompose into atoms (add implicit industry/asset/player/practice tags); the pipe character in curated cells always means multiple tags.

Privacy standing rule (KTD1/KTD12 of the plan): firm surface strings never enter committed files — `clusters.assert_no_surfaces` is the enforcement choke point; row-level artifacts live gitignored under `eval/data/`; decision sheets ship as private artifacts, deleted after their gate folds.
