# fr-2 Codex review prompt

Review the local fr-2 change against base `abf14ce`, including working-tree
simplification, under `ce-code-review mode:agent` and `CLAUDE.md`.

Plan: `docs/plans/2026-10-26-1051-feat-fr-2-campaign-readiness-plan.md`.

Review scope:

- `eval/folio_eval/campaign_readiness.py`
- `eval/check_campaign_readiness.py`
- `tests/test_eval_campaign_readiness.py`
- `eval/README.md`

Verify the fixed schema-v1 inventories, strict finite-vocabulary validation,
honest binding/ownership/receipt/currentness states, deterministic renderers,
content-free diagnostics, documented exit contract and read-only CLI boundary.
Check requirements against the plan. Favorable attestations must never become
verification, execution authorization or adoption permission. Review correctness,
security, reliability, tests and applicable project standards. No source edits.

Use native Codex review on Astra. Cross-model review is disabled by checkout
configuration. Do not access protected evaluation inputs, credentials, private
files, providers or network services. Use fabricated snapshots only. No remote
changes, publishing, spending, security changes or unbacked deletion.

Return findings and a verdict, recording verification limits. Retain the prompt
and verdict artifacts. Public receipts contain artifact names and verdicts only.

## Follow-up prompt

Recheck the output-error fixes in commit `9f14cd6` against the first review's
confirmed exit-code finding. Inspect the closed-stream and real broken-pipe
regressions, plus the added launcher no-artifact test. The host observed three
failures before the fix and 91 passing selected tests afterward on each of
Python 3.11.14 and 3.12.12. Verify the current source and return a final verdict
with any remaining findings. Preserve the same scope and offline boundaries.
