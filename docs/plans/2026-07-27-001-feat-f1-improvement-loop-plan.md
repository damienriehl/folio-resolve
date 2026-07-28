---
title: folio-resolve F1 Improvement Loop - Plan
type: feat
date: 2026-07-27
topic: f1-improvement-loop
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
product_contract_source: ce-brainstorm
execution: code
---

# folio-resolve F1 Improvement Loop - Plan

## Goal Capsule

- **Objective:** Raise folio-resolve's measured F1 (strict IRI-set precision/recall) on real-world firm-taxonomy gold, through an evaluation-driven improvement loop, so that folio-mapper, folio-enrich, and every other consumer inherit the gains through the library.
- **Product authority:** Damien's dialogue answers of 2026-07-27 and cockpit ask `folio-resolve-2026-07-27-f1-loop-scope` (all four questions answered on the recommended path; overall scope confirmed).
- **Open blockers:** None.

---

## Product Contract

### Summary

Build a multi-label gold set from two firms' SALI-mapping workbooks and a strict IRI-set-F1 evaluation harness, then run an iterative improvement loop over folio-resolve — three iterations per check-in — with the gold itself correctable and versioned. Downstream consumers are validated read-only at each check-in. Once Damien is satisfied with F1, a gated follow-on phase generates a synthetic test corpus from the real-data patterns and wires it in as a permanent regression floor.

### Problem Frame

folio-resolve's precision machinery was calibrated against curated corpora, and its label matcher reaches 100% gold recall on bench samples — but its performance on real firm taxonomies (the actual mapping workload) has never been measured. The one quantified gap points at candidate generation: retiring folio-enrich's forked recall orchestration would collapse the ranked candidate set by 87.5% (`docs/migration/SCHEDULE.md`). Two real firm workbooks with human-curated SALI mappings now exist as exemplars, which makes a genuine F1 measurement — and a disciplined improvement loop against it — possible for the first time. Because the mappings were made by hand, the gold itself may contain errors, and an F1 score is only as trustworthy as the gold it is measured against.

### Key Decisions

