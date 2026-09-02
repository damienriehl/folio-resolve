from __future__ import annotations

from pathlib import Path

from folio_resolve.annotate import (
    Annotation,
    ConceptTag,
    FeedbackEntry,
    FeedbackStore,
    Span,
    promote,
    reject,
    render_segments,
    restore,
)


def test_us_aa_01_review_confidence_and_public_selection() -> None:
    """US-AA-01 exposes confidence and promotes one selected concept through the public API."""
    arbitration = ConceptTag(
        iri="R-arbitration-rules",
        label="Arbitration Rules",
        confidence=0.99,
        match_score=99.0,
    )
    defenses = ConceptTag(
        iri="R-litigation-defenses",
        label="Litigation Defenses",
        confidence=0.88,
        match_score=88.0,
    )
    annotation = Annotation(
        span=Span(start=0, end=20, text="rules of arbitration"),
        concepts=[arbitration, defenses],
    )

    assert [(tag.iri, tag.confidence) for tag in annotation.concepts] == [
        ("R-arbitration-rules", 0.99),
        ("R-litigation-defenses", 0.88),
    ]

    returned = promote(annotation, 1)

    assert returned is annotation
    assert annotation.state == "confirmed"
    assert annotation.primary_iri == defenses.iri
    assert annotation.concepts == [defenses, arbitration]
    assert annotation.lineage[-1].action == "user_promotion"
    assert annotation.lineage[-1].detail == f"promoted {defenses.iri}"


def test_us_aa_02_reject_and_restore_tags(tmp_path: Path) -> None:
    """US-AA-02 rejects and restores one tag while persisting both review events."""
    tag = ConceptTag(
        iri="R-auction",
        label="Auction",
        confidence=0.92,
        match_score=92.3,
    )
    annotation = Annotation(
        id="annotation-action",
        span=Span(start=0, end=6, text="Action"),
        concepts=[tag],
        state="confirmed",
    )
    store = FeedbackStore(tmp_path)

    reject(annotation, comment="Action is not Auction")
    store.save(
        FeedbackEntry(
            id="reject-event",
            job_id="synthetic-review",
            annotation_id=annotation.id,
            rating="dismissed",
            comment=annotation.lineage[-1].detail,
            annotation_text=annotation.span.text,
            folio_iri=annotation.primary_iri,
            folio_label=annotation.concepts[0].label,
            lineage=[event.model_dump() for event in annotation.lineage],
        )
    )
    assert annotation.state == "rejected"

    restore(annotation)
    store.save(
        FeedbackEntry(
            id="restore-event",
            job_id="synthetic-review",
            annotation_id=annotation.id,
            rating="up",
            comment="restored after reviewer reconsideration",
            annotation_text=annotation.span.text,
            folio_iri=annotation.primary_iri,
            folio_label=annotation.concepts[0].label,
            lineage=[event.model_dump() for event in annotation.lineage],
        )
    )

    assert annotation.state == "confirmed"
    assert annotation.primary_iri == "R-auction"
    assert annotation.concepts[0].confidence == 0.92
    assert [event.action for event in annotation.lineage] == [
        "user_rejected",
        "user_restored",
    ]
    assert [entry.rating for entry in store.list_all()] == ["dismissed", "up"]


def test_us_aa_03_persist_notes_and_derive_insights(tmp_path: Path) -> None:
    """US-AA-03 round-trips a note and deterministically renders review insights."""
    text = "Arbitration rules govern the hearing."
    arbitration = Annotation(
        id="annotation-arbitration",
        span=Span(start=0, end=17, text="Arbitration rules"),
        concepts=[
            ConceptTag(
                iri="R-arbitration-rules",
                label="Arbitration Rules",
                confidence=0.99,
            )
        ],
    )
    hearing = Annotation(
        id="annotation-hearing",
        span=Span(start=29, end=36, text="hearing"),
        concepts=[ConceptTag(iri="R-hearing", label="Hearing", confidence=0.85)],
    )
    segments = render_segments(text, [arbitration, hearing])

    store = FeedbackStore(tmp_path)
    note = FeedbackEntry(
        id="review-note",
        job_id="synthetic-review",
        annotation_id=hearing.id,
        rating="down",
        stage="label_search",
        comment="The heading names procedure, not a hearing event.",
        annotation_text=hearing.span.text,
        folio_iri=hearing.primary_iri,
        folio_label=hearing.concepts[0].label,
        created_at="2026-09-02T12:00:00+00:00",
    )
    store.save(note)

    loaded = store.load(note.id)
    first_summary = store.get_insights("synthetic-review")
    second_summary = store.get_insights("synthetic-review")

    assert loaded is not None and loaded.comment == note.comment
    assert "".join(segment.text for segment in segments) == text
    assert segments[0].annotation_ids == (arbitration.id,)
    assert first_summary.model_dump() == second_summary.model_dump()
    assert first_summary.total_feedback == 1
    assert first_summary.most_downvoted_concepts == [
        {"iri": "R-hearing", "label": "Hearing", "count": 1}
    ]
