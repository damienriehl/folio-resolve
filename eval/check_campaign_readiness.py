"""Offline launcher for the content-free campaign attestation summary."""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.campaign_readiness import main

if __name__ == "__main__":
    sys.exit(main())
