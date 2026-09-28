"""Offline launcher for uncapped recall attribution collection and reconciled JSON finalization."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.recall_attribution import main

if __name__ == "__main__":
    sys.exit(main())
