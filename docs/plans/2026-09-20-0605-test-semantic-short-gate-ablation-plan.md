---
title: Semantic Short-Label Gate Experiment - Plan
type: test
date: 2026-09-20
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Semantic Short-Label Gate Experiment - Plan

## Goal Capsule

- **Objective:** Determine whether relaxing the short-label gate deserves further investigation for semantic retrieval, with the recall benefit and newly admitted candidates visible to reviewers.
- **Means:** One controlled benchmark ablation of ShortLabelGate (KTD1).
- **Authority:** Owner-approved unambiguous fixtures remain fixed; this plan governs the experiment, not production adoption.
- **Execution profile:** Two sequential units; the executor owns tests, recorded review, and delivery of the evidence.
- **Stop condition:** Finish with the measured verdict even if it rejects further work. Do not tune another lever to manufacture improvement.

## Product Contract

### Summary

Compare the existing pipeline with one benchmark-only variant that bypasses ShortLabelGate. Preserve the model and all other matching settings, report which candidates change, and state whether the result warrants a separate policy proposal.

### Problem Frame

The [stage evidence](../benchmarks/embedding-diagnostics.md) shows that the local model retrieves Negligence fifth at 54.6, then the short-label gate demotes it to 40 below the 45 floor. The other three paraphrase targets have full-corpus semantic scores below 45. Wider retrieval alone cannot recover them.

The two negative controls also have local semantic scores below 45. Their continued absence under a relaxed gate would not demonstrate that the gate is unnecessary. A controlled ablation can measure its effect without treating the benchmark as sufficient evidence to change defaults.

### Requirements

**Controlled comparison**

- R1. Use only the existing eight frozen public queries and their answer sets, with the same ontology, model pin, and normalized corpus.
- R2. Compare baseline and short-label-gate bypass at semantic top-k 5 and score floor 45 across disabled, hashing, and local variants; leave blocklist, place gate, lexical retrieval, deduplication, and ranking unchanged.
- R3. Preserve every existing baseline artifact and production source file; experimental behavior must remain confined to benchmark tooling.

**Evidence and interpretation**

- R4. Report per-case final candidates, target ranks, score changes, newly admitted candidates and extraction paths, positive hit rates at 1 and 5, and negative-control candidate counts.
- R5. Exercise existing short-label/place-name/blocklist regression scenarios as named guard checks, reporting the protection lost by the ablation without weakening their assertions for normal production behavior.
- R6. Finish with a supported verdict on further investigation; no result from these eight queries alone authorizes a production policy change or a general precision claim.

### Key Decisions

- **Keep the unambiguous initial cases.** Governs R1. (session-settled: user-directed — chosen over the full twelve proposed fixtures: the owner selected only unambiguous cases initially.)

### Scope Boundaries

Production gate changes, new model selection, score-floor tuning, wider retrieval, FAISS, and embedding caches are outside this experiment. New benchmark answers require separate owner review. Private gold, U10 triage, campaign execution, and PR45 remain excluded.

## Planning Contract

### Key Technical Decisions

- KTD1. **Ablate the entire short-label gate only within benchmark construction.** Inject a pass-through gate into the experimental pipeline and use the real MatchPipeline for both arms. This deliberately broad ablation establishes the gate's effect across paths; it does not claim that bypassing lexical protections would be a safe product policy. It avoids copying the production ranking algorithm or changing the public API. Implements R2/R3.
- KTD2. **Use the frozen arm as an exact control.** Reuse corpus/model identity checks from `benchmarks/embedding_recall.py` and comparison patterns from `benchmarks/embedding_diagnostics.py`. Baseline candidate fields and ordering must match the committed artifacts before interpreting experimental output. Record both source and input hashes. Implements R1/R4.
- KTD3. **Measure relevance, not a speedup.** Build once per embedding variant and run both arms against identical inputs in that process. Stage latency attribution already exists; the experiment makes no comparative latency or memory claim. Implements R4 without adding noisy performance targets.

### Assumptions

