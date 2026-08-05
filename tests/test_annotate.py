"""Annotate primitives — per-tag verdicts, lifecycle, feedback store, render."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from folio_resolve.annotate import (
    Annotation,
    ConceptTag,
    FeedbackItem,
    Span,
    StageEvent,
    TagVerdict,
    Verdict,
    bulk_reject,
    cascade_promote,
    promote,
    reject,
    render_segments,
    restore,
)
from folio_resolve.annotate.feedback_store import FeedbackStore
from folio_resolve.annotate.models import FeedbackEntry


def _ann(iri: str, start: int, end: int, text: str, conf: float = 0.9) -> Annotation:
    return Annotation(
        span=Span(start=start, end=end, text=text),
        concepts=[ConceptTag(iri=iri, label=text, confidence=conf)],
    )


def test_per_tag_verdict_model() -> None:
    v = TagVerdict(unit_id="u1", tag_iri="R1", verdict=Verdict.WRONG, note="Action != Auction")
    assert v.verdict == "wrong"
    assert v.note


def test_reject_restore_lifecycle() -> None:
    ann = _ann("R1", 0, 5, "court")
    reject(ann, comment="not relevant")
    assert ann.state == "rejected"
    assert ann.dismissed_at is not None
    assert ann.lineage[-1].action == "user_rejected"
    restore(ann)
    assert ann.state == "confirmed"
    assert ann.dismissed_at is None
    assert ann.lineage[-1].action == "user_restored"


def test_promote_swaps_primary() -> None:
    ann = Annotation(
        span=Span(start=0, end=3, text="law"),
        concepts=[ConceptTag(iri="R1", label="A"), ConceptTag(iri="R2", label="B")],
    )
    promote(ann, 1)
    assert ann.primary_iri == "R2"


def test_cascade_promote() -> None:
    anns = [
        Annotation(
            span=Span(start=0, end=3, text="x"),
            concepts=[ConceptTag(iri="R-old", label="old"), ConceptTag(iri="R-new", label="new")],
        )
        for _ in range(3)
    ]
    updated = cascade_promote(anns, old_iri="R-old", new_iri="R-new")
    assert len(updated) == 3
    assert all(a.primary_iri == "R-new" for a in anns)


def test_bulk_reject() -> None:
    anns = [_ann("R-bad", 0, 1, "a"), _ann("R-bad", 2, 3, "b"), _ann("R-ok", 4, 5, "c")]
    rejected = bulk_reject(anns, folio_iri="R-bad")
    assert len(rejected) == 2
    assert anns[2].state != "rejected"


def test_render_segments_non_overlapping() -> None:
    text = "cross examination of the expert witness"
    anns = [
        _ann("R-cross", 0, 17, "cross examination"),
        _ann("R-witness", 25, 39, "expert witness"),
    ]
    segments = render_segments(text, anns)
    # Segments partition the text with no gaps/overlaps.
    assert segments[0].start == 0
    assert segments[-1].end == len(text)
    for a, b in pairwise(segments):
        assert a.end == b.start


def test_feedback_store_roundtrip_and_insights(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(FeedbackEntry(job_id="j1", annotation_id="a1", rating="down", folio_iri="R1", folio_label="X"))
    store.save(FeedbackEntry(job_id="j1", annotation_id="a2", rating="up", folio_iri="R2"))
    store.save(FeedbackEntry(job_id="j1", annotation_id="a3", rating="dismissed", folio_iri="R1", folio_label="X"))
    insights = store.get_insights("j1")
    assert insights.total_feedback == 3
    assert insights.thumbs_up == 1
    assert insights.thumbs_down == 1
    assert insights.total_dismissed == 1
    assert insights.most_downvoted_concepts[0]["iri"] == "R1"


# -- models: identity, defaults, and the per-tag verdict layer -----------


def test_every_record_gets_a_unique_id_and_a_utc_timestamp() -> None:
    a, b = _ann("R1", 0, 1, "a"), _ann("R1", 0, 1, "a")
    assert a.id != b.id
    verdict = TagVerdict(unit_id="u1", tag_iri="R1", verdict=Verdict.CORRECT)
    assert verdict.id != TagVerdict(unit_id="u1", tag_iri="R1", verdict=Verdict.CORRECT).id
    assert verdict.reviewed_at.endswith("+00:00")  # timezone-aware, not a naive local stamp


def test_verdict_wire_values_are_stable() -> None:
    # Persisted in verdict packs and replayed as regression fixtures.
    assert [v.value for v in Verdict] == ["correct", "weak", "wrong"]


def test_a_verdict_carries_everything_the_self_improving_loop_needs() -> None:
    # A `wrong` verdict has to be able to become an alias-blocklist entry AND a calibration
    # sample AND a regression fixture, so surface, IRI, score and provenance all travel with it.
    v = TagVerdict(
        unit_id="u1",
        run_id="r1",
        corpus_name="treatise",
        tag_iri="R-auction",
        tag_label="Auction",
        extraction_path="entity_ruler",
        match_score=92.3,
        verdict=Verdict.WRONG,
        note="Action != Auction",
        domain_prior="Litigation",
        book="b",
        chapter="2",
        reviewer="dr",
    )
    assert (v.tag_iri, v.match_score, v.verdict) == ("R-auction", 92.3, "wrong")
    assert v.model_dump()["verdict"] == "wrong"  # round-trips as a plain string


def test_an_annotation_without_concepts_has_no_primary_iri() -> None:
    assert Annotation(span=Span(start=0, end=1, text="x")).primary_iri == ""
    assert Annotation(span=Span(start=0, end=1, text="x")).state == "preliminary"


def test_span_and_concept_tag_defaults() -> None:
    span = Span(start=0, end=5, text="court")
    assert span.sentence_text is None
    tag = ConceptTag(iri="R1", label="Court")
    assert (tag.confidence, tag.branch, tag.extraction_path, tag.match_score, tag.span) == (
        0.0,
        "",
        "",
        None,
        None,
    )


def test_mutable_defaults_are_not_shared_between_instances() -> None:
    a, b = _ann("R1", 0, 1, "a"), _ann("R2", 0, 1, "b")
    a.lineage.append(StageEvent(stage="s", action="created"))
    assert b.lineage == []


def test_annotations_round_trip_through_json() -> None:
    ann = _ann("R1", 0, 5, "court")
    reject(ann, comment="not relevant")
    restored = Annotation.model_validate_json(ann.model_dump_json())
    assert restored == ann


# -- lifecycle edges -----------------------------------------------------


def test_reject_records_the_comment_on_the_lineage() -> None:
    ann = _ann("R1", 0, 5, "court")
    reject(ann, comment="wrong sense")
    assert ann.lineage[-1].detail == "wrong sense"
    assert ann.lineage[-1].stage == "user"


def test_restore_clears_stale_feedback() -> None:
    # Feedback was given against the rejected reading; restoring invalidates it.
    ann = _ann("R1", 0, 5, "court")
    ann.feedback.append(FeedbackItem(rating="down", comment="bad"))
    reject(ann)
    restore(ann)
    assert ann.feedback == []


def test_the_lineage_accumulates_rather_than_replacing() -> None:
    ann = _ann("R1", 0, 5, "court")
    reject(ann)
    restore(ann)
    reject(ann)
    assert [e.action for e in ann.lineage] == ["user_rejected", "user_restored", "user_rejected"]


def test_promote_confirms_and_preserves_the_other_candidates() -> None:
    ann = Annotation(
        span=Span(start=0, end=3, text="law"),
        concepts=[ConceptTag(iri="R1", label="A"), ConceptTag(iri="R2", label="B"), ConceptTag(iri="R3", label="C")],
    )
    promote(ann, 2)
    assert [c.iri for c in ann.concepts] == ["R3", "R1", "R2"]
    assert ann.state == "confirmed"
    assert ann.lineage[-1].detail == "promoted R3"


def test_promoting_the_current_primary_is_a_no_op_reorder() -> None:
    ann = Annotation(
        span=Span(start=0, end=3, text="law"),
        concepts=[ConceptTag(iri="R1", label="A"), ConceptTag(iri="R2", label="B")],
    )
    promote(ann, 0)
    assert [c.iri for c in ann.concepts] == ["R1", "R2"]
    assert ann.state == "confirmed"


@pytest.mark.parametrize("index", [-1, 2, 99])
def test_promote_rejects_an_out_of_range_index(index: int) -> None:
    ann = Annotation(
        span=Span(start=0, end=3, text="law"),
        concepts=[ConceptTag(iri="R1", label="A"), ConceptTag(iri="R2", label="B")],
    )
    with pytest.raises(IndexError, match="out of range"):
        promote(ann, index)
    assert [c.iri for c in ann.concepts] == ["R1", "R2"]  # untouched


def test_promote_on_a_concept_less_annotation_raises() -> None:
    with pytest.raises(IndexError):
        promote(Annotation(span=Span(start=0, end=1, text="x")), 0)


def test_cascade_skips_annotations_whose_primary_is_not_the_old_iri() -> None:
    match = Annotation(
        span=Span(start=0, end=1, text="x"),
        concepts=[ConceptTag(iri="R-old", label="old"), ConceptTag(iri="R-new", label="new")],
    )
    other = Annotation(
        span=Span(start=0, end=1, text="x"),
        concepts=[ConceptTag(iri="R-unrelated", label="u"), ConceptTag(iri="R-new", label="new")],
    )
    updated = cascade_promote([match, other], old_iri="R-old", new_iri="R-new")
    assert updated == [match]
    assert other.primary_iri == "R-unrelated"
    assert other.lineage == []


def test_cascade_skips_annotations_that_never_offered_the_new_iri() -> None:
    # The correction cannot be applied where the alternative was never a candidate.
    ann = Annotation(
        span=Span(start=0, end=1, text="x"), concepts=[ConceptTag(iri="R-old", label="old")]
    )
    assert cascade_promote([ann], old_iri="R-old", new_iri="R-new") == []
    assert ann.primary_iri == "R-old"


def test_cascade_records_both_the_promotion_and_its_cascade_provenance() -> None:
    ann = Annotation(
        span=Span(start=0, end=1, text="x"),
        concepts=[ConceptTag(iri="R-old", label="old"), ConceptTag(iri="R-new", label="new")],
    )
    cascade_promote([ann], old_iri="R-old", new_iri="R-new")
    assert [e.detail for e in ann.lineage] == ["promoted R-new", "Cascade: R-old -> R-new"]


def test_cascade_over_nothing() -> None:
    assert cascade_promote([], old_iri="R-old", new_iri="R-new") == []


def test_bulk_reject_skips_already_rejected_annotations() -> None:
    already = _ann("R-bad", 0, 1, "a")
    reject(already, comment="first pass")
    fresh = _ann("R-bad", 2, 3, "b")
    rejected = bulk_reject([already, fresh], folio_iri="R-bad", comment="sweep")
    assert rejected == [fresh.id]
    assert len(already.lineage) == 1  # not re-stamped


def test_bulk_reject_matches_on_the_primary_iri_only() -> None:
    backup_only = Annotation(
        span=Span(start=0, end=1, text="x"),
        concepts=[ConceptTag(iri="R-ok", label="ok"), ConceptTag(iri="R-bad", label="bad")],
    )
    assert bulk_reject([backup_only], folio_iri="R-bad") == []
    assert backup_only.state == "preliminary"


def test_bulk_reject_propagates_the_comment() -> None:
    ann = _ann("R-bad", 0, 1, "a")
    bulk_reject([ann], folio_iri="R-bad", comment="homonym sweep")
    assert ann.lineage[-1].detail == "homonym sweep"


# -- render_segments: the boundary sweep ---------------------------------


def test_render_segments_of_unannotated_text_is_one_bare_segment() -> None:
    segments = render_segments("cross examination", [])
    assert len(segments) == 1
    assert (segments[0].start, segments[0].end, segments[0].annotation_ids) == (0, 17, ())
    assert segments[0].text == "cross examination"


def test_render_segments_of_empty_text_is_empty() -> None:
    assert render_segments("", [_ann("R1", 0, 5, "x")]) == []


def test_segments_carry_the_exact_covering_annotations() -> None:
    text = "cross examination of the expert witness"
    cross = _ann("R-cross", 0, 17, "cross examination")
    witness = _ann("R-witness", 25, 39, "expert witness")
    by_span = {(s.start, s.end): s for s in render_segments(text, [cross, witness])}
    assert by_span[(0, 17)].annotation_ids == (cross.id,)
    assert by_span[(17, 25)].annotation_ids == ()  # " of the "
    assert by_span[(25, 39)].annotation_ids == (witness.id,)


def test_segment_text_always_matches_its_offsets() -> None:
    text = "cross examination of the expert witness"
    for seg in render_segments(text, [_ann("R1", 0, 17, "x"), _ann("R2", 6, 25, "y")]):
        assert seg.text == text[seg.start : seg.end]


def test_nested_spans_produce_a_segment_covered_by_both() -> None:
    text = "cross-examination begins"
    outer = _ann("R-outer", 0, 17, "cross-examination", conf=0.9)
    inner = _ann("R-inner", 0, 5, "cross", conf=0.7)
    segments = render_segments(text, [outer, inner])
    assert [(s.start, s.end) for s in segments] == [(0, 5), (5, 17), (17, 24)]
    assert set(segments[0].annotation_ids) == {outer.id, inner.id}
    assert segments[1].annotation_ids == (outer.id,)


def test_covering_annotations_are_ordered_highest_confidence_first() -> None:
    text = "cross-examination"
    low = _ann("R-low", 0, 17, "x", conf=0.2)
    high = _ann("R-high", 0, 17, "x", conf=0.95)
    mid = _ann("R-mid", 0, 17, "x", conf=0.6)
    ids = render_segments(text, [low, high, mid])[0].annotation_ids
    assert ids == (high.id, mid.id, low.id)


def test_duplicate_iris_collapse_to_the_most_confident_annotation() -> None:
    # The same concept found twice over one span is one chip, not two.
    text = "cross-examination"
    weak = _ann("R-same", 0, 17, "x", conf=0.4)
    strong = _ann("R-same", 0, 17, "x", conf=0.9)
    ids = render_segments(text, [weak, strong])[0].annotation_ids
    assert ids == (strong.id,)


def test_annotations_without_concepts_are_kept_separately() -> None:
    # They have no IRI to dedup on, so each must keep its own slot rather than colliding.
    text = "cross-examination"
    a = Annotation(span=Span(start=0, end=17, text=text))
    b = Annotation(span=Span(start=0, end=17, text=text))
    assert set(render_segments(text, [a, b])[0].annotation_ids) == {a.id, b.id}


def test_spans_outside_the_text_do_not_create_bogus_segments() -> None:
    text = "short"
    segments = render_segments(text, [_ann("R1", 100, 200, "way past the end")])
    assert [(s.start, s.end) for s in segments] == [(0, 5)]
    assert segments[0].annotation_ids == ()


def test_a_span_overrunning_the_end_is_clamped_and_still_covers() -> None:
    text = "cross"
    ann = _ann("R1", 0, 99, "cross...")
    segments = render_segments(text, [ann])
    assert [(s.start, s.end) for s in segments] == [(0, 5)]
    assert segments[0].annotation_ids == (ann.id,)


def test_a_negative_start_is_clamped_to_zero() -> None:
    text = "cross"
    ann = _ann("R1", -5, 5, "cross")
    segments = render_segments(text, [ann])
    assert [(s.start, s.end) for s in segments] == [(0, 5)]
    assert segments[0].annotation_ids == (ann.id,)


def test_segments_partition_the_text_exactly() -> None:
    text = "the burden of proof rests with the moving party"
    anns = [_ann("R-a", 4, 19, "burden of proof"), _ann("R-b", 8, 30, "of proof rests with the")]
    segments = render_segments(text, anns)
    assert segments[0].start == 0
    assert segments[-1].end == len(text)
    assert "".join(s.text for s in segments) == text
    for a, b in pairwise(segments):
        assert a.end == b.start


def test_rendered_segments_are_hashable_value_objects() -> None:
    seg = render_segments("court", [])[0]
    assert hash(seg)
    with pytest.raises(AttributeError):
        seg.start = 3  # type: ignore[misc]
