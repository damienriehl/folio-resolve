# User acceptance test report — core-only

- Commit: `4c2444c13eadcc3c5524ae6964a64b884cf7b9b3`
- Python: `3.12.13`
- Extras coverage: not run (core-only report; no extras results inferred).
- Real ontology enabled: `false`
- Metadata source: report-interpreter fallback (JUnit properties absent).

## Rerun commands

- `uv run --offline --frozen --extra dev pytest tests/uat -m uat --junitxml=.codex-out/uat/core.xml`
- `uv run --offline --frozen --extra dev python tests/uat/build_report.py --core-only --stories docs/uat/user-stories.md --out docs/uat/2026-10-26-uat-report.md --core-junit .codex-out/uat/core.xml`

## Story verdicts

| Story ID | Persona | Core verdict | Reason |
|---|---|---|---|
| US-PI-01 | PI | untested |  |
| US-PI-02 | PI | untested |  |
| US-PI-03 | PI | untested |  |
| US-SI-01 | SI | untested |  |
| US-SI-02 | SI | untested |  |
| US-SI-03 | SI | untested |  |
| US-RI-01 | RI | untested |  |
| US-RI-02 | RI | untested |  |
| US-RI-03 | RI | untested |  |
| US-AA-01 | AA | untested |  |
| US-AA-02 | AA | untested |  |
| US-AA-03 | AA | untested |  |
| US-LJ-01 | LJ | untested |  |
| US-LJ-02 | LJ | untested |  |
| US-LJ-03 | LJ | untested |  |
| US-OM-01 | OM | untested |  |
| US-OM-02 | OM | untested |  |
| US-OM-03 | OM | untested |  |
| US-EO-01 | EO | untested |  |
| US-EO-02 | EO | untested |  |
| US-EO-03 | EO | untested |  |
| US-RM-01 | RM | untested |  |
| US-RM-02 | RM | untested |  |
| US-RM-03 | RM | untested |  |

## Skipped testcases

- None.

## Unmapped harness failures

- `tests.uat.test_uat_annotation_app` (fail) — collection failure

## Summary

testcases: pass 0 fail 1 fail (classified) 0 skip 0, untested stories: 24, harness failures: 1

## Execution disposition

The core persona run exited 2 during collection because the offline environment did not contain the locked Pydantic distribution. Its JUnit file records one unmapped collection failure, so no persona story is claimed as tested. Python 3.11 was not cached; the available interpreter was Python 3.12.13. Extras and real-ontology execution were not attempted.

The installed-wheel walk exited 0 but acceptance is **incomplete**: all nine steps (`readme_quickstart`, `scoring_only`, `resolve`, `annotate`, `judge`, `missing_extra`, `typing`, `import_cost`, and `build_metadata`) were skipped because the wheel's locked dependencies were unavailable in the offline cache. No retained post-walk environment was created and no installed-distribution inventory can be claimed.

The focused report-generator regression tests passed (6 passed). The full suite, lint, and type gates could not be established from the frozen environment because the cache lacked `mypy==2.3.0` and other locked packages. This receipt is evidence of an attempted offline rerun and its coverage gap, not an acceptance pass.
