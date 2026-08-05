"""File-backed feedback store — atomic writes, id safety, and insights aggregation.

Ported from folio-enrich's ``storage/feedback_store.py``: one JSON file per entry, written via
tempfile + rename. Its consumers are web APIs, so the id-to-filename mapping is treated as
untrusted input here. All feedback content is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from folio_resolve.annotate.feedback_store import FeedbackStore
from folio_resolve.annotate.models import FeedbackEntry


def _entry(**kw: object) -> FeedbackEntry:
    payload: dict[str, object] = {"job_id": "j1", "annotation_id": "a1", "rating": "up"}
    payload.update(kw)
    return FeedbackEntry(**payload)  # type: ignore[arg-type]


# -- construction --------------------------------------------------------


def test_the_base_directory_is_created_on_demand(tmp_path: Path) -> None:
    base = tmp_path / "nested" / "feedback"
    FeedbackStore(base)
    assert base.is_dir()


def test_an_existing_directory_is_reused(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="keep-me"))
    assert len(FeedbackStore(tmp_path).list_all()) == 1


def test_a_string_path_is_accepted(tmp_path: Path) -> None:
    store = FeedbackStore(str(tmp_path))
    store.save(_entry(id="e1"))
    assert store.load("e1") is not None


# -- save / load / delete ------------------------------------------------


def test_save_load_roundtrip_preserves_every_field(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    entry = _entry(
        id="e1",
        rating="down",
        stage="entity_ruler",
        comment="wrong sense",
        annotation_text="action",
        sentence_text="The action was dismissed.",
        folio_iri="R-auction",
        folio_label="Auction",
        lineage=[{"stage": "entity_ruler", "action": "created"}],
    )
    store.save(entry)
    assert store.load("e1") == entry


def test_the_entry_lands_as_readable_json_named_for_its_id(tmp_path: Path) -> None:
    FeedbackStore(tmp_path).save(_entry(id="e1", folio_iri="R1"))
    payload = json.loads((tmp_path / "e1.json").read_text(encoding="utf-8"))
    assert payload["folio_iri"] == "R1"


def test_saving_the_same_id_twice_overwrites_in_place(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1", comment="first"))
    store.save(_entry(id="e1", comment="second"))
    loaded = store.load("e1")
    assert loaded is not None and loaded.comment == "second"
    assert len(store.list_all()) == 1


def test_save_leaves_no_temp_files_behind(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["e1.json"]


def test_loading_an_unknown_id_is_none_not_an_error(tmp_path: Path) -> None:
    assert FeedbackStore(tmp_path).load("nope") is None


def test_delete_reports_whether_anything_was_removed(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1"))
    assert store.delete("e1") is True
    assert store.delete("e1") is False
    assert store.load("e1") is None


# -- id safety -----------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["../escaped", "..", "nested/child", "a/../../b", "", ".hidden", "-leading-dash", "a b"],
)
def test_an_unsafe_id_is_refused(tmp_path: Path, bad_id: str) -> None:
    """Regression: the id was interpolated straight into a filename.

    `FeedbackEntry.id` is settable and, in the FastAPI routes this store was lifted from,
    reachable from a request path — so `../../secrets` wrote and read outside the store.
    """
    store = FeedbackStore(tmp_path)
    with pytest.raises(ValueError, match="unsafe feedback id"):
        store.save(_entry(id=bad_id))
    with pytest.raises(ValueError, match="unsafe feedback id"):
        store.load(bad_id)
    with pytest.raises(ValueError, match="unsafe feedback id"):
        store.delete(bad_id)
    assert list(tmp_path.iterdir()) == []


def test_nothing_escapes_the_base_directory(tmp_path: Path) -> None:
    base = tmp_path / "store"
    store = FeedbackStore(base)
    with pytest.raises(ValueError):
        store.save(_entry(id="../escaped"))
    assert not (tmp_path / "escaped.json").exists()


@pytest.mark.parametrize("good_id", ["e1", "0", "a.b_c-d", "9f4c1e2a-0b3d-4f5e-8a7b-6c5d4e3f2a1b"])
def test_ordinary_ids_including_uuid4_are_accepted(tmp_path: Path, good_id: str) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id=good_id))
    assert store.load(good_id) is not None


def test_the_default_generated_id_is_always_safe(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    for _ in range(20):
        entry = _entry()
        store.save(entry)
        assert store.load(entry.id) is not None


# -- list_all ------------------------------------------------------------


def test_list_all_of_an_empty_store(tmp_path: Path) -> None:
    assert FeedbackStore(tmp_path).list_all() == []


def test_list_all_returns_every_entry_in_a_stable_order(tmp_path: Path) -> None:
    """Path.glob yields filesystem order, which made tie-breaks machine-dependent."""
    store = FeedbackStore(tmp_path)
    for name in ("c", "a", "b"):
        store.save(_entry(id=name))
    assert [e.id for e in store.list_all()] == ["a", "b", "c"]
    assert [e.id for e in store.list_all()] == ["a", "b", "c"]


def test_find_by_annotation_needs_both_keys_to_match(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1", job_id="j1", annotation_id="a1"))
    store.save(_entry(id="e2", job_id="j2", annotation_id="a1"))
    found = store.find_by_annotation("j2", "a1")
    assert found is not None and found.id == "e2"
    assert store.find_by_annotation("j1", "a2") is None
    assert store.find_by_annotation("j3", "a1") is None


# -- insights ------------------------------------------------------------


def test_insights_of_an_empty_store_are_all_zero(tmp_path: Path) -> None:
    summary = FeedbackStore(tmp_path).get_insights()
    assert summary.total_feedback == 0
    assert (summary.thumbs_up, summary.thumbs_down, summary.total_dismissed) == (0, 0, 0)
    assert summary.by_stage == {}
    assert summary.most_downvoted_concepts == []
    assert summary.recent_feedback == []


def test_insights_count_each_rating_kind(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    for i, rating in enumerate(["up", "up", "down", "dismissed"]):
        store.save(_entry(id=f"e{i}", rating=rating))
    summary = store.get_insights()
    assert (summary.total_feedback, summary.thumbs_up, summary.thumbs_down, summary.total_dismissed) == (
        4,
        2,
        1,
        1,
    )


def test_insights_are_scoped_to_a_job_when_asked(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1", job_id="j1", rating="up"))
    store.save(_entry(id="e2", job_id="j2", rating="down"))
    assert store.get_insights("j1").total_feedback == 1
    assert store.get_insights("j2").thumbs_down == 1
    assert store.get_insights().total_feedback == 2  # None == every job
    assert store.get_insights("j-none").total_feedback == 0


def test_stage_buckets_are_keyed_by_stage_with_overall_as_the_default(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1", stage="entity_ruler", rating="down"))
    store.save(_entry(id="e2", stage="entity_ruler", rating="up"))
    store.save(_entry(id="e3", stage=None, rating="dismissed"))
    by_stage = store.get_insights().by_stage
    assert by_stage["entity_ruler"] == {"up": 1, "down": 1, "dismissed": 0}
    assert by_stage["overall"] == {"up": 0, "down": 0, "dismissed": 1}


def test_an_unrecognized_rating_still_gets_a_stage_bucket(tmp_path: Path) -> None:
    # Consumers have added rating kinds; the bucket must appear rather than KeyError.
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1", stage="llm", rating="flagged"))
    summary = store.get_insights()
    assert summary.by_stage["llm"] == {"up": 0, "down": 0, "dismissed": 0}
    assert summary.total_feedback == 1
    assert (summary.thumbs_up, summary.thumbs_down, summary.total_dismissed) == (0, 0, 0)


def test_downvoted_and_dismissed_concepts_are_counted_separately(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1", rating="down", folio_iri="R-auction", folio_label="Auction"))
    store.save(_entry(id="e2", rating="down", folio_iri="R-auction", folio_label="Auction"))
    store.save(_entry(id="e3", rating="down", folio_iri="R-slovenia", folio_label="Slovenia"))
    store.save(_entry(id="e4", rating="dismissed", folio_iri="R-slovenia", folio_label="Slovenia"))
    summary = store.get_insights()
    assert summary.most_downvoted_concepts == [
        {"iri": "R-auction", "label": "Auction", "count": 2},
        {"iri": "R-slovenia", "label": "Slovenia", "count": 1},
    ]
    assert summary.most_dismissed_concepts == [
        {"iri": "R-slovenia", "label": "Slovenia", "count": 1}
    ]


def test_concepts_without_an_iri_are_not_counted(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1", rating="down", folio_iri=None))
    store.save(_entry(id="e2", rating="dismissed", folio_iri=""))
    summary = store.get_insights()
    assert summary.most_downvoted_concepts == []
    assert summary.most_dismissed_concepts == []
    assert summary.thumbs_down == 1  # still counted in the totals


def test_a_dismissed_only_concept_still_gets_its_label(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    store.save(_entry(id="e1", rating="dismissed", folio_iri="R1", folio_label="Slovenia"))
    assert store.get_insights().most_dismissed_concepts[0]["label"] == "Slovenia"


def test_the_top_concept_lists_are_capped_at_ten(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    for i in range(15):
        store.save(_entry(id=f"e{i}", rating="down", folio_iri=f"R{i}", folio_label=f"L{i}"))
    assert len(store.get_insights().most_downvoted_concepts) == 10


def test_recent_feedback_is_newest_first_and_capped_at_twenty(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path)
    for i in range(25):
        store.save(_entry(id=f"e{i:02d}", created_at=f"2026-08-05T00:00:{i:02d}+00:00"))
    recent = store.get_insights().recent_feedback
    assert len(recent) == 20
    assert [e.id for e in recent[:3]] == ["e24", "e23", "e22"]