The proposed next step is an agent recommendation for review: a bounded ablation is more useful now than modifying defaults. A gain can support investigating a source-aware policy later, but further answer review and adversarial coverage would still be needed before adoption.

### Decision Rule

First require exact baseline reproduction and intact normal-mode guard tests. Report further investigation as warranted only if the ablation recovers an approved paraphrase in the final top five without losing either exact target from the top five. Report any newly admitted negative-control candidates and any weakened named guards alongside that verdict. Those effects constrain a later policy proposal; they cannot be hidden by the recall total. Otherwise recommend retaining the baseline and explain the failed condition. This is an experiment selection rule, not an adoption gate (R6).

## Implementation Units

### U1. Add the controlled ablation and guard evidence

**Goal:** Make the isolated effect of the short-label gate reproducible.

**Requirements:** R1-R5. **Dependencies:** Existing public baseline and stage diagnostic artifacts.

**Files:** Create `benchmarks/embedding_gate_ablation.py` and `tests/test_embedding_gate_ablation.py`; read `tests/test_pipeline.py`, `tests/test_new_capabilities.py`, and `tests/test_blocklist.py` as regression references.

**Approach:** Follow KTD1-KTD3. Keep one explicit baseline arm and one explicit bypass arm. Record paired candidate deltas keyed by IRI and extraction path. Record guard inputs and results separately from the eight-query recall denominators. Reuse existing helpers without editing the frozen runner or source pins.

**Execution note:** Start with real-pipeline characterization and a failing test for the new paired report before writing the runner.

**Patterns to follow:** `benchmarks/embedding_diagnostics.py` for snapshots, identity checks and baseline equality; `tests/test_embedding_diagnostics.py` for real index, ontology, blocklist, and gate behavior.

**Test scenarios:**

- A retrieved single-word semantic target above 45 is removed by baseline and survives the bypass arm.
- An exact short label survives in both arms; a duplicate IRI retains the actual strongest eligible candidate.
- A lexical short-label fuzzy hit becomes newly eligible when the short gate alone was its suppressor; the report identifies its lexical path.
- An uncorroborated place and a blocked alias stay suppressed by their unchanged protections in both arms.
- A target already below 45 stays absent even when the short-label gate is bypassed.
- Disabled embeddings produce no semantic-path deltas; hashing remains identified as a diagnostic provider.
- Fixture, corpus, model, imported-library, or baseline-output drift fails before a comparison verdict is produced.

**Verification:** Focused tests exercise real pipeline composition and paired reporting. Existing normal-mode guard tests remain green and unchanged.

### U2. Record the public comparison and verdict

**Goal:** Give reviewers inspectable evidence for the next policy decision.

**Requirements:** R1, R4-R6. **Dependencies:** U1.

**Files:** Create `docs/benchmarks/embedding-gate-ablation.md` and variant-specific `docs/benchmarks/embedding-gate-ablation-*.json`.

**Approach:** Run the paired experiment with the verified offline inputs. Apply the Decision Rule, report every changed candidate, and distinguish observed guard losses from unknown generalization. Keep the stage evidence as the explanation for choosing this lever.

**Test expectation:** No additional unit tests for generated reports; verify all case identities, control candidates, provenance hashes, and metric arithmetic against the raw outputs.

**Verification:** All three baseline arms reproduce their frozen candidates exactly. The report follows the measured outcome even when it rejects the proposed lever.

## Verification Contract

Use the project's isolated development pytest command for the full core suite, including the new tests, plus Ruff and configured mypy. Run the public experiment with the pinned offline model environment used by the baseline. Keep measurement output separate from test doubles. Retain the Codex review prompt and verdict before delivery. No release validation or deployment monitoring is needed for benchmark-only changes.

## Definition of Done

- U1 has real-pipeline proof of paired behavior, meaningful guard checks, and fail-closed identity validation.
- U2 records all eight cases for both arms of all three variants, unchanged baseline results, candidate deltas, and a verdict that obeys R6.
- Required tests, lint, type checks, and recorded review pass; abandoned experimental code is removed.
- Production code, frozen fixtures, original measurements, private evaluation artifacts, and unrelated workspace files remain unchanged.
