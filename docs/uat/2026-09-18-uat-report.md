# User acceptance test report

- Commit: `20c231f1ad02e7ab7bc1181c32f41a6f4f6ae9bf`
- Python: `3.13.11`
- Extras present: `folio=true`, `spacy=true`, `embedding=false`
- Real ontology enabled: `true`
- Metadata source: JUnit testsuite properties.

## Rerun commands

- `uv run --isolated --extra dev pytest tests/uat -m uat --junitxml=.codex-out/junit/core.xml`
- `FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1 .venv/bin/python -m pytest tests/uat -m uat --junitxml=.codex-out/junit/extras.xml`
- `.venv/bin/python tests/uat/build_report.py --junit .codex-out/junit/extras.xml --stories docs/uat/user-stories.md --out docs/uat/2026-09-18-uat-report.md --core-junit .codex-out/junit/core.xml`

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
| US-EO-02 | EO | fail | pass | AssertionError: path audit failed: home=0, eval-data=7, other-outside-tmp=0 assert not Counter({'eval-data': 7}); core: requires the folio extra and FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1 |
| US-EO-03 | EO | pass | pass |  |
| US-RM-01 | RM | pass | pass |  |
| US-RM-02 | RM | pass | pass |  |
| US-RM-03 | RM | pass | pass |  |

## Unmapped tests

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

extras: pass 23 fail 1 skip 0, core: pass 24 fail 0 skip 0, harness failures: 0

## Findings — US-EO-02 failure classification

**US-EO-02** (`tests/uat/test_uat_synthetic_operator.py::test_us_eo_02_cli_real_ontology_runs_one_shard`)
fails on this rerun with:

```
AssertionError: path audit failed: home=0, eval-data=7, other-outside-tmp=0
assert not Counter({'eval-data': 7})
```

**Classification: library defect.** Root-caused as follows:

- The UAT audit failure is a symptom, not the root cause. The subprocess the test drives
  (`folio_eval.synthetic_score.main`) itself raises `folio_eval.leakcheck.LeakcheckError:
  manifest stale: local gold identity does not match surface manifest` at
  `eval/folio_eval/synthetic_score.py:794` before the harness's own audit assertion ever runs
  (confirmed by invoking the CLI body directly, outside the UAT subprocess harness).
- `synthetic_score.py:794` calls `load_manifest(args.leak_manifest)` with the default
  `allow_stale=False`. That makes `assert_manifest_current` (`eval/folio_eval/leakcheck.py:501`)
  glob and read every `eval/data/gold/gold_v*.manifest.json` on disk (a PROTECTED_ROOTS path —
  hence the "eval-data=7" audit hit) and compare the newest one's `gold_version` against the
  synthetic manifest's `gold_version`. The test's synthetic manifest intentionally sets
  `gold_version="test"` (`tests/uat/test_uat_synthetic_operator.py:86`), a literal placeholder
  that can never match a real `gold_vN` identity, so the comparison always fails whenever any
  local gold manifest is present.
- This is the same defect class already fixed once in a sibling CLI: commit `f295930`
  ("fix(eval): keep the synthetic experiment CLI independent of local firm gold", 2026-09-03)
  patched `folio_eval.experiment.main` to call `load_manifest(args.leak_manifest,
  allow_stale=True)` for exactly this reason — "the synthetic slice records gold_version 0
  ... so a checkout holding a different local gold identity failed before the attempt started."
  `folio_eval/synthetic_score.py`'s own CLI entry point was never given the same fix.
- The unit suite does not catch this: `tests/test_eval_synthetic_score.py` monkeypatches
  `load_manifest` outright (lines 747 and 795), so the real `allow_stale=False` freshness check
  in `synthetic_score.main` is never exercised outside this UAT subprocess test.
- **Not caused by the ranking commits under investigation.** `git show
  4cf7876f7c89965eb23e42687f41f67a73d2d30b:eval/folio_eval/synthetic_score.py` shows the same
  unguarded `load_manifest(args.leak_manifest)` call already present at the 2026-09-02 report
  commit, and `eval/data/gold/gold_v1.manifest.json` through `gold_v7.manifest.json` on this
  machine predate that commit (timestamps 2026-07-27 through 2026-08-17). The defect is
  environment-dependent, not code-dependent on `4e0407e` or `dabd86e`: it only triggers when the
  machine running the suite happens to have local (gitignored, private) `eval/data/gold/*`
  manifests on disk. The 2026-09-02 report was very likely produced on a checkout without that
  gitignored private data present, which is why it recorded a pass.
- **Not attempted here per task scope.** The fix (mirroring `f295930`) would be to add
  `allow_stale=True` to the `load_manifest` call at `synthetic_score.py:794`. That change is not
  made in this branch; it is reported as a finding for a follow-up fix.
