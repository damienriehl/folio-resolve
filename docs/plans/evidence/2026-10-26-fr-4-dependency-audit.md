# fr-4 dependency audit — 2026-10-26

## Scope and identity

Audit date: 2026-10-26. Package version `0.4.0`; supported Python `>=3.11`. `pyproject.toml` and `uv.lock` were read without modification. The lock contains 105 package records and is the frozen dependency authority; alternate marker-selected entries are not described as simultaneously installed.

## Declared and locked coverage

| Area | Declaration | Lock observation | Execution disposition |
|---|---|---|---|
| Core | `pydantic>=2.7,<3.0` | Pydantic 2.13.4 | Not installed in the available Python 3.12 environment; core UAT collection stopped. |
| Dev | pytest, pytest-asyncio, mypy, Ruff | pytest 9.1.1, pytest-asyncio 1.4.0, mypy 2.3.0, Ruff 0.15.21 | A non-frozen host pytest ran focused reporter checks. Frozen dev sync was unavailable because mypy 2.3.0 was absent from cache. |
| `folio` extra | `folio-python[search]>=0.1.5` | folio-python 0.3.6 | Locked, not installed or exercised. |
| `embedding` extra | faiss-cpu, sentence-transformers, NumPy | faiss-cpu 1.14.3, sentence-transformers 3.4.1, marker-specific NumPy 2.4.6 and 2.5.1 | Locked, not installed or exercised. NumPy alternatives are marker selections, not one installed inventory. |
| `spacy` extra | `spacy>=3.7,<4.0` | spaCy 3.8.14 | Locked, not installed or exercised. |
| Build | Hatchling backend | Build requirement is declared outside the project dependency records | Offline build could not provision its dependencies; all nine installed-wheel checks were skipped. |

Python 3.11 was unavailable; checks used the present Python 3.12.13 only where explicitly stated. No post-walk installed inventory exists because installation did not complete.

## Compatibility and advisory evidence

The focused report-generator tests passed under the host test runner, but this is not frozen-environment compatibility evidence. The frozen sync and full checks could not run from cache. No `pip-audit` executable or complete cached advisory database was available, and network lookup was prohibited, so **known-vulnerability coverage is unavailable** rather than clean. Checked advisory packages: 0; skipped/unqueried locked package records: 105. No advisory identifiers, affected versions, or fixed versions can truthfully be reported.

Controller follow-up: in a network-permitted audit-only environment, export exact marker-selected pins from `uv.lock`, run `pip-audit` with dependency resolution disabled and JSON output, and record tool/database versions, lookup failures, checked count, and marker scope. Do not apply fixes as part of this receipt.

No constraint, lock entry, package version, build configuration, or release metadata was changed.
