---
title: Synthetic scorer manifest fix verification
lane: evaluation
tags: [eval, synthetic-benchmark, leakcheck, regression]
status: active
related: [docs/plans/2026-09-18-fix-synthetic-score-manifest-freshness.md]
---

# Synthetic scorer manifest fix: verification

The synthetic scorer CLI now loads its surface manifest with `allow_stale=True`,
matching the experiment CLI fix in `f295930`. This skips local firm-gold identity
freshness binding; manifest validation and publication leak scanning remain active.

## Observed red before production changes

A direct real-loader call with a synthetic manifest and the checkout's seven local
gold manifests failed with:

```text
folio_eval.leakcheck.LeakcheckError: manifest stale: local gold identity does not match surface manifest
```

The original real-ontology UAT story also failed with:

```text
AssertionError: path audit failed: home=0, eval-data=7, other-outside-tmp=0
1 failed, 3 deselected in 0.47s
```

The new regression uses the real loader and real publication preflight. Only local
gold discovery is redirected to a temporary mismatched manifest. Both benign and
planted-collision cases failed at `synthetic_score.py:794` before the fix:

```text
uv run --isolated --extra dev pytest tests/test_eval_synthetic_score.py -k test_main_ignores_local_firm_gold_freshness -v --tb=short
2 failed, 30 deselected in 0.09s
```

Both failures were the stale-manifest error above, not assertion or import failures.

## Observed green after the fix

| Command | Result |
| --- | --- |
| Same focused regression command | `2 passed, 30 deselected in 0.06s` |
| `uv run --isolated --extra dev pytest tests/test_eval_synthetic_score.py tests/test_eval_experiment.py tests/test_eval_leakcheck.py -q` | `112 passed in 0.25s` |
| `uv run --isolated --extra dev pytest tests/uat -m uat -q` | `31 passed, 3 skipped in 0.59s` |
| `FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1 .venv/bin/python -m pytest tests/uat -m uat -q` | `34 passed in 26.48s` |
| `uv run --isolated --extra dev ruff check eval/folio_eval/synthetic_score.py tests/test_eval_synthetic_score.py` | `All checks passed!` |
| `git diff --check` | Exit 0 |

The collision case still raises `SyntheticScoringError` with a leak-check failure;
the benign case reaches ontology construction. Existing checkpoint test stubs accept
the loader keyword and supply a dummy optional ontology module for core-only tests.

## Broader-suite limitation

`uv run --isolated --extra dev pytest -q` returned
`22 failed, 1274 passed, 4 skipped in 55.29s`. All failures were in the unchanged
campaign-report tests and involved their own local gold freshness binding. Loading
the original scorer source from `main` into the test process, without editing the
working tree, reproduced the same campaign-report failures:
`22 failed, 33 passed in 0.10s`.

This does not establish a fully green repository suite. Campaign-report changes and
U10 comparison v2 leak triage remain outside this fix.

## Review receipt

ce-code-review receipt: Codex review `.codex-out/reviews/synthetic-score-2026-09-19/verdict.txt`, verdict `merge`

The review prompt, complete reviewer output, and extracted verdict are retained in
that gitignored local directory as `prompt.md`, `review.txt`, and `verdict.txt`.
These are machine-local artifacts, not part of the commit. Review prose is excluded
from this repository artifact under the project's review-retention convention.
