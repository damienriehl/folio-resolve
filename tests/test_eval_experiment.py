"""U6 iteration protocol runner: AE4 tripwire, window discipline, leak scan, append-only (synthetic).

No workbook, no FOLIO, no network. ``score_tune_firm2``/``after_scores`` are always synthetic
callables or literal ``SliceOutcome`` values built from :class:`ItemOutcome` tuples.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from folio_eval.experiment import (
    CHECK_IN_ATTEMPTS,
    BoundaryRecord,
    ExperimentRecord,
    ItemOutcome,
    PendingAttemptError,
    SliceOutcome,
    StopRuleError,
    WindowRefusalError,
    append_record,
    build_scores_json,
    compute_status,
    current_window_baseline,
    evaluate_ae4_tripwire,
    finish_attempt,
    load_raw_records,
    pair_outcomes,
    start_attempt,
    status,
    stop_status,
)
from folio_eval.grade import DISAGREEMENT_CLASSES
from folio_eval.leakcheck import ScryptParams, build_manifest

GOLD_SURFACES = ("Fund Formation", "Escrow Services", "Carry Waterfall")


def outcome(
    item_id: str, *, tp: int = 1, fp: int = 0, fn: int = 0, exact: bool = True
) -> ItemOutcome:
    return ItemOutcome(item_id=item_id, tp=tp, fp=fp, fn=fn, exact=exact)


def slice_of(name: str, items: list[ItemOutcome]) -> SliceOutcome:
    return SliceOutcome(slice_name=name, items=tuple(items))


def synthetic_iteration(
    iteration: int,
    *,
    delta: float = 0.002,
    low: float = -0.001,
    high: float = 0.004,
    lever_scope: str = "shared",
    corpus_version: str = "synthetic-v1",
    classes: tuple[str, ...] = (),
    rebaseline: bool = False,
) -> dict[str, object]:
    return {
        "record_type": "attempt",
        "iteration": iteration,
        "lever_scope": lever_scope,
        "corpus_version": corpus_version,
        "item_count": 20,
        "bootstrap_ci": {
            "point": delta,
            "low": low,
            "high": high,
            "width": high - low,
        },
        "disagreement_classes_seen": list(classes),
        "rebaseline": rebaseline,
    }


# --------------------------------------------------------------------------------------
# AE4 tripwire -- both arms, and the clean case
# --------------------------------------------------------------------------------------


def test_ae4_flags_on_a_negative_changed_item_ci() -> None:
    """Enough regressions that essentially every bootstrap resample reads net-negative."""
    before = [outcome(f"item-{i}", exact=True) for i in range(10)]
    after = [
        outcome(f"item-{i}", exact=(i >= 8))  # 8 of 10 flip correct -> incorrect
        for i in range(10)
    ]
    result = evaluate_ae4_tripwire(before, after, n_resamples=300, seed=1)
    assert result.ci_negative is True
    assert result.any_regression is True
    assert result.flagged is True
    assert result.breakdown["regressed"] == 8


def test_ae4_flags_on_a_single_regression_even_without_a_negative_ci() -> None:
    """One item flips correct -> incorrect, outweighed by improvements: CI stays non-negative,
    but AE4 must still flag on the regression alone (the two arms are independent triggers)."""
    before = [outcome(f"item-{i}", exact=False) for i in range(9)] + [outcome("item-9", exact=True)]
    after = [outcome(f"item-{i}", exact=True) for i in range(9)] + [outcome("item-9", exact=False)]
    result = evaluate_ae4_tripwire(before, after, n_resamples=300, seed=1)
    assert result.any_regression is True
    assert result.breakdown["regressed"] == 1
    assert result.breakdown["improved"] == 9
    assert result.ci_negative is False
    assert result.flagged is True


def test_ae4_clean_when_neither_arm_fires() -> None:
    before = [outcome(f"item-{i}", exact=False) for i in range(5)]
    after = [outcome(f"item-{i}", exact=False) for i in range(5)]  # nothing changed
    result = evaluate_ae4_tripwire(before, after, n_resamples=300, seed=1)
    assert result.any_regression is False
    assert result.ci_negative is False
    assert result.flagged is False
    assert result.breakdown == {"items": 5, "improved": 0, "regressed": 0, "unchanged": 5, "net": 0}


def test_pair_outcomes_drops_items_missing_from_either_side() -> None:
    before = [outcome("a"), outcome("b")]
    after = [outcome("b"), outcome("c")]
    paired = pair_outcomes(before, after)
    assert [p.item_id for p in paired] == ["b"]


# --------------------------------------------------------------------------------------
# Leak scan -- refusal on a poisoned hypothesis
# --------------------------------------------------------------------------------------


def test_start_attempt_refuses_a_hypothesis_that_leaks_a_gold_surface_string(
    tmp_path: Path,
) -> None:
    from folio_eval.clusters import SurfaceLeakError

    pending_path = tmp_path / "pending.json"
    log_path = tmp_path / "experiments.jsonl"
    with pytest.raises(SurfaceLeakError):
        start_attempt(
            hypothesis="Widen the recall channel for Fund Formation-style leaves",
            cluster_targeted="candidate_gap_truncated",
            cluster_size=42,
            gold_version=1,
            ontology_hash="a" * 64,
            config_hash="b" * 64,
            surfaces=GOLD_SURFACES,
            prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
            run_selftest=False,
            experiments_log=log_path,
            pending_path=pending_path,
        )
    assert not pending_path.exists()
    assert not log_path.exists()


def test_clean_hypothesis_does_not_refuse(tmp_path: Path) -> None:
    pending_path = tmp_path / "pending.json"
    log_path = tmp_path / "experiments.jsonl"
    pending = start_attempt(
        hypothesis="Widen the recall channel for truncated candidate lists",
        cluster_targeted="candidate_gap_truncated",
        cluster_size=42,
        gold_version=1,
        ontology_hash="a" * 64,
        config_hash="b" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
        run_selftest=False,
        experiments_log=log_path,
        pending_path=pending_path,
    )
    assert pending_path.exists()
    assert pending.iteration == 1


# --------------------------------------------------------------------------------------
# Window discipline -- refusal on gold/ontology drift, rebaseline escape hatch
# --------------------------------------------------------------------------------------


def _seed_one_attempt(
    log_path: Path, *, gold_version: int = 1, ontology_hash: str = "a" * 64
) -> None:
    """Write one attempt record directly, establishing the implicit window baseline."""
    record = ExperimentRecord(
        attempt_id="attempt-0001",
        iteration=1,
        gold_version=gold_version,
        ontology_hash=ontology_hash,
        config_hash="c" * 64,
        commit_sha="deadbeef",
        hypothesis="widen the recall channel",
        cluster_targeted="candidate_gap_truncated",
        cluster_size=10,
        scores_before=build_scores_json(
            {"tune": slice_of("tune", []), "firm2": slice_of("firm2", [])}
        ),
        scores_after=build_scores_json(
            {"tune": slice_of("tune", []), "firm2": slice_of("firm2", [])}
        ),
        tripwire={"flagged": False},
        triage={"new_suspects": 0},
        decision="keep",
        reason="tune F1 improved, no firm2 regression",
        recorded_at="2026-07-27T00:00:00Z",
    )
    append_record(log_path, record.to_json(), surfaces=GOLD_SURFACES)


def test_start_attempt_refuses_when_gold_version_changed_mid_window(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    _seed_one_attempt(log_path, gold_version=1)
    with pytest.raises(WindowRefusalError, match="gold/ontology moved mid-window"):
        start_attempt(
            hypothesis="a second attempt",
            cluster_targeted="normalization",
            cluster_size=5,
            gold_version=2,  # moved
            ontology_hash="a" * 64,
            config_hash="c" * 64,
            surfaces=GOLD_SURFACES,
            prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
            run_selftest=False,
            experiments_log=log_path,
            pending_path=tmp_path / "pending.json",
        )


def test_start_attempt_refuses_when_ontology_hash_changed_mid_window(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    _seed_one_attempt(log_path, ontology_hash="a" * 64)
    with pytest.raises(WindowRefusalError, match="gold/ontology moved mid-window"):
        start_attempt(
            hypothesis="a second attempt",
            cluster_targeted="normalization",
            cluster_size=5,
            gold_version=1,
            ontology_hash="f" * 64,  # moved
            config_hash="c" * 64,
            surfaces=GOLD_SURFACES,
            prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
            run_selftest=False,
            experiments_log=log_path,
            pending_path=tmp_path / "pending.json",
        )


def test_rebaseline_writes_a_boundary_record_and_starts_a_fresh_window(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    _seed_one_attempt(log_path, gold_version=1, ontology_hash="a" * 64)
    pending = start_attempt(
        hypothesis="post gold-bump attempt",
        cluster_targeted="normalization",
        cluster_size=5,
        gold_version=2,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
        rebaseline=True,
        rebaseline_reason="audit gate folded 3 accepted corrections",
        run_selftest=False,
        experiments_log=log_path,
        pending_path=tmp_path / "pending.json",
    )
    records = load_raw_records(log_path)
    assert [r["record_type"] for r in records] == ["attempt", "boundary"]
    boundary = records[1]
    assert boundary["gold_version"] == 2
    assert boundary["after_attempt_count"] == 1
    # a fresh window: baseline now reads the boundary, and the next attempt is iteration 2.
    baseline = current_window_baseline(records)
    assert baseline is not None
    assert (baseline.gold_version, baseline.ontology_hash) == (2, "a" * 64)
    assert pending.iteration == 2


def test_rebaseline_is_a_no_op_when_nothing_actually_changed(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    _seed_one_attempt(log_path, gold_version=1, ontology_hash="a" * 64)
    start_attempt(
        hypothesis="same window, rebaseline flag set but nothing moved",
        cluster_targeted="normalization",
        cluster_size=5,
        gold_version=1,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
        rebaseline=True,
        run_selftest=False,
        experiments_log=log_path,
        pending_path=tmp_path / "pending.json",
    )
    records = load_raw_records(log_path)
    assert [r["record_type"] for r in records] == ["attempt"]  # no boundary written


def test_first_ever_attempt_needs_no_baseline(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"  # empty log
    pending = start_attempt(
        hypothesis="the very first attempt of the loop",
        cluster_targeted="candidate_gap_truncated",
        cluster_size=100,
        gold_version=2,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
        run_selftest=False,
        experiments_log=log_path,
        pending_path=tmp_path / "pending.json",
    )
    assert pending.iteration == 1


# --------------------------------------------------------------------------------------
# start/finish cycle, decisions, and the check-in tally
# --------------------------------------------------------------------------------------


def test_dry_run_attempt_produces_a_complete_log_record(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    pending_path = tmp_path / "pending.json"
    start_attempt(
        hypothesis="raise the label-search limit to reclaim truncated candidates",
        cluster_targeted="candidate_gap_truncated",
        cluster_size=250,
        gold_version=1,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", []), slice_of("firm2", [outcome("f-1")])),
        run_selftest=False,
        experiments_log=log_path,
        pending_path=pending_path,
    )
    record = finish_attempt(
        decision="keep",
        reason="tune F1 rose, no firm2 regression",
        surfaces=GOLD_SURFACES,
        after_scores=(slice_of("tune", []), slice_of("firm2", [outcome("f-1")])),
        commit_sha="cafebabe",
        experiments_log=log_path,
        pending_path=pending_path,
    )
    assert not pending_path.exists()  # cleared
    assert record.decision == "keep"
    assert record.commit_sha == "cafebabe"
    assert record.iteration == 1
    assert record.gold_version == 1 and record.ontology_hash == "a" * 64
    assert record.tripwire["flagged"] is False
    assert set(record.to_json()) == {
        "record_type",
        "attempt_id",
        "iteration",
        "gold_version",
        "ontology_hash",
        "config_hash",
        "commit_sha",
        "hypothesis",
        "cluster_targeted",
        "cluster_size",
        "scores_before",
        "scores_after",
        "tripwire",
        "triage",
        "decision",
        "reason",
        "recorded_at",
    }


@pytest.mark.parametrize(
    ("tune_before", "tune_after", "firm2_before", "firm2_after", "expected"),
    [
        (
            [outcome("t", tp=0, fp=1, fn=1, exact=False)],
            [outcome("t")],
            [outcome("f")],
            [outcome("f")],
            "keep",
        ),
        ([outcome("t")], [outcome("t")], [outcome("f")], [outcome("f")], "park"),
        (
            [outcome("t")],
            [outcome("t", tp=0, fp=1, fn=1, exact=False)],
            [outcome("f")],
            [outcome("f")],
            "revert",
        ),
        (
            [outcome("t", exact=False)],
            [outcome("t")],
            [outcome("f")],
            [outcome("f", exact=False)],
            "park",
        ),
    ],
)
def test_finish_auto_decision_follows_predeclared_gates(
    tmp_path: Path,
    tune_before: list[ItemOutcome],
    tune_after: list[ItemOutcome],
    firm2_before: list[ItemOutcome],
    firm2_after: list[ItemOutcome],
    expected: str,
) -> None:
    log_path = tmp_path / "experiments.jsonl"
    pending_path = tmp_path / "pending.json"
    start_attempt(
        hypothesis="measure deterministic recall",
        cluster_targeted="candidate_gap_unreachable",
        cluster_size=337,
        gold_version=3,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", tune_before), slice_of("firm2", firm2_before)),
        run_selftest=False,
        experiments_log=log_path,
        pending_path=pending_path,
    )

    record = finish_attempt(
        decision="auto",
        reason="",
        surfaces=GOLD_SURFACES,
        after_scores=(slice_of("tune", tune_after), slice_of("firm2", firm2_after)),
        commit_sha="cafebabe",
        experiments_log=log_path,
        pending_path=pending_path,
    )

    assert record.decision == expected
    assert record.reason.startswith("automatic decision:")


def test_finish_refuses_an_unknown_decision(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    pending_path = tmp_path / "pending.json"
    start_attempt(
        hypothesis="an attempt",
        cluster_targeted="normalization",
        cluster_size=1,
        gold_version=1,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
        run_selftest=False,
        experiments_log=log_path,
        pending_path=pending_path,
    )
    with pytest.raises(ValueError, match="decision must be one of"):
        finish_attempt(
            decision="maybe",
            reason="unsure",
            surfaces=GOLD_SURFACES,
            after_scores=(slice_of("tune", []), slice_of("firm2", [])),
            commit_sha="cafebabe",
            experiments_log=log_path,
            pending_path=pending_path,
        )
    assert pending_path.exists()  # untouched by the refused finish


def test_finish_refuses_without_a_pending_attempt(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    with pytest.raises(PendingAttemptError):
        finish_attempt(
            decision="keep",
            reason="nothing pending",
            surfaces=GOLD_SURFACES,
            after_scores=(slice_of("tune", []), slice_of("firm2", [])),
            commit_sha="cafebabe",
            experiments_log=log_path,
            pending_path=tmp_path / "no-such-pending.json",
        )


def test_start_refuses_a_second_attempt_while_one_is_pending(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    pending_path = tmp_path / "pending.json"
    start_attempt(
        hypothesis="first attempt",
        cluster_targeted="normalization",
        cluster_size=1,
        gold_version=1,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
        run_selftest=False,
        experiments_log=log_path,
        pending_path=pending_path,
    )
    with pytest.raises(PendingAttemptError):
        start_attempt(
            hypothesis="second attempt while the first is still open",
            cluster_targeted="normalization",
            cluster_size=1,
            gold_version=1,
            ontology_hash="a" * 64,
            config_hash="c" * 64,
            surfaces=GOLD_SURFACES,
            prior_scores=(slice_of("tune", []), slice_of("firm2", [])),
            run_selftest=False,
            experiments_log=log_path,
            pending_path=pending_path,
        )


def test_reverted_attempt_appends_a_revert_record_and_counts_toward_the_tally(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "experiments.jsonl"
    pending_path = tmp_path / "pending.json"
    start_attempt(
        hypothesis="a change that turns out to overfit",
        cluster_targeted="synonymy",
        cluster_size=30,
        gold_version=1,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        surfaces=GOLD_SURFACES,
        prior_scores=(slice_of("tune", []), slice_of("firm2", [outcome("f-1", exact=True)])),
        run_selftest=False,
        experiments_log=log_path,
        pending_path=pending_path,
    )
    record = finish_attempt(
        decision="revert",
        reason="firm2 regressed on item f-1",
        surfaces=GOLD_SURFACES,
        after_scores=(slice_of("tune", []), slice_of("firm2", [outcome("f-1", exact=False)])),
        commit_sha="cafebabe",
        experiments_log=log_path,
        pending_path=pending_path,
    )
    assert record.decision == "revert"
    assert record.tripwire["flagged"] is True  # the item that regressed backs up the revert

    result = status(log_path)
    assert result.window_attempts == 1
    assert result.revert_count == 1
    assert result.keep_count == 0


# --------------------------------------------------------------------------------------
# Append-only
# --------------------------------------------------------------------------------------


def test_append_only_existing_lines_are_never_rewritten(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"
    _seed_one_attempt(log_path)
    first_bytes = log_path.read_bytes()

    second = ExperimentRecord(
        attempt_id="attempt-0002",
        iteration=2,
        gold_version=1,
        ontology_hash="a" * 64,
        config_hash="c" * 64,
        commit_sha="feedface",
        hypothesis="a second, unrelated attempt",
        cluster_targeted="hierarchy_near_miss",
        cluster_size=8,
        scores_before=build_scores_json(
            {"tune": slice_of("tune", []), "firm2": slice_of("firm2", [])}
        ),
        scores_after=build_scores_json(
            {"tune": slice_of("tune", []), "firm2": slice_of("firm2", [])}
        ),
        tripwire={"flagged": False},
        triage={"new_suspects": 0},
        decision="park",
        reason="inconclusive, revisit after more gold",
        recorded_at="2026-07-27T01:00:00Z",
    )
    append_record(log_path, second.to_json(), surfaces=GOLD_SURFACES)

    all_bytes = log_path.read_bytes()
    assert all_bytes.startswith(first_bytes)  # the first record's bytes are untouched
    lines = [json.loads(line) for line in all_bytes.decode("utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["attempt_id"] == "attempt-0001"
    assert lines[1]["attempt_id"] == "attempt-0002"

    # a leaking third record refuses to append and the file is unchanged again
    from folio_eval.clusters import SurfaceLeakError

    poisoned = {"record_type": "attempt", "hypothesis": "Fund Formation leaked here"}
    with pytest.raises(SurfaceLeakError):
        append_record(log_path, poisoned, surfaces=GOLD_SURFACES)
    assert log_path.read_bytes() == all_bytes


# --------------------------------------------------------------------------------------
# Window status math
# --------------------------------------------------------------------------------------


def test_window_status_math_across_a_boundary(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments.jsonl"

    def seed(attempt_id: str, iteration: int, decision: str, gold_version: int = 1) -> None:
        record = ExperimentRecord(
            attempt_id=attempt_id,
            iteration=iteration,
            gold_version=gold_version,
            ontology_hash="a" * 64,
            config_hash="c" * 64,
            commit_sha="sha",
            hypothesis="hypothesis text",
            cluster_targeted="normalization",
            cluster_size=1,
            scores_before={},
            scores_after={},
            tripwire={"flagged": False},
            triage={"new_suspects": 0},
            decision=decision,
            reason="reason text",
            recorded_at="2026-07-27T00:00:00Z",
        )
        append_record(log_path, record.to_json(), surfaces=GOLD_SURFACES)

    seed("attempt-0001", 1, "keep")
    seed("attempt-0002", 2, "revert")
    result = compute_status(load_raw_records(log_path))
    assert (result.total_attempts, result.window_attempts) == (2, 2)
    assert result.check_in_due is False  # 2 < CHECK_IN_ATTEMPTS
    assert (result.keep_count, result.revert_count, result.park_count) == (1, 1, 0)

    seed("attempt-0003", 3, "park")
    result = compute_status(load_raw_records(log_path))
    assert result.window_attempts == CHECK_IN_ATTEMPTS
    assert result.check_in_due is True

    # a boundary resets the window's attempt count without touching total_attempts
    boundary = BoundaryRecord(
        gold_version=2,
        ontology_hash="a" * 64,
        after_attempt_count=3,
        reason="check-in accepted 2 corrections",
        recorded_at="2026-07-27T02:00:00Z",
    )
    append_record(log_path, boundary.to_json(), surfaces=GOLD_SURFACES)
    result = compute_status(load_raw_records(log_path))
    assert result.window_attempts == 0
    assert result.total_attempts == 3
    assert result.check_in_due is False
    assert result.baseline_gold_version == 2

    seed("attempt-0004", 4, "keep", gold_version=2)
    result = compute_status(load_raw_records(log_path))
    assert result.window_attempts == 1
    assert result.total_attempts == 4


def test_status_on_an_empty_log(tmp_path: Path) -> None:
    result = status(tmp_path / "no-such-file.jsonl")
    assert result.total_attempts == 0
    assert result.window_attempts == 0
    assert result.baseline_gold_version is None
    assert result.check_in_due is False
    assert result.last_decision is None


# --------------------------------------------------------------------------------------
# U9 synthetic iteration records and guarded diminishing-return stop rule
# --------------------------------------------------------------------------------------


def test_build_scores_json_accepts_named_slice_map_and_legacy_record_still_parses() -> None:
    scores = build_scores_json({"tune": slice_of("tune", []), "firm2": slice_of("firm2", [])})
    legacy = ExperimentRecord.from_json(
        {
            "record_type": "attempt",
            "attempt_id": "attempt-legacy",
            "scores_before": {"tune": {"f1": 0.5}, "firm2": {"aggregate": {}, "items": []}},
            "scores_after": scores,
        }
    )
    assert set(scores) == {"tune", "firm2"}
    assert legacy.scores_before["tune"] == {"f1": 0.5}
    assert legacy.lever_scope is None


def test_append_record_refuses_empty_surfaces_without_manifest_checker(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty surfaces"):
        append_record(tmp_path / "log.jsonl", {"record_type": "attempt"}, surfaces=())


def test_synthetic_record_via_manifest_checker_round_trips(tmp_path: Path) -> None:
    salt = b"u9-test-salt"
    manifest = build_manifest(
        ["private firm phrase"],
        salt,
        gold_version="gold_v1",
        gold_content_sha256="a" * 64,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8),
    )
    log_path = tmp_path / "synthetic.jsonl"
    pending_path = tmp_path / "pending.json"
    before = SliceOutcome.from_synthetic_report(
        {"overall": {"items": 2, "tp": 8, "fp": 2, "fn": 2, "exact_items": 1}}
    )
    after = slice_of(
        "synthetic",
        [outcome("s-1"), outcome("s-2", tp=7, fp=2, fn=2, exact=False)],
    )
    start_attempt(
        hypothesis="shared normalization adjustment",
        cluster_targeted="synthetic disagreement",
        cluster_size=2,
        gold_version=0,
        ontology_hash="a" * 64,
        config_hash="b" * 64,
        surfaces=(),
        manifest_checker=(manifest, salt),
        prior_scores={"synthetic": before},
        lever_scope="shared",
        corpus_version="synthetic-v1",
        disagreement_classes_seen=("set_mismatch",),
        run_selftest=False,
        experiments_log=log_path,
        pending_path=pending_path,
    )
    record = finish_attempt(
        decision="keep",
        reason="small synthetic improvement",
        surfaces=(),
        manifest_checker=(manifest, salt),
        after_scores={"synthetic": after},
        commit_sha="cafebabe",
        experiments_log=log_path,
        pending_path=pending_path,
        ci_resamples=50,
        ci_seed=1,
    )
    loaded = ExperimentRecord.from_json(load_raw_records(log_path)[0])
    assert loaded == record
    assert loaded.lever_scope == "shared"
    assert loaded.corpus_version == "synthetic-v1"
    assert loaded.item_count == 2
    assert set(loaded.bootstrap_ci) >= {"low", "high", "width"}
    assert loaded.disagreement_classes_seen == ("set_mismatch",)


def test_stop_status_stops_only_with_corroborating_checkpoint() -> None:
    records = [synthetic_iteration(1), synthetic_iteration(2)]
    checkpoint = {"tune": {"f1": 0.7}, "firm2": {"changed_items": 0}, "corroborates": True}
    assert stop_status(records, interim_checkpoint=checkpoint).status == "stopped"
    assert stop_status(records, interim_checkpoint=None).status == "escalate"


def test_adapter_only_iteration_does_not_advance_stop_counter() -> None:
    records = [
        synthetic_iteration(1),
        synthetic_iteration(2, lever_scope="adapter_only"),
    ]
    assert stop_status(records, interim_checkpoint={"corroborates": True}).status == "continue"


def test_stop_status_refuses_cross_version_delta_without_rebaseline() -> None:
    records = [
        synthetic_iteration(1, corpus_version="synthetic-v1"),
        synthetic_iteration(2, corpus_version="synthetic-v2"),
    ]
    with pytest.raises(StopRuleError, match="rebaseline"):
        stop_status(records, interim_checkpoint={"corroborates": True})


def test_novel_disagreement_class_resets_stop_counter() -> None:
    novel = next(iter(DISAGREEMENT_CLASSES))
    records = [
        synthetic_iteration(1),
        synthetic_iteration(2, classes=(novel,)),
        synthetic_iteration(3),
    ]
    result = stop_status(records, interim_checkpoint={"corroborates": True})
    assert result.status == "continue"
    assert result.consecutive_sub_epsilon == 1


# --------------------------------------------------------------------------------------
# Incremental triage hook (reuses audit._score_driven_suspects)
# --------------------------------------------------------------------------------------


def _gold_row(item_id: str, gold_iri: str) -> object:
    from folio_eval.audit import gold_row_from_json

    return gold_row_from_json(
        {
            "item_id": item_id,
            "firm": "firm1",
            "stratum": "Corporate",
            "stratum_id": "sid-Corporate",
            "ancestor_path": ["Corporate", "Funds"],
            "leaf": "leaf",
            "input_text": "leaf",
            "gold_iris": [gold_iri],
            "values": [
                {
                    "raw": "leaf",
                    "iri": gold_iri,
                    "origin": "own",
                    "column": "SALI 1",
                    "branch": "exact_preferred",
                    "parse_branch": "plain",
                }
            ],
            "flags": [],
            "rules": [],
            "blank": False,
            "notes": None,
            "provenance": "curator_workbook",
            "gold_version": 1,
        }
    )


def test_incremental_triage_hook_reuses_score_driven_classification_and_dedupes(
    tmp_path: Path,
) -> None:
    from folio_eval.experiment import incremental_triage

    stream_path = tmp_path / "live_suspects.jsonl"
    gold_rows = [_gold_row("item-1", "R-gold")]
    cluster_rows = [
        {
            "item_id": "item-1",
            "kind": "fn",
            "cluster": "synonymy",
            "gold_iri": "R-gold",
            "gold_labels": ["leaf"],
            "signals": {"max_token_jaccard": 0.0},
            "slice": "tune",
        }
    ]
    first = incremental_triage(cluster_rows, gold_rows, stream_path=stream_path)
    assert [entry["item_id"] for entry in first] == ["item-1"]
    assert stream_path.exists()

    # calling again with the same disagreement yields nothing new (dedupe by item_id)
    second = incremental_triage(cluster_rows, gold_rows, stream_path=stream_path)
    assert second == []
    assert len(load_raw_records(stream_path)) == 1
