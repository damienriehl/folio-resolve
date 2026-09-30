---
title: Finish U10 from preserved receipts after output metadata repair
type: fix
date: 2026-09-30
artifact_contract: ce-unified-plan/v1
product_contract_source: owner-dispatch
---

# Finish U10 from preserved receipts after output metadata repair

## Goal Capsule

Apply the September 30 authorization to triage locally and resume finalization, preserving the checkpoint and confidentiality gate. No corpus text, restricted match, salt, item identifier, or checkpoint digest is recorded here. Do not run the scoring campaign again.

## Product Contract

The September 6 comparison close-out plan remains authoritative: a verified loss permits no-adopt close-out; pass or hold returns to the existing owner gate. The frozen firm exam remains owner-run. The present worker may only write its assigned worktree, so actual replay in the preserved candidate checkout is an orchestrator-owned dependency, not a missing owner decision.

## Planning Contract

The existing repair runner separates reviewed finalization code from the fingerprinted candidate. The local fix adds the exact existing v2 working output path to the two producer-path consumers: repair admission and the validated output argument's public-metadata binding. It does not edit the manifest, salt, public-metadata file, or stored fingerprint. No general allowlist or filename pattern is added. Repeating the output string in unrelated prose remains blocked, as do custom output paths, duplicate output arguments, and metadata mismatches.

## Implementation Units

1. Validate every original shard with `_load_completed_shard` and validate the manifest with `_checkpoint_manifest`. Completed locally: 90 receipts agree with report hashes and stored fingerprint bindings; all three merged stacks contain 90 rows; the final completion receipt is absent. This is stored-artifact integrity, not a claim that today's runtime matches the recorded environment.
2. Characterize the publication-path failure with generated test cases. Before the repair: the v2 entrypoint rejects the output, and the metadata preflight reports one collision. After the repair: both v1/v2 paths pass their intended field checks; copied output text elsewhere, duplicate arguments, and custom paths still fail.
3. Review and integrate the repair; prepare a clean repair checkout separately from the original candidate. Use the original candidate interpreter and locale, pinned app roots and original bound inputs. Resolve the variables below from the preserved run configuration; do not guess them or expose their contents.

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
  --checkpoint-dir "$CHECKPOINT_ROOT" --out "$V2_OUTPUT" --limit 60
```

4. Let existing fingerprint and leak checks fail closed. Any current runtime mismatch needs evidence-preserving recovery, not a rewritten stored fingerprint. The assigned worker checkout contains unrelated task material and is not a pristine repair checkout. Do not run the above here by disabling cleanliness checks.
5. Preserve the first finalized output and receipt. Independently regenerate for byte comparison using the established reviewed replay procedure; a second ordinary invocation with an existing completion receipt only validates it and returns, so that shortcut alone does not prove deterministic regeneration. Keep the original checkpoint backed up, verify zero collisions, and compare output binding before release.
6. Regenerate the campaign report from the verified result, route the measured verdict, then have the orchestrator update the existing cards. Publish only leak-clean reviewed aggregate artifacts with the required LFS tracking. Never treat this local preparation as campaign completion.

## Verification Contract

The U12 work out-file records local tests, original receipt checks, and limitations. A read-only reconstruction from all original receipts found exactly one publication collision. Excluding only the output argument value reduced the count to zero. This localizes the entire reconstructed failure to producer metadata, not corpus content; the generated regression reproduces the refusal without restricted inputs. Reconstruction used current code and did not validate the live runtime or publish a report. No restricted match is printed. Live finalization, present-day full runtime fingerprint agreement, byte-identical replay, final aggregate leak acceptance, and the measured R17 verdict remain pending.

The silent-failure context is already fixed in Cockpit: c3d59a89 is an ancestor of its locally stored master. Its recorded regression failed the clean-exit blocked/failed cases before the fix; the receipt records 55 passing wrapper/watchdog tests afterward. Successful terminations now preserve blocked, failed, and review_ready. This worker neither modifies nor reruns external monitoring. A fresh end-to-end durable notification, restart/dedup, and handoff exercise is not independently established by this receipt; any additional monitoring validation belongs to Cockpit.

## Definition of Done

Local repair: targeted regressions and comparison suite pass; reviewed source and replay instructions are committed. Campaign completion: original pinned replay succeeds, all confidentiality/binding checks pass, aggregate results and routed verdict exist, and the orchestrator records completion. The historical “blocked 10 days” description is a September 18 observation, not a current elapsed-time claim.

Rollback: revert the repair commit before work if needed; preserve all original checkpoint files. Later report publication gets a separate revert pointer. Both the repair and this preparation belong on the default branch independently of the older documentation branch named in the dispatch.