- KD1. **Strict exact-IRI set-F1 is the headline metric** (session-settled: user-directed — chosen over hierarchical partial credit: hard to game, easy to trust). Governs R5.
- KD2. **Cross-firm holdout plus a frozen ~20% slice** (session-settled: user-directed — chosen over a pooled random split: the firms' vocabularies differ enough to test real generalization). Governs R6.
- KD3. **Three iterations per check-in** (session-settled: user-directed — chosen over longer autonomous runs: early redirection beats long unobserved runways). Governs R10.
- KD4. **Changes land in folio-resolve only** (session-settled: user-directed — chosen over multi-repo freedom: clean attribution and rollback). Governs R9, R13.
- KD5. **Gold is correctable and versioned** (session-settled: user-directed — Damien's 2026-07-27 note: the goal is highest true classification accuracy, not fidelity to one spreadsheet pass). Governs R3, R11.
- KD6. **Suspect-gold routing: audit gate after baseline, then batches at check-ins** (session-settled: user-directed — chosen over per-suspect interrupts or check-in-only: earliest signal without mid-loop pings). Governs R11.
- KD7. **Blank SALI cells mean "not yet mapped"** (session-settled: user-directed — chosen over treating blanks as true negatives: blanks are incomplete work, and confident pipeline output there is an opportunity, not an error). Governs R4.
- KD8. **All four Firm-2 term sets are in scope** (session-settled: user-directed — chosen over skipping Jurisdiction: the place-name tension becomes a measured finding rather than a blind spot). Governs R1.
- KD9. **The loop is failure-driven** (session-settled: user-approved — chosen over committing upfront to the recall-engine port or an embedding channel: every iteration's target must be justified by measured failure clusters; the recall port is the expected iteration-1 target, the embedding channel is admitted only if clusters show misses no lexical strategy can reach). Governs R8, R9.
- KD10. **Tiered delegation** (session-settled: user-directed — the orchestrating session plans, judges, and ships; Opus subagents carry implementation and analysis; Sonnet subagents carry work they can do sufficiently well, e.g., mechanical extraction, batch runs, report assembly).

### Actors

- A1. Damien — product and gold authority; decides gold corrections, check-in continuations, and the synthetic-phase gate.
- A2. Orchestrating session — decomposes work, judges results, runs check-ins, ships.
- A3. Worker subagents — execute gold construction, harness code, failure analysis, and library changes under A2's specs, tiered per KD10.

### Requirements

**Gold construction**

- R1. The gold set covers both workbooks: Firm 1's mapping sheet (multi-label SALI 0–6 columns, with Level-2 headings cascading to their children) and all four Firm-2 term sets (WorkType, Legal Topic, Sector, Jurisdiction) where SALI mappings are populated.
- R2. Gold SALI labels and legacy IRIs resolve to FOLIO IRIs by exact-label lookup and legacy-IRI normalization — never by the pipeline under test. Gold rows that cannot be resolved are excluded and surfaced in the audit report with counts.
- R3. The gold set is versioned. Every reported score cites the gold version it was measured against; accepted corrections bump the version, and the score trajectory is re-baselined so no F1 movement comes from moving goalposts.
- R4. Rows with blank SALI columns are excluded from scoring and reported as coverage. Where the pipeline is confident on a blank row, its suggestion is queued for Damien as a new-gold candidate.

**Evaluation harness**

- R5. The harness scores micro-averaged precision, recall, and F1 — per firm, per term set, and overall — by strict exact-IRI set comparison. Parent/child near-misses are reported as diagnostics and score zero.
- R6. Tuning uses Firm 1; every iteration validates on Firm 2; a frozen ~20% slice of both firms stays untouched until the final report.
- R7. Harness runs are offline (locally cached FOLIO ontology) and reproducible: versioned inputs, deterministic outputs.
- R8. A failure-analysis step clusters misses by cause (e.g., candidate-set gaps, synonymy, homonym traps, normalization, hierarchy), and each iteration's target is justified by cluster size.

**Improvement loop**

- R9. Each iteration lands one coherent change in folio-resolve, measured before/after on the tune set and validated cross-firm. No new runtime dependencies; existing optional extras are allowed.
- R10. After three iterations, a check-in reaches Damien as a Cockpit ask plus decision artifact: F1 trajectory, per-change attribution, downstream validation deltas, and a keep-going/stop recommendation.
- R11. A gold-audit gate runs after the baseline, before iteration 1: pipeline-vs-gold disagreements are triaged into pipeline-wrong (iteration fuel) and gold-suspect (sent to Damien with suggested corrections and evidence). New suspects found later batch into check-ins.
- R12. The existing test suite stays green throughout, and any change to default scoring behavior is versioned deliberately rather than drifting silently.

**Downstream validation**

- R13. Each check-in includes read-only validation of the improved library against folio-mapper's demo probe payloads and folio-enrich's demo corpus and golden-baseline harness, reporting deltas without committing to those repos.

**Synthetic regression phase (gated on Damien's satisfaction signal)**

- R14. A synthetic test corpus is generated from the real data's patterns — vocabulary styles, multi-label shapes, trap types — kept distinct from all tuning data.
- R15. The synthetic suite runs in CI as an F1 regression floor, so later library changes cannot silently lower F1.

### Key Flows

```mermaid
flowchart TB
  G[Build versioned gold set] --> H[Harness: strict set-F1]
  H --> B[Baseline scores + failure clusters]
  B --> A{Gold-audit gate}
  A -->|corrections accepted| G2[Gold version bump + re-baseline]
  G2 --> I[Iterate: one change, measure, validate]
  A -->|no corrections| I
  I --> I2{3 iterations?}
  I2 -->|no| I
  I2 -->|yes| C[Check-in: trajectory + downstream deltas]
  C -->|continue| I
  C -->|satisfied| S[Synthetic corpus + CI regression floor]
```

- F1. **Baseline and audit.** **Trigger:** gold set and harness exist. **Steps:** score baseline; cluster failures; triage disagreements; deliver gold-audit sheet to Damien; fold accepted corrections into a new gold version; re-baseline. **Outcome:** trusted gold, trusted baseline. **Covers R2, R3, R5, R8, R11.**
- F2. **Improvement iteration.** **Trigger:** baseline or prior iteration complete. **Steps:** pick the largest justified failure cluster; implement one change in the library; re-score tune set; validate on Firm 2; record attribution. After three, run downstream validation and check in. **Outcome:** measured, attributable F1 movement. **Covers R6, R8, R9, R10, R12, R13.**
- F3. **Gold correction.** **Trigger:** suspect surfaced at audit gate or check-in. **Steps:** Damien reviews suggestion and evidence; accepts or rejects; accepted corrections bump the gold version; scores re-baseline. **Outcome:** gold quality rises with F1, goalposts never move silently. **Covers R3, R11.**
- F4. **Synthetic phase.** **Trigger:** Damien signals satisfaction with F1. **Steps:** generate synthetic corpus from real-data patterns; verify it is disjoint from tuning data; wire into CI as a regression floor. **Outcome:** durable protection. **Covers R14, R15.**

### Acceptance Examples

- AE1. **Gold suspect.** **Covers R11, R3.** **Given** the pipeline maps "Fund Formation" to a concept with strong label and definition evidence while gold says an unrelated concept, **when** triage runs, **then** the row appears on Damien's audit sheet with both candidates and evidence, and F1 is unaffected until he accepts — at which point the gold version bumps.
- AE2. **Blank row.** **Covers R4.** **Given** a Firm-2 Legal Topic row with empty SALI columns, **when** scoring runs, **then** the row is absent from the scored denominator, counted in coverage, and a confident pipeline suggestion for it lands on the new-gold candidate list.
- AE3. **Legacy IRI.** **Covers R2, R5.** **Given** gold IRI `http://lmss.sali.org/RBX1KA0BJR7y27zZSvaLBVE`, **when** the pipeline returns the corresponding `https://folio.openlegalstandard.org/` IRI, **then** the match counts — normalization happens at gold construction, not at scoring time.
- AE4. **Overfit tripwire.** **Covers R6, R9.** **Given** an iteration that gains 4 F1 points on Firm 1 but loses 2 on Firm 2, **when** validation runs, **then** the change is flagged as overfitting and is not accepted as-is.
- AE5. **Near-miss.** **Covers R5.** **Given** the pipeline returns the direct parent of a gold concept, **when** scoring runs, **then** the item scores zero on that concept and the near-miss appears in diagnostics.

### Success Criteria

- Strict set-F1 on the frozen holdout improves from baseline to final report, both measured against the same (final) gold version. No numeric target is pinned; Damien decides sufficiency at check-ins.
- Every iteration's F1 delta is attributable to one named change.
- Consumer demo validations show no regression at any check-in.
- The trusted gold set and harness survive the loop as durable artifacts a future session can rerun.

### Scope Boundaries

- No code changes in consumer repos; validation there is read-only.
- No new runtime dependencies; existing optional extras (embedding, spacy, folio) are the outer limit.
- No gold change without Damien's acceptance; the pipeline never grades itself.
- PyPI release timing and consumer pin bumps stay with ask `folio-resolve-2026-07-24-release-and-recall`, not this plan.
- FOLIO ontology content fixes (wrong labels, missing concepts in FOLIO itself) are findings to report, not work items here.

<!-- ce-section: work-relationships -->
### How This Work Fits Together

This plan owns the F1 improvement loop. The surrounding picture, as currently understood:

- **Shares** its expected iteration-1 payload with migration Stage 2 (`docs/migration/SCHEDULE.md`): the `RecallOntology` protocol + `MultiStrategyRecall` engine. Landing it here, evidence-justified, also advances retiring folio-enrich's forked `search.py`.
- **Enables** the "assess where migration falls short and how can we improve" thread Damien opened in his `q4-insights-bridges` answer — the failure clusters and downstream deltas are that assessment's evidence.
- **Can proceed independently of** the PyPI release decision; consumers validate against the local library.
- **Still to decide:** whether the synthetic corpus later becomes a shared fixture for consumer repos' own test suites.

### Dependencies / Assumptions

- Local FOLIO ontology cache and folio-python (`folio` extra) are available offline; confirmed present on this machine.
- Assumption: most gold SALI labels/IRIs resolve to current FOLIO concepts. Unverified until gold construction; resolution coverage is measured and surfaced by R2 rather than assumed.
- The two uploaded workbooks are the gold source of record; they currently live outside any repo.

### Outstanding Questions

- **Deferred to Planning:** where the harness and gold live in the repo, and whether either firm workbook (or the derived gold) may be committed — default assumption is a gitignored data directory, since firm taxonomy data may be sensitive and the repo may become public.
- **Deferred to Planning:** whether the pipeline input per row includes hierarchy context (parent headings) or the leaf label only — treated as an experiment variable inside the loop.
- **Deferred to Planning:** the exact confidence threshold for queueing new-gold candidates from blank rows.

### Sources / Research

- `docs/migration/SCHEDULE.md` — Stage-2 recall findings (candidate-set collapse, per-path gate adoption) and open nondeterminism note.
- `bench/RESULTS.md` — ruler shootout: gold recall and throughput evidence for the current matcher.
- `src/folio_resolve/resolve.py`, `scoring.py`, `gates.py`, `pipeline.py` — current thresholds, gates, and the ranked-candidate API (`MatchPipeline.match`).
- folio-enrich: `backend/app/services/folio/search.py` (recall orchestration to port), `backend/migration/` (golden-baseline corpus and delta harness).
- folio-mapper: `scripts/demos/` (probe payloads with expected outputs), `scripts/demos/run_probe.py`.
- folio-python: legacy-IRI normalization in `folio/graph.py`; ontology cache under `~/.folio/cache/`.
- Workbooks: `DUMMY_SALI_FOLIO_MAPPER.xlsx` (Firm 1, mapping sheet "Copy of SALI ↔ Big Firm") and `Copy_of_SALI___Big_CC_Firm.xlsx` (Firm 2, four term-set sheets).
