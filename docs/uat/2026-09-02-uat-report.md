# User acceptance test report

- Commit: `bdd395ec958b842808f97a9c2623f38daa6237ab`
- Python: `3.13.11`
- Extras present: `folio=true`, `spacy=true`, `embedding=false`
- Real ontology enabled: `true`
- Metadata source: JUnit testsuite properties.

## Rerun commands

- `uv run --isolated --extra dev pytest tests/uat -m uat --junitxml=.codex-out/junit/core.xml`
- `FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1 .venv/bin/python -m pytest tests/uat -m uat --junitxml=.codex-out/junit/extras.xml`
- `.venv/bin/python tests/uat/build_report.py --junit .codex-out/junit/extras.xml --stories docs/uat/user-stories.md --out docs/uat/2026-09-02-uat-report.md --core-junit .codex-out/junit/core.xml`

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
| US-OM-03 | OM | fail | pass | AssertionError: assert {'https://fol...JbpNO1A', ...} <= set() Extra items in the left set: 'https://folio.openlegalsta…; core: requires the folio extra and FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1 |
| US-EO-01 | EO | pass | pass |  |
| US-EO-02 | EO | fail | pass | AssertionError: path audit failed: home=64, eval-data=0, other-outside-tmp=0 assert not Counter({'home': 64}); core: requires the folio extra and FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1 |
| US-EO-03 | EO | pass | pass |  |
| US-RM-01 | RM | pass | pass |  |
| US-RM-02 | RM | pass | pass |  |
| US-RM-03 | RM | pass | pass |  |

## Unmapped tests

- `tests.uat.test_uat_harness::test_uat_marker_is_applied_to_tests_in_this_package` (pass)
- `tests.uat.test_uat_harness::test_real_ontology_requires_the_extra_and_explicit_opt_in` (pass)
- `tests.uat.test_uat_harness::test_real_ontology_propagates_installed_package_import_failures` (pass)
- `tests.uat.test_uat_harness::test_blocked_optional_imports_keeps_the_public_core_importable` (pass)
- `tests.uat.test_uat_harness::test_build_report_uses_strict_classification_metadata_and_lane_counts` (pass)
- `tests.uat.test_uat_harness::test_build_report_labels_interpreter_metadata_fallback` (pass)
- `tests.uat.test_uat_harness::test_real_ontology_requires_the_extra_and_explicit_opt_in` (skip)

## Summary

extras: pass 22 fail 2 skip 0, core: pass 24 fail 0 skip 0, harness failures: 0
