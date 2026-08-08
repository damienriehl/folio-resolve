"""F1 evaluation harness for folio-resolve (docs/plans/2026-07-27-001-feat-f1-improvement-loop-plan.md).

This package never ships in the wheel and is not a runtime dependency of ``folio_resolve`` — it
is invoked directly by test/tooling commands (``pytest`` with ``pythonpath = ["eval"]``, ``mypy
eval``). Workbook-touching code lives only in :mod:`folio_eval.intake`, which imports
``openpyxl`` lazily (function-local) so the rest of the package — and every test outside
``test_eval_intake.py``'s extraction path — runs against synthetic fixtures under the base venv,
per KTD11.
"""

from __future__ import annotations
