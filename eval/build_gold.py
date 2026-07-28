"""Runner for the gold builder: ``uv run python eval/build_gold.py`` (U2).

The ``eval`` tree is on ``pythonpath`` for pytest and on ``mypy_path`` for the type checker, but
not on a plain interpreter's path, so this launcher puts its own directory there and calls
``folio_eval.gold.main``. Equivalent to ``PYTHONPATH=eval uv run python -m folio_eval.gold``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.gold import main  # the path setup above must run before this import

if __name__ == "__main__":
    sys.exit(main())
