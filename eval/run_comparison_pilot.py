#!/usr/bin/env python3
"""CLI wrapper for the power-loss-safe U10 comparison pilot."""

import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from folio_eval.comparison_pilot import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
