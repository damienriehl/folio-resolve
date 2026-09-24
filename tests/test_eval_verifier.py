"""Offline verifier contract tests; no ontology or model is loaded."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem
from folio_eval.verifier import (
    CandidateProbability,
    DecisionCollection,
    PassageDecision,
    Thresholds,
    calibration_metrics,
    compare_collections,
    emit,
    fold_for_item,
    load_collection,
    score_collection,
    select_thresholds,
)


def corpus() -> LoadedCorpus:
    manifest = CorpusManifest(
        version=1,
        content_sha256="a" * 64,
        nomatch_content_sha256="b" * 64,
        ontology_cache_sha256="c" * 64,
        answer_rule_config_sha256="d" * 64,
        item_counts={"scoreable": 10, "nomatch": 5},
        non_lexical_fraction=1.0,
        non_lexical_floor=0.3,
        scoreable=True,
        seed=7,
        created="test",
        manifest_path=Path("unused.json"),
    )
    rows = tuple(
        SyntheticItem(
            item_id=f"s{i}",
            doc_type="motion",
            jurisdiction="US",
            text="public text",
            gold_iris=frozenset({"gold"}),
            verification="deterministic",
        )
        for i in range(10)
    )
    controls = tuple(
        SyntheticItem(
            item_id=f"n{i}",
            doc_type="control",
            jurisdiction="US",
            text="control text",
            verification="deterministic",
            provenance={"no_match": True},
        )
        for i in range(5)
    )
    return LoadedCorpus(manifest, rows, controls)


def collection(c: LoadedCorpus) -> DecisionCollection:
    decisions = tuple(
        PassageDecision(
            item_id=item.item_id,
            kind=kind,
            shortlist=("wrong", "gold"),
            no_match_p=0.95 if kind == "nomatch" else 0.05,
            candidates=(CandidateProbability("wrong", 0.1), CandidateProbability("gold", 0.9)),
        )
        for kind, items in (("scoreable", c.scoreable_items), ("nomatch", c.nomatch_items))
        for item in items
    )
    return DecisionCollection(
        arm_name="test",
        model_id="fake",
        prompt_template_sha256="e" * 64,
        corpus_content_sha256=c.manifest.content_sha256,
        nomatch_content_sha256=c.manifest.nomatch_content_sha256,
        adapter_source="attempt-0004",
        adapter_sha256="f" * 64,
        shortlist_depth=24,
        decisions=decisions,
    )


def test_roundtrip_and_abstention(tmp_path: Path) -> None:
    c = corpus()
    original = collection(c)
    path = tmp_path / "collection.json"
    path.write_text(json.dumps(original.to_json()))
    loaded = load_collection(path, c)
    assert loaded == original
    result = score_collection(loaded, c, thresholds=Thresholds(0.5, 0.9))
    assert result.run.overall.f1 == 1.0
    assert result.nomatch_fp_rate == 0.0
    assert result.failed_count == 0
    assert emit(loaded.decisions[-1], Thresholds(0.5, 0.95)) == ()
    assert emit(loaded.decisions[-1], Thresholds(0.5, None)) == ("gold",)


def test_rank_24_and_no_top_k_cap() -> None:
    c = corpus()
    iris = (*(f"wrong-{i}" for i in range(23)), "gold")
    d = replace(
        collection(c).decisions[0],
        shortlist=iris,
        candidates=tuple(CandidateProbability(iri, 0.8) for iri in iris),
    )
    coll = replace(collection(c), decisions=(d, *collection(c).decisions[1:]))
    result = score_collection(coll, c, thresholds=Thresholds(0.8, None))
    first = result.run.item_scores[0]
    assert first.tp == 1 and first.fp == 23
    assert len(first.committed_iris) == 24


@pytest.mark.parametrize("value", [1.2, -0.1, float("nan"), float("inf"), True, "0.5"])
@pytest.mark.parametrize("field", ["candidate", "no_match_p"])
def test_bad_probabilities_name_item(value: object, field: str) -> None:
    c = corpus()
    payload = collection(c).to_json()
    row = payload["decisions"][0]
    if field == "candidate":
        row["candidates"][0]["p"] = value
    else:
        row[field] = value
    with pytest.raises(ValueError, match="s0"):
        DecisionCollection.from_json(payload, c)


@pytest.mark.parametrize(
    "change",
    [
        "outside",
        "duplicate",
        "missing_candidate",
        "kind",
        "missing_item",
        "duplicate_item",
        "extra_item",
        "depth",
    ],
)
def test_invalid_records(change: str) -> None:
    c = corpus()
    payload = collection(c).to_json()
    rows = payload["decisions"]
    row = rows[0]
    if change == "outside":
        row["candidates"][0]["iri"] = "outside"
    elif change == "duplicate":
        row["candidates"][1]["iri"] = "wrong"
    elif change == "missing_candidate":
        row["candidates"].pop()
    elif change == "kind":
        row["kind"] = "nomatch"
    elif change == "missing_item":
        rows.pop(0)
    elif change == "duplicate_item":
        rows.append(row)
    elif change == "extra_item":
        rows.append(dict(row, item_id="extra"))
    else:
        payload["shortlist_depth"] = 1
    with pytest.raises(ValueError):
        DecisionCollection.from_json(payload, c)


@pytest.mark.parametrize(
    "field",
    [
        "corpus_content_sha256",
        "nomatch_content_sha256",
        "prompt_template_sha256",
        "adapter_sha256",
        "model_id",
    ],
)
def test_provenance_validation(field: str) -> None:
    c = corpus()
    payload = collection(c).to_json()
    payload[field] = ""
    with pytest.raises(ValueError):
        DecisionCollection.from_json(payload, c)


def test_folds_reproducible_and_no_heldout_label_leakage() -> None:
    c = corpus()
    coll = collection(c)
    folds = [fold_for_item(f"item-{i}") for i in range(100)]
    assert folds == [fold_for_item(f"item-{i}") for i in range(100)]
    assert set(folds) == set(range(5))
    chosen = select_thresholds(coll, c)
    assert chosen == select_thresholds(replace(coll, arm_name="other"), c)
    for fold in range(5):
        altered = replace(
            c,
            corpus_items=tuple(
                replace(item, gold_iris=frozenset({"wrong"}))
                if fold_for_item(item.item_id) == fold
                else item
                for item in c.corpus_items
            ),
        )
        assert select_thresholds(coll, altered)[fold] == chosen[fold]
    result = score_collection(coll, c)
    assert result.thresholds_by_fold == chosen
    assert result.run.overall.f1 == 1.0


def test_thresholds_maximize_training_f1() -> None:
    c = corpus()
    coll = collection(c)
    # Distinguish the optimum from the conventional 0.5 default.
    coll = replace(
        coll,
        decisions=tuple(
            replace(
                d,
                candidates=(
                    CandidateProbability("wrong", 0.65),
                    CandidateProbability("gold", 0.7),
                ),
            )
            for d in coll.decisions
        ),
    )
    chosen = select_thresholds(coll, c)
    assert all(0.65 < t.admit <= 0.7 for t in chosen)
    assert score_collection(coll, c).run.overall.f1 == 1.0


def test_calibration_bins_and_empty() -> None:
    perfect = calibration_metrics(((0.0, False), (1.0, True)))
    assert perfect.brier == 0.0 and perfect.ece == 0.0 and perfect.count == 2
    soft = calibration_metrics(((0.5, False), (0.5, True)))
    assert soft.ece == 0.0 and soft.brier == 0.25
    bad = calibration_metrics(((0.9, False), (0.9, True)))
    assert bad.ece == pytest.approx(0.4)
    assert bad.brier == pytest.approx(0.41)
    empty = calibration_metrics(())
    assert empty.count == 0 and empty.brier is None and empty.ece is None


def test_calibration_uses_controls_and_all_candidates() -> None:
    c = corpus()
    result = score_collection(collection(c), c)
    assert result.candidate_calibration.count == 30
    assert result.nomatch_calibration.count == 15
    assert result.nomatch_calibration.brier == pytest.approx(0.0025)


def test_identical_baseline_delta_and_fixed_baseline_replay() -> None:
    c = corpus()
    coll = collection(c)
    fixed = Thresholds(0.5, None)
    compared = compare_collections(coll, coll, c, thresholds=fixed, baseline_thresholds=fixed)
    assert compared.delta.point == 0.0
    assert compared.delta.low <= 0 <= compared.delta.high
    assert compared.delta.n_resamples == 2000 and compared.delta.seed == 20260727
    assert compared.delta.n_units == 10
    assert compared.baseline.nomatch_fp_rate == 1.0


def test_failed_scoreable_and_control_load_score_and_pair() -> None:
    c = corpus()
    base = collection(c)
    decisions = tuple(
        replace(d, state="failed", no_match_p=None, candidates=())
        if d.item_id in {"s0", "n0"}
        else d
        for d in base.decisions
    )
    coll = DecisionCollection.from_json(replace(base, decisions=decisions).to_json(), c)
    compared = compare_collections(coll, base, c, thresholds=Thresholds(0.5, None))
    first = compared.candidate.run.item_scores[0]
    assert (first.tp, first.fp, first.fn) == (0, 0, 1)
    assert compared.candidate.failed_count == 2
    assert compared.candidate.nomatch_fp_rate == 0.8
    assert compared.delta.n_units == 10
    assert compared.candidate.candidate_calibration.count == 26
    assert compared.candidate.nomatch_calibration.count == 13


def test_pairing_mismatched_sets_rejected() -> None:
    c = corpus()
    coll = collection(c)
    short = replace(coll, decisions=coll.decisions[:-1])
    with pytest.raises(ValueError, match="item-id sets"):
        compare_collections(coll, short, c)


def test_failed_cannot_carry_fabricated_probabilities() -> None:
    c = corpus()
    coll = collection(c)
    bad = replace(coll, decisions=(replace(coll.decisions[0], state="failed"), *coll.decisions[1:]))
    with pytest.raises(ValueError, match="s0"):
        DecisionCollection.from_json(bad.to_json(), c)


def test_controls_alone_teach_heldout_abstention() -> None:
    c = corpus()
    coll = collection(c)
    result = score_collection(coll, c)
    assert result.nomatch_fp_rate < 1.0
    assert result.nomatch_fp_rate == 0.0
    assert result.run.overall.f1 == 1.0
    assert result.thresholds_by_fold == (Thresholds(0.9, 0.95),) * 5
    for d in coll.decisions:
        if d.kind == "nomatch":
            assert emit(d, result.thresholds_by_fold[fold_for_item(d.item_id)]) == ()
    baseline = compare_collections(coll, coll, c).baseline
    assert baseline.thresholds_by_fold == (Thresholds(0.5, None),) * 5
    assert baseline.nomatch_fp_rate == 1.0


def test_control_emissions_never_tune_own_fold() -> None:
    c = corpus()
    c = replace(c, nomatch_items=c.nomatch_items[:1])
    coll = collection(c)
    control = coll.decisions[-1]
    own_fold = fold_for_item(control.item_id)
    before = select_thresholds(coll, c)
    altered = replace(
        coll,
        decisions=(*coll.decisions[:-1], replace(control, shortlist=(), candidates=())),
    )
    after = select_thresholds(altered, c)
    assert before[own_fold] == after[own_fold] == Thresholds(0.9, None)
    assert {fold for fold in range(5) if before[fold] != after[fold]} == (
        set(range(5)) - {own_fold}
    )
    assert after == (Thresholds(0.9, None),) * 5


def test_no_controls_preserves_scoreable_only_report() -> None:
    c = replace(corpus(), nomatch_items=())
    coll = collection(c)
    # Both candidates tie: one TP and one FP per row, so the old optimum is 2/3.
    coll = replace(
        coll,
        decisions=tuple(
            replace(d, candidates=tuple(replace(p, p=0.9) for p in d.candidates))
            for d in coll.decisions
        ),
    )
    result = score_collection(coll, c)
    previous = score_collection(coll, c, thresholds=Thresholds(0.9, None))
    assert result.thresholds_by_fold == (Thresholds(0.9, None),) * 5
    assert result.run.overall == previous.run.overall
    assert result.run.overall.f1 == pytest.approx(2 / 3)
    assert result.nomatch_fp_rate == 0.0


def test_crossfit_learns_abstention_for_controls() -> None:
    c = corpus()
    coll = collection(c)
    # Each training split contains correct low-no-match and incorrect high-no-match rows.
    decisions = tuple(
        replace(
            d,
            no_match_p=0.95,
            shortlist=("wrong",),
            candidates=(CandidateProbability("wrong", 0.9),),
        )
        if d.item_id in {"s0", "s1", "s2", "s3", "s4"}
        else d
        for d in coll.decisions
    )
    coll = replace(coll, decisions=decisions)
    result = score_collection(coll, c)
    for d in coll.decisions:
        if d.kind == "nomatch":
            t = result.thresholds_by_fold[fold_for_item(d.item_id)]
            assert t.nomatch is not None and d.no_match_p >= t.nomatch
            assert emit(d, t) == ()
    assert result.nomatch_fp_rate == 0.0


def test_all_failed_retains_denominators_and_undefined_calibration() -> None:
    c = corpus()
    coll = collection(c)
    coll = replace(
        coll,
        decisions=tuple(
            replace(d, state="failed", no_match_p=None, candidates=()) for d in coll.decisions
        ),
    )
    result = score_collection(coll, c)
    assert result.failed_count == 15
    assert result.run.overall.fn == 10 and result.run.overall.f1 == 0
    assert result.nomatch_fp_rate == 0.0
    assert result.candidate_calibration.brier is None
    assert result.nomatch_calibration.ece is None


def test_empty_shortlists_and_control_slice() -> None:
    c = replace(corpus(), nomatch_items=())
    coll = collection(c)
    coll = replace(
        coll, decisions=tuple(replace(d, shortlist=(), candidates=()) for d in coll.decisions)
    )
    result = score_collection(coll, c)
    assert result.run.overall.fn == 10 and result.nomatch_fp_rate == 0.0
    assert result.candidate_calibration.count == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("shortlist_depth", True),
        ("shortlist_depth", 0),
        ("lever_scope", "shared"),
    ],
)
def test_schema_errors(field: str, value: object) -> None:
    c = corpus()
    payload = collection(c).to_json()
    payload[field] = value
    with pytest.raises(ValueError):
        DecisionCollection.from_json(payload, c)


def test_nomatch_one_can_be_disabled() -> None:
    d = replace(collection(corpus()).decisions[-1], no_match_p=1.0)
    assert emit(d, Thresholds(0.5, 1.0)) == ()
    assert emit(d, Thresholds(0.5, None)) == ("gold",)
