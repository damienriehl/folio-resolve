"""Runner for the U6 iteration protocol: ``uv run python eval/run_experiment.py start|finish|status``.

Same launcher shape as ``eval/run_baseline.py`` and ``eval/run_audit.py`` -- the ``eval`` tree is on
``pythonpath`` for pytest and ``mypy_path`` for the type checker, but not on a plain interpreter's
path, so this puts its own directory there and calls ``folio_eval.experiment.main``.

``start`` opens one iteration attempt (hypothesis, targeted cluster, before scores, the KTD8 window
check, the determinism self-test). ``finish`` re-scores, evaluates the AE4 tripwire, and appends the
KTD8 record to ``eval/reports/experiments.jsonl`` -- refusing on a firm-surface leak or on a pending
attempt already in flight. ``status`` reports the check-in tally without touching FOLIO at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.experiment import main  # the path setup above must run before this import

if __name__ == "__main__":
    sys.exit(main())
