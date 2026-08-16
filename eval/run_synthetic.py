"""Offline launcher for the pinned synthetic scoring lane (U8)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.synthetic_score import main

if __name__ == "__main__":
    sys.exit(main())
