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

## Offline build receipt

Implementation: `463946c`. Usage and the controller execution boundary are in
[`u10-finalization-replay.md`](../../benchmarks/u10-finalization-replay.md).

- Before production edits, generated real first finalization and the ordinary shortcut passed; explicit replay failed with the unsupported argument (`1 passed, 1 failed`).
- The U2 generated replay suite passed 65 tests. A separate generated characterization loaded only the pre-change finalizer source from `ab80aa6` and proved byte equality for both internal and external checkpoint paths (2 passed).
- After U3, `tests/test_eval_u10_finalize_replay.py` passed all 76 cases. These exercise real campaign parsing/rendering/CLI, fixture discovery and digest pins, local-gold freshness, leak and item-ID rejection, and unchanged adoption boundary semantics.
- The permitted comparison regression run initially returned 153 passed and one failure: the ordinary entrypoint passed replay-only keywords to an existing hook. The implementation restored the ordinary call shape, and that unchanged regression then passed. The two cases selected out read protected metadata and were not run in this lane; equivalent policies are exercised with generated metadata in the new module.
- Repository-wide `ruff check .` passed. Library `mypy` passed all 26 source files. `git diff --check` passed.

The permitted regression run used a process-level access guard installed before pytest import; the new module also carries a scoped collection/execution guard. Protected inputs, unrelated home data and network access were rejected. No existing test was changed or removed. No full-suite or original-data scan result is claimed.

Environment note: offline dependency synchronization failed because the supplied cache lacked the locked development wheels. A separate development environment was populated from the existing lockfile using the package index; subsequent verification used offline, no-sync commands. No candidate environment, dependency declaration or lockfile was changed; no provider call, model download or paid measurement ran.

## Final verification and review

Reviewed implementation: `6cb4251`. Simplification removed seven redundant comments/docstrings without changing executable behavior or tests.

The independent review identified an incomplete runtime-root exclusion. Four generated destination cases reproduced the gap before the fix: replay incorrectly succeeded inside model, derived, ontology and consumer-runtime fixture roots. The fix discovers runtime prefixes, import paths and model-cache roots through each bound interpreter before creating replay output; it also protects the shared FOLIO cache. Stored fingerprint fields are unchanged.

- Final generated suite: **84 passed**, including real generated interpreter discovery, a consumer-runtime symlink and malformed-probe refusal. The independent Codex recheck reran all 84 successfully.
- Existing pilot suite after the compatibility correction: **89 passed, 2 deselected** under the access guard. The two protected-metadata cases remain unchanged in CI.
- Repository-wide Ruff passed; the library typecheck passed all 26 source files. Diff checks passed.
- No existing test was removed or weakened. There are no frontend changes or affected browser routes, so screenshot verification is not applicable.

ce-code-review receipt: Codex review `.git/fr1-review/recheck.json` (prompt `.git/fr1-review/recheck-prompt.md`; amends `.git/fr1-review/review.json`), verdict `merge`

The initial prompt and fix-first verdict remain at `.git/fr1-review/prompt.md` and `.git/fr1-review/review.json`. Raw review artifacts stay in local Git metadata, outside the tracked publication. The controller should retain them with the publication evidence. No actionable review finding remains unapplied.

All fr-1 changes are committed locally. Publication remains controller-owned: there is no configured remote and no push or PR from this clone. Full CI, original-input replay, original-data scanning and unavailable-ref reconciliation remain the acceptance prerequisites described above; this receipt does not clear them.
