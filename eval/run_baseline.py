"""Runner for the U4 baseline: ``uv run python eval/run_baseline.py``.

Same launcher shape as ``eval/run_score.py`` — the ``eval`` tree is on ``pythonpath`` for pytest
and ``mypy_path`` for the type checker, but not on a plain interpreter's path, so this puts its
own directory there and calls ``folio_eval.clusters.main``. Equivalent to
``PYTHONPATH=eval uv run python -m folio_eval.clusters``.

The same KTD7 gates run first as for scoring: ``PYTHONHASHSEED=0`` (re-exec if unset), the
ontology pin, and the determinism self-test. The run fits the answer rule on the **tune slice
only** (KTD2), scores tune + the Firm-2 signal slice, clusters every miss by cause (R8), and
writes the committed baseline report plus gitignored row-level detail. The frozen slice is never
touched here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.clusters import main  # the path setup above must run before this import

if __name__ == "__main__":
    sys.exit(main())
