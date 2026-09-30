# User acceptance test report

- Commit: `984b4125651908e78d441d4a7e476d911c0bd655`
- Python: `3.13.11`
- Extras present: `folio=true`, `spacy=true`, `embedding=false`
- Real ontology enabled: `true`
- Metadata source: JUnit testsuite properties.

## Rerun commands

- `uv run --isolated --extra dev pytest tests/uat -m uat --junitxml=.codex-out/uat/core.xml`
- `FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1 .venv/bin/python -m pytest tests/uat -m uat --junitxml=.codex-out/uat/extras.xml`
- `.venv/bin/python tests/uat/build_report.py --junit .codex-out/uat/extras.xml --stories docs/uat/user-stories.md --out docs/uat/2026-09-30-uat-report.md --core-junit .codex-out/uat/core.xml`

## Story verdicts

| Story ID | Persona | Verdict | Core-run verdict | Skip/fail reason |
|---|---|---|---|---|
| US-PI-01 | PI | pass | pass |  |
| US-PI-02 | PI | pass | pass |  |
| US-PI-03 | PI | pass | pass |  |
| US-SI-01 | SI | pass | pass |  |
| US-SI-02 | SI | pass | pass |  |
| US-SI-03 | SI | pass | pass |  |
| US-RI-01 | RI | pass | pass |  |
| US-RI-02 | RI | pass | pass |  |
| US-RI-03 | RI | pass | pass |  |
| US-AA-01 | AA | pass | pass |  |
| US-AA-02 | AA | pass | pass |  |
| US-AA-03 | AA | pass | pass |  |
| US-LJ-01 | LJ | pass | pass |  |
| US-LJ-02 | LJ | pass | pass |  |
| US-LJ-03 | LJ | pass | pass |  |
| US-OM-01 | OM | pass | pass |  |
| US-OM-02 | OM | pass | pass |  |
| US-OM-03 | OM | pass | pass |  |
| US-EO-01 | EO | pass | pass |  |
| US-EO-02 | EO | pass | pass |  |
| US-EO-03 | EO | pass | pass |  |
| US-RM-01 | RM | pass | pass |  |
| US-RM-02 | RM | pass | pass |  |
| US-RM-03 | RM | pass | pass |  |

## Unmapped tests

- `tests.uat.test_consumer_walk::test_aggregate_and_exit_code[verdicts0-works-0]` (pass)
- `tests.uat.test_consumer_walk::test_aggregate_and_exit_code[verdicts1-friction-0]` (pass)
- `tests.uat.test_consumer_walk::test_aggregate_and_exit_code[verdicts2-skipped-0]` (pass)
- `tests.uat.test_consumer_walk::test_aggregate_and_exit_code[verdicts3-broken-1]` (pass)
- `tests.uat.test_consumer_walk::test_readme_extractor_preserves_drift_and_markdown_indentation` (pass)
- `tests.uat.test_consumer_walk::test_fake_process_verdict[0-works]` (pass)
- `tests.uat.test_consumer_walk::test_fake_process_verdict[1-broken]` (pass)
- `tests.uat.test_consumer_walk::test_fake_process_verdict[2-friction]` (pass)
- `tests.uat.test_consumer_walk::test_drifted_readme_is_friction_and_later_blocks_run` (pass)
- `tests.uat.test_uat_harness::test_uat_marker_is_applied_to_tests_in_this_package` (pass)
- `tests.uat.test_uat_harness::test_real_ontology_requires_the_extra_and_explicit_opt_in` (pass)
- `tests.uat.test_uat_harness::test_real_ontology_propagates_installed_package_import_failures` (pass)
- `tests.uat.test_uat_harness::test_real_ontology_audit_roots_require_opt_in_and_use_folio_defaults` (pass)
- `tests.uat.test_uat_harness::test_audit_categories_allow_runtime_and_repo_but_protect_eval_data` (pass)
- `tests.uat.test_uat_harness::test_blocked_optional_imports_keeps_the_public_core_importable` (pass)
- `tests.uat.test_uat_harness::test_build_report_uses_strict_classification_metadata_and_lane_counts` (pass)
- `tests.uat.test_uat_harness::test_build_report_labels_interpreter_metadata_fallback` (pass)
- `tests.uat.test_uat_harness::test_real_ontology_requires_the_extra_and_explicit_opt_in` (skip)

## Summary

extras: pass 24 fail 0 skip 0, core: pass 24 fail 0 skip 0, harness failures: 0

## Consumer walk (installed wheel)

`tests/uat/consumer_walk.py` builds the wheel, installs it into a fresh virtualenv outside the checkout, and runs each common consumer workflow as a subprocess. It judges developer experience, not just pass/fail. Rerun: `uv run python tests/uat/consumer_walk.py --out .codex-out/consumer-walk.json` (add `--offline` to use only the uv cache).

| Step | First run | After fixes | Finding and disposition |
|---|---|---|---|
| readme_quickstart | friction (P2) | works | Two README blocks could not run verbatim (undefined `provider`; `PlaceNameGate` not imported). Fixed: each block is now self-contained on the core install. |
| scoring_only | works | works | — |
| resolve | works | works | — |
| annotate | works | works | — |
| judge | works | works | — |
| missing_extra | friction (P2) | works | A missing `folio` or `embedding` extra raised a bare `ModuleNotFoundError`. Fixed: `FolioNotInstalledError` and `EmbeddingNotInstalledError` (both `ImportError` subclasses) name the `pip install "folio-resolve[...]"` command. Only the exact missing top-level module is translated. |
| typing | friction (P2) | works | The wheel shipped without `py.typed`, so consumer mypy ignored the library's types. Fixed: the PEP 561 marker ships in the wheel, and a strict consumer snippet type-checks. |
| import_cost | works | works | `import folio_resolve` pulls in no optional heavy dependency. |
| build_metadata | works | works | — |

Found during the fix loop:

- **Frozen evidence blocked every library edit (P1 for contributors).** The offline validators compared each frozen benchmark receipt's library-source hash to the *current* tree, so any `src/` change failed core CI. Fixed in `benchmarks/evidence_replay.py`: offline validation now proves each receipt by exact deterministic replay, then checks it against its recorded source identity. All receipts and every hashed benchmark file are byte-identical; collection paths still bind the current source.
- **Local mypy on Python 3.13 fails in NumPy stubs (P3, open).** `uv run mypy` with a 3.13 venv reports a PEP 695 `type` statement in NumPy's stubs against the 3.11 target. CI (3.11) is unaffected. Deferred as dev-environment friction.

Full suite on this branch: 2,089 passed, 5 skipped. Ruff clean.
