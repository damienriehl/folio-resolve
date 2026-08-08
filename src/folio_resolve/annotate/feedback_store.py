"""File-backed feedback store with insights aggregation.

Ported from folio-enrich ``storage/feedback_store.py``: one JSON file per feedback entry, written
atomically (tempfile + rename), plus ``get_insights`` aggregation. Synchronous here (no aiofiles
dependency) — the atomic-rename guarantee is preserved.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from .models import FeedbackEntry, InsightsSummary

# A feedback id becomes a filename. Ids default to a uuid4, but `FeedbackEntry.id` is settable
# and, in the FastAPI consumers this store was lifted from, reachable from a request path — so
# an id like "../../secrets" would have written and read outside the store's directory.
_SAFE_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class FeedbackStore:
    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, feedback_id: str) -> Path:
        if not isinstance(feedback_id, str) or not _SAFE_ID_RE.match(feedback_id) or ".." in feedback_id:
            raise ValueError(
                f"unsafe feedback id {feedback_id!r}: expected [A-Za-z0-9._-] starting "
                "alphanumeric (a uuid4 by default)"
            )
        return self._base / f"{feedback_id}.json"

    def save(self, entry: FeedbackEntry) -> None:
        path = self._path(entry.id)
        fd, tmp = tempfile.mkstemp(dir=self._base, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(entry.model_dump_json(indent=2))
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def load(self, feedback_id: str) -> FeedbackEntry | None:
        path = self._path(feedback_id)
        if not path.exists():
            return None
        return FeedbackEntry.model_validate_json(path.read_text(encoding="utf-8"))

    def list_all(self) -> list[FeedbackEntry]:
        """Every stored entry, in a stable (filename) order.

        ``Path.glob`` yields directory order, which is filesystem-dependent — that made
        ``find_by_annotation`` and the ``most_common`` / ``recent_feedback`` tie-breaks in
        :meth:`get_insights` differ between machines for identical stored data.
        """
        out: list[FeedbackEntry] = []
        for p in sorted(self._base.glob("*.json")):
            out.append(FeedbackEntry.model_validate_json(p.read_text(encoding="utf-8")))
        return out

    def delete(self, feedback_id: str) -> bool:
        path = self._path(feedback_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def find_by_annotation(self, job_id: str, annotation_id: str) -> FeedbackEntry | None:
        for entry in self.list_all():
            if entry.job_id == job_id and entry.annotation_id == annotation_id:
                return entry
        return None

    def get_insights(self, job_id: str | None = None) -> InsightsSummary:
        entries = [e for e in self.list_all() if job_id is None or e.job_id == job_id]
        summary = InsightsSummary(total_feedback=len(entries))
        by_stage: dict[str, dict[str, int]] = {}
        down_counter: Counter[str] = Counter()
        dismissed_counter: Counter[str] = Counter()
        down_labels: dict[str, str] = {}
        for e in entries:
            if e.rating == "up":
                summary.thumbs_up += 1
            elif e.rating == "down":
                summary.thumbs_down += 1
            elif e.rating == "dismissed":
                summary.total_dismissed += 1
            bucket = by_stage.setdefault(e.stage or "overall", {"up": 0, "down": 0, "dismissed": 0})
            if e.rating in bucket:
                bucket[e.rating] += 1
            if e.rating == "down" and e.folio_iri:
                down_counter[e.folio_iri] += 1
                down_labels[e.folio_iri] = e.folio_label or ""
            if e.rating == "dismissed" and e.folio_iri:
                dismissed_counter[e.folio_iri] += 1
                down_labels.setdefault(e.folio_iri, e.folio_label or "")
        summary.by_stage = by_stage
        summary.most_downvoted_concepts = [
            {"iri": iri, "label": down_labels.get(iri, ""), "count": n}
            for iri, n in down_counter.most_common(10)
        ]
        summary.most_dismissed_concepts = [
            {"iri": iri, "label": down_labels.get(iri, ""), "count": n}
            for iri, n in dismissed_counter.most_common(10)
        ]
        summary.recent_feedback = sorted(entries, key=lambda e: e.created_at, reverse=True)[:20]
        return summary
