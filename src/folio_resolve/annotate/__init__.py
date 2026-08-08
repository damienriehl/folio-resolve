"""Annotate primitives — the feature-rich display + feedback layer.

Per Damien's ``feedback_share`` directive: folio-resolve must be first-class, not a minimal
extraction. This package lifts folio-enrich's confidence scores, per-item feedback, notes,
reject/restore lifecycle, and feedback insights as library primitives — plus the new **per-tag
verdict** (``correct`` / ``weak`` / ``wrong`` + note) the Ch02 review demanded.
"""

from .feedback_store import FeedbackStore
from .lifecycle import (
    bulk_reject,
    cascade_promote,
    promote,
    reject,
    restore,
)
from .models import (
    Annotation,
    ConceptTag,
    FeedbackEntry,
    FeedbackItem,
    InsightsSummary,
    Span,
    StageEvent,
    TagVerdict,
    Verdict,
)
from .render import RenderedSegment, render_segments

__all__ = [
    "Annotation",
    "ConceptTag",
    "FeedbackEntry",
    "FeedbackItem",
    "FeedbackStore",
    "InsightsSummary",
    "RenderedSegment",
    "Span",
    "StageEvent",
    "TagVerdict",
    "Verdict",
    "bulk_reject",
    "cascade_promote",
    "promote",
    "reject",
    "render_segments",
    "restore",
]
