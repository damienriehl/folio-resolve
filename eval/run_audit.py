"""Runner for the U5 audit gate: ``uv run python eval/run_audit.py``.

Same launcher shape as ``eval/run_baseline.py`` — the ``eval`` tree is on ``pythonpath`` for
pytest and ``mypy_path`` for the type checker, but not on a plain interpreter's path, so this
puts its own directory there and calls ``folio_eval.audit.main``.

``--mode packet`` (the default) assembles the decision packet from the landed gold, split
manifest, suspects, resolution batch, and U4 clusters, running the pipeline over the suspects and
the eligible blank rows; it writes the gitignored packet under ``eval/data/reports/``.
``--mode fold --decisions <file>`` folds Damien's answers into the next gold version and appends
the ID-keyed records to the committed ``eval/reports/gold_decisions.jsonl``.
``--mode sitting --clusters <clusters_v2.jsonl> --lane firm`` auto-folds safe pairing pre-checks
and renders the requested ranked, at-most-25-row sitting plus its JSON manifest. Packet and
sitting regeneration always require an explicit clusters path so v1 cannot be selected silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.audit import main  # the path setup above must run before this import

if __name__ == "__main__":
    sys.exit(main())
