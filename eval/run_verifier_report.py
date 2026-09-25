"""Offline launcher for the Stage 1 verifier ceiling report."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.verifier_report import main

if __name__ == "__main__":
    sys.exit(main())
