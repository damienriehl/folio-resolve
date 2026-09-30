"""Confidence, per-tag feedback, lifecycle and persisted review insights."""
from folio_resolve.annotate import (
    Annotation,
    ConceptTag,
    FeedbackEntry,
    FeedbackStore,
    Span,
    TagVerdict,
    Verdict,
    reject,
    restore,
)

annotation = Annotation(
    id="action", span=Span(start=0, end=6, text="Action"), state="confirmed",
    concepts=[ConceptTag(iri="R-auction", label="Auction", confidence=0.92, match_score=92.3)],
)
verdict = TagVerdict(
    unit_id=annotation.id, tag_iri="R-auction", verdict=Verdict.WRONG,
    note="Action is not Auction", match_score=92.3,
)
assert TagVerdict.model_validate_json(verdict.model_dump_json()) == verdict
assert verdict.tag_iri == annotation.primary_iri and verdict.verdict == Verdict.WRONG
reject(annotation, comment=verdict.note)
assert annotation.state == "rejected"
restore(annotation)
assert annotation.state == "confirmed"
assert annotation.concepts[0].confidence == 0.92
assert [event.action for event in annotation.lineage] == ["user_rejected", "user_restored"]
store = FeedbackStore("feedback")
note = FeedbackEntry(
    id="note", job_id="review", annotation_id=annotation.id, rating="down",
    comment=verdict.note, folio_iri=annotation.primary_iri, folio_label="Auction",
)
store.save(note)
assert store.load("note").comment == verdict.note
insights = store.get_insights("review")
assert insights.total_feedback == 1 and insights.thumbs_down == 1
assert insights.most_downvoted_concepts == [{"iri": "R-auction", "label": "Auction", "count": 1}]
print("confidence, per-tag verdict/note, reject/restore, persisted notes and insights passed")
