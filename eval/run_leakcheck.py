"""Runner for the U4 leak checker: ``uv run python eval/run_leakcheck.py check --help``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.leakcheck import main  # the path setup above must run before this import

if __name__ == "__main__":
    sys.exit(main())
