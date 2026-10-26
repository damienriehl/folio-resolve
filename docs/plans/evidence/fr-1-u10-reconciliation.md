# fr-1 U10 source and history reconciliation

Verified 2026-10-26 for `docs/plans/2026-10-26-0931-fix-fr1-offline-u10-replay-plan.md`, U1 (R7–R9).

The clean build starting point was `drawdown/fr-1` at `373b343852ca03a2fe76e915bdad3c41a92c0d76`, containing the committed plan. Local `main` was `0a09f2c15b97458ab46429c1290e249578cbde73`, eight commits behind that starting point. No remote is configured. These are local observations, not a freshness claim about the default branch elsewhere.

| Available evidence | Disposition |
| --- | --- |
| `a78f8fe90521997f1103d317c683d31288d25ec2` | Contained merge of `feat/u10-resumable-pilot-final`; second parent `f0c0fe1` is contained. |
| `ddf6cc2` | Contained isolated repair launcher and separate candidate/repair identity implementation. |
| `2e9c34f`, merge `c3a0143` | Contained exact v2 output admission and public-metadata repair. No reapplication needed. |
| Current `eval/folio_eval/comparison_pilot.py` | Final-receipt shortcut validates the saved result; it does not independently regenerate artifacts. This is the remaining implementation gap. |
| `eval/u10-*` | Unavailable: no matching local refs or supplied exact tips. Patch equivalence and unique deltas cannot be established. |
| `feat/u10-resumable-pilot`, `feat/u10-resumable-pilot-v2` | Unavailable. The October 20 handoff records noncontained ancestry then, but supplies no exact tips. Do not infer equivalence or integration from their names. |
| `feat/u10-resumable-pilot-final` branch ref | Unavailable as a ref; its recorded merge tip is contained as noted above. |

Checks: `git for-each-ref`, `git rev-list --count main..HEAD` at the build starting point, path-limited `git log`, `git show` of the merge, and `git merge-base --is-ancestor` for the contained source anchors. No reference was deleted or rewritten. No unique available U10 patch required cherry-picking. Equivalent patches from absent tips remain unverified.

The controller must supply exact local refs or a history bundle for the missing U10 branches before exhaustive reconciliation. It must also verify the chief campaign Goal Capsule and R11/R15/R16/R17/R19/R20: the chief checkout and cited campaign file are absent here. The supplied lane rules govern this build. Neither dependency requires a new Damien decision.

Original checkpoint execution, original runtime agreement, original-data confidentiality scans, and actual campaign/adoption verdicts remain controller-owned. This receipt contains source-history evidence only; it does not establish live completion.
