"""Runner for the scoring harness: ``uv run python eval/run_score.py --slice tune`` (U3).

The ``eval`` tree is on ``pythonpath`` for pytest and on ``mypy_path`` for the type checker, but
not on a plain interpreter's path, so this launcher puts its own directory there and calls
``folio_eval.score.main``. Equivalent to ``PYTHONPATH=eval uv run python -m folio_eval.score``.

Every invocation runs the KTD7 gates first: ``PYTHONHASHSEED=0`` (re-exec if unset), the ontology
pin (cache file resolved, hashed, compared against the gold manifest), and the determinism
self-test (second pass in a subprocess under a different hash seed). Scoring the frozen slice
additionally requires ``--frozen-final``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.score import main  # the path setup above must run before this import

if __name__ == "__main__":
    sys.exit(main())
