---
title: Preserve downstream measurement and adoption gates
type: eval
date: 2026-09-30
artifact_contract: ce-unified-plan/v1
product_contract_source: owner-dispatch
execution: knowledge-work
---

# Preserve downstream measurement and adoption gates

## Goal Capsule

Keep the downstream reminder in Waiting until measured improvement justifies a consumer change. This is local preparation, not completion of downstream adoption.

## Product Contract

The September 27 retrieval-loss plan supersedes the old unconditional wiring reminder. Preserve both consumer pipelines and their existing pins. Measure six arms (each consumer's deterministic, Gemini, and Luna pipeline) under U8, then judge each proposed consumer change against its own baseline. Do not launch duplicate measurements for this reminder.

## Planning Contract

The September 27 plan's R11 requires increased recall at shortlist depth 100, a paired 95% F1 gain interval above zero, and verifier precision at least 3.3%. Consumer adoption additionally requires a paired item-bootstrap 95% F1 gain interval above zero against that consumer's R14 baseline. Use the same population and input identity. Record precision, recall, F1, and no-match false-positive rate. Preserve opt-in integration and prove disabled-flag parity before adoption.

## Implementation Units

1. Complete the shared U8 measurement lane described in `2026-09-30-retrieval-loss-live-completion-plan.md`.
2. Evaluate each consumer independently. A loss, hold, missing baseline, or missing paired interval keeps that consumer in Waiting.
3. Only after passing evidence, propose the specific consumer change behind an opt-in flag in its owning repository. No consumer edit is authorized by this receipt alone.

## Verification Contract

Current evidence: neither required recall-loss report exists in this branch or the inspected recall-run worktree. Therefore R11 adoption is not established. Existing comparison results do not substitute for the six new arms. Local regression verification is recorded in the U12 execution out-file.

## Definition of Done

For this local reminder task: this durable gate and a shared measurement dependency exist. For adoption: consumer-specific passing evidence, disabled-flag parity, and reviewed consumer integration must all exist. The orchestrator should retain the original card in Waiting, with the title “Measure each preserved consumer pipeline; adopt only after its paired F1 gain passes R11.” No board write was performed.

Rollback: revert this documentation commit; any future consumer change must carry its own revert pointer and flag-off procedure. This document belongs on the default branch independently of `docs/consumer-legacy-analysis`.
