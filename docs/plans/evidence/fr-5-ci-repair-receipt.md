# fr-5 Core CI repair receipt

Item: `fr-5` (expansion). Baseline: `e19f385`; the committed plan is `0d25388`.
The baseline retained all six fr-2 commits and reproduced Ruff failures in the
readiness launcher and tests before repair.

## Repair

The launcher keeps bytecode suppression and its local import-path setup ahead of
the readiness import, while documenting that intentionally delayed import with
the same local `E402` exception used by the existing evaluation launchers. Test
lint findings were repaired without changing their assertions. The copied,
isolated launcher regression now covers JSON, Markdown, and `--help`; each case
checks exit zero, expected stdout, empty stderr, byte-identical source and input
files, and absence of newly created files.

## Verification

| Check | Python 3.11.15 | Python 3.12.13 |
|---|---|---|
| Readiness tests | 76 passed | 76 passed |
| Ruff | Passed | Passed |
| Supplemental readiness mypy | Passed | Not required by the workflow |
| Frozen `uv sync --extra dev` | Incomplete: offline cache lacked `pluggy==1.6.0` | Incomplete for the same locked environment |
| Complete default suite | Incomplete: collection lacked cached Pydantic | Incomplete: an improvised cache-path run was invalid because the suite correctly rejects `PYTHONPATH`; stopped after 897 passes, 1 skip, and 5 environment-induced failures |
| Library mypy | Incomplete: Pydantic was unavailable | Not required by the workflow |
| Wheel build and installed-wheel smoke | Incomplete because the frozen environment could not be installed | Incomplete because the frozen environment could not be installed |

The exact workflow environment could not be reconstructed without a network
download, which this build forbids. No test was skipped, deleted, narrowed, or
weakened. Generated JUnit files remain untracked and are not publication
evidence. The controller must rerun both unchanged Core jobs in a complete
locked environment before publication; this receipt does not claim they pass.

Review prompt: `fr-5-codex-review-prompt.md`.

ce-code-review receipt: Codex review `fr-5-codex-review-verdict.md`, verdict `merge` for the scoped source diff; verification limitations remain publication blockers.

No provider call, spend, migration, data rewrite, release, outside service,
protected-input modification, R17 verdict change, or R11 adoption change was
made. A replacement PR must explicitly state that it supersedes PR #71; local
work alone does not make that claim true.
