"""Runner for U9's downstream validation: ``uv run python eval/run_downstream.py <snapshot|diff> ...``.

Same launcher shape as ``eval/run_baseline.py`` and ``eval/run_score.py`` -- puts ``eval/`` on
``sys.path`` (mirroring the ``pythonpath = ["eval"]`` pytest config and ``mypy_path = "eval:src"``)
so a plain interpreter invocation resolves ``folio_eval`` the same way the gates do, then calls
``folio_eval.downstream.main``.

Examples:
    uv run python eval/run_downstream.py snapshot --gold eval/data/gold/gold_v1.jsonl
    uv run python eval/run_downstream.py diff \\
        --before eval/data/reports/downstream_baseline/folio-mapper/snapshot.json \\
        --after  eval/data/reports/downstream_baseline/folio-mapper/snapshot.json
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.downstream import main  # the path setup above must run before this import

if __name__ == "__main__":
    sys.exit(main())
