"""Generated evidence only: strict cohorts, paired micro-F1, and flag-off parity."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from folio_eval import adoption_scorecard as scorecard

FIXTURE = Path(__file__).resolve().parents[1] / "eval/fixtures/adoption-scorecard-v1.json"


def fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_bytes())


def test_bound_fixture_and_missing_arm(tmp_path: Path) -> None:
    raw = FIXTURE.read_bytes()
    value = scorecard.load_bundle(FIXTURE, hashlib.sha256(raw).hexdigest())
    assert len(value.arms) == 4
    data = fixture()
    data["arms"].pop()
    data["arms"][0]["baseline"] = None
    data["arms"][0]["parity"] = None
    assert scorecard.validate_bundle(data).arms[0].baseline is None
    with pytest.raises(scorecard.EvidenceError, match=r"^invalid_evidence$"):
        scorecard.load_bundle(FIXTURE, "0" * 64)


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "missing",
        "extra",
        "wrong_gold",
        "bool_count",
        "negative_count",
        "fraction_count",
        "impossible",
        "prediction_count",
        "nonfinite",
        "unknown_field",
        "unknown_arm",
        "duplicate_arm",
        "binding",
        "lever",
        "same_sources",
        "r14_reference",
        "parity_candidate",
        "parity_missing",
        "parity_extra",
        "parity_duplicate",
        "enabled_flag",
        "projection",
        "control_overlap",
        "control_pair",
        "population_count",
        "zero_scoreable",
        "verifier_config",
        "recall_sources",
        "recall_gold",
        "depth_bool",
    ],
)
def test_malformed_evidence_rejected(case: str) -> None:
    data = fixture()
    arm = data["arms"][0]
    rows = arm["candidate"]["scoreable"]
    if case == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif case == "missing":
        rows.pop()
    elif case == "extra":
        rows.append(dict(rows[0], item_key="extra"))
    elif case == "wrong_gold":
        rows[0]["gold_count"] += 1
        rows[0]["fn"] += 1
    elif case in ("bool_count", "negative_count", "fraction_count", "nonfinite"):
        rows[0]["tp"] = {
            "bool_count": True,
            "negative_count": -1,
            "fraction_count": 1.5,
            "nonfinite": float("nan"),
        }[case]
    elif case == "impossible":
        rows[0]["fn"] += 1
    elif case == "prediction_count":
        rows[0]["predicted_count"] += 1
    elif case == "unknown_field":
        arm["private_text"] = "DO-NOT-PRINT-CANARY"
    elif case == "unknown_arm":
        arm["consumer"] = "unknown"
    elif case == "duplicate_arm":
        data["arms"].append(deepcopy(arm))
    elif case == "binding":
        arm["binding"]["corpus_sha256"] = "0" * 64
    elif case == "lever":
        arm["lever_sha256"] = "0" * 64
    elif case == "same_sources":
        arm["candidate"]["source_sha256"] = arm["baseline"]["source_sha256"]
    elif case == "r14_reference":
        arm["baseline"]["r14_reference_sha256"] = "not-a-digest"
    elif case == "parity_candidate":
        arm["parity"]["candidate_source_sha256"] = "0" * 64
    elif case == "parity_missing":
        arm["parity"]["candidate"].pop()
    elif case == "parity_extra":
        arm["parity"]["candidate"].append(dict(arm["parity"]["candidate"][0], item_key="extra"))
    elif case == "parity_duplicate":
        arm["parity"]["candidate"][1] = deepcopy(arm["parity"]["candidate"][0])
    elif case == "enabled_flag":
        arm["parity"]["flag_enabled"] = True
    elif case == "projection":
        arm["parity"]["projection_version"] = "counts-only"
    elif case == "control_overlap":
        data["cohort"]["no_match"][0] = data["cohort"]["scoreable"][0]["item_key"]
    elif case == "control_pair":
        arm["candidate"]["no_match"].pop()
    elif case == "population_count":
        data["cohort"]["scoreable_count"] += 1
    elif case == "zero_scoreable":
        data["cohort"]["scoreable"] = []
        data["cohort"]["scoreable_count"] = 0
    elif case == "verifier_config":
        data["verifier"]["identity"]["configuration_sha256"] = "0" * 64
    elif case == "recall_sources":
        data["recall"]["candidate_source_sha256"] = "0" * 64
    elif case == "recall_gold":
        data["recall"]["gold_count"] += 1
    elif case == "depth_bool":
        data["recall"]["candidate_depth"] = True
    with pytest.raises(scorecard.EvidenceError, match=r"^invalid_evidence$"):
        scorecard.validate_bundle(data)


@pytest.mark.parametrize("raw", [b'{"x": 1, "x": 2}', b'{"x": NaN}', b'{"x": Infinity}', b"[]"])
def test_invalid_json_is_safe(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    with pytest.raises(scorecard.EvidenceError, match=r"^invalid_evidence$"):
        scorecard.load_bundle(path, hashlib.sha256(raw).hexdigest())


def test_projection_preserves_complete_results_and_array_order() -> None:
    result = {
        "results": [{"concept": "generated-a", "score": 0.5, "span": [1, 2]}],
        "abstained": False,
    }
    digest = scorecard.semantic_digest(result)
    assert scorecard.semantic_digest(dict(reversed(list(result.items())))) == digest
    for field, value in [("score", 0.6), ("span", [2, 3]), ("concept", "generated-b")]:
        changed = deepcopy(result)
        changed["results"][0][field] = value
        assert scorecard.semantic_digest(changed) != digest
    assert scorecard.semantic_digest([1, 2]) != scorecard.semantic_digest([2, 1])


def assess(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = json.dumps(fixture() if data is None else data, sort_keys=True).encode()
    return scorecard.evaluate(
        scorecard.validate_bundle(json.loads(raw)), hashlib.sha256(raw).hexdigest()
    )


def test_independent_arm_results_and_unchanged_adoption_authority() -> None:
    report = assess()
    cards = report["scorecards"]
    assert len(cards) == 4
    assert [card["status"] for card in cards] == ["pass", "fail", "pass", "pass"]
    assert cards[0]["baseline_metrics"]["f1"] == pytest.approx(2 / 3)
    assert cards[0]["candidate_metrics"]["f1"] == 1
    assert cards[0]["consumer_f1_interval"]["low"] == pytest.approx(1 / 3)
    assert cards[1]["consumer_f1_interval"]["low"] == 0
    assert all(card["adoption_authorized"] is False for card in cards)
    assert report["adoption_authorized"] is False
    assert report["evidence_kind"] == "generated_fixture"
    data = fixture()
    data["evidence_kind"] = "recorded_input"
    assert assess(data)["adoption_authorized"] is False


def test_missing_evidence_does_not_erase_available_metrics_or_other_arms() -> None:
    data = fixture()
    data["arms"][1]["baseline"] = None
    data["arms"][1]["parity"] = None
    data["arms"].pop()
    report = assess(data)
    assert report["scorecards"][0]["status"] == "pass"
    missing = report["scorecards"][1]
    assert missing["status"] == "missing"
    assert missing["complete"] is False
    assert missing["baseline_metrics"] is None
    assert missing["candidate_metrics"]["f1"] == pytest.approx(2 / 3)
    assert missing["gates"]["consumer_gain"]["reasons"] == ["missing_baseline"]
    assert report["scorecards"][3]["candidate_metrics"] is None


def test_flag_off_digest_change_fails_even_when_all_counts_match() -> None:
    data = fixture()
    data["arms"][0]["parity"]["candidate"][0]["output_sha256"] = "0" * 64
    card = assess(data)["scorecards"][0]
    assert card["gates"]["disabled_flag_parity"]["status"] == "fail"
    assert card["parity"]["changed_observations"] == 1
    assert card["parity"]["observed_items"] == 3


def test_missing_plus_measured_failure_keeps_both_reasons() -> None:
    data = fixture()
    data["verifier"] = None
    data["arms"][0]["parity"] = None
    data["recall"]["candidate_hits"] = data["recall"]["baseline_hits"]
    card = assess(data)["scorecards"][0]
    assert card["status"] == "fail"
    assert card["complete"] is False
    assert card["gates"]["recall_gain"]["reasons"] == ["recall_not_increased"]
    assert card["gates"]["verifier_gain"]["status"] == "missing"
    assert card["gates"]["disabled_flag_parity"]["status"] == "missing"


@pytest.mark.parametrize("depth, expected", [(99, "fail"), (100, "pass")])
def test_shortlist_depth_gate(depth: int, expected: str) -> None:
    data = fixture()
    data["recall"]["candidate_depth"] = depth
    assert assess(data)["scorecards"][0]["gates"]["shortlist_depth"]["status"] == expected


@pytest.mark.parametrize("predicted, expected", [(1000, "pass"), (1001, "fail")])
def test_verifier_precision_floor_is_inclusive(predicted: int, expected: str) -> None:
    data = fixture()
    for entry in data["cohort"]["scoreable"]:
        entry["gold_count"] = 100
    data["recall"]["gold_count"] = 200
    for section in [data["verifier"], *data["arms"]]:
        for side in ("baseline", "candidate"):
            for row in section[side]["scoreable"]:
                row["gold_count"] = 100
                row["tp"] = 33
                row["fn"] = 67
                row["predicted_count"] = (
                    predicted if section is data["verifier"] and side == "candidate" else 33
                )
                row["fp"] = row["predicted_count"] - row["tp"]
    card = assess(data)["scorecards"][0]
    assert card["gates"]["verifier_precision"]["status"] == expected


def test_micro_f1_not_mean_item_f1_and_zero_crossing_interval() -> None:
    data = fixture()
    for entry, gold in zip(data["cohort"]["scoreable"], (100, 1), strict=True):
        entry["gold_count"] = gold
    data["recall"].update(gold_count=101, baseline_hits=50, candidate_hits=100)
    for section in [data["verifier"], *data["arms"]]:
        for side in ("baseline", "candidate"):
            for row, gold in zip(section[side]["scoreable"], (100, 1), strict=True):
                tp = (
                    (50 if gold == 100 else 1)
                    if side == "baseline"
                    else (100 if gold == 100 else 0)
                )
                row.update(gold_count=gold, tp=tp, fp=0, fn=gold - tp, predicted_count=tp)
    card = assess(data)["scorecards"][0]
    interval = card["consumer_f1_interval"]
    assert interval["point"] == pytest.approx(200 / 201 - 102 / 152)
    assert interval["point"] > 0  # Mean per-item gain is negative instead.
    assert interval["low"] < 0 < interval["high"]
    assert card["gates"]["consumer_gain"]["status"] == "fail"


@pytest.mark.parametrize(
    "case, expected", [("negative", "fail"), ("zero", "fail"), ("tiny", "pass")]
)
def test_unrounded_interval_boundary(case: str, expected: str) -> None:
    data = fixture()
    if case == "negative":
        arm = data["arms"][0]
        arm["baseline"]["scoreable"], arm["candidate"]["scoreable"] = (
            arm["candidate"]["scoreable"],
            arm["baseline"]["scoreable"],
        )
    elif case == "zero":
        data["arms"][0]["candidate"]["scoreable"] = deepcopy(
            data["arms"][0]["baseline"]["scoreable"]
        )
    else:
        for row in data["cohort"]["scoreable"]:
            row["gold_count"] = 10_000_000
        data["recall"]["gold_count"] = 20_000_000
        for section in [data["verifier"], *data["arms"]]:
            for side in ("baseline", "candidate"):
                for row in section[side]["scoreable"]:
                    tp = 1 if side == "baseline" else 2
                    row.update(
                        gold_count=10_000_000, tp=tp, fp=0, fn=10_000_000 - tp, predicted_count=tp
                    )
    card = assess(data)["scorecards"][0]
    assert card["gates"]["consumer_gain"]["status"] == expected
    if case == "tiny":
        assert 0 < card["consumer_f1_interval"]["low"] < 0.0000005


def test_control_rates_are_separate_and_do_not_add_a_rejection_threshold() -> None:
    data = fixture()
    data["arms"][0]["candidate"]["no_match"][0]["predicted_count"] = 12
    card = assess(data)["scorecards"][0]
    assert card["candidate_metrics"]["f1"] == 1
    assert card["candidate_metrics"]["no_match"] == {
        "false_positive_items": 1,
        "items": 1,
        "rate": 1.0,
    }
    assert card["status"] == "pass"
    data["cohort"].update(no_match=[], no_match_count=0)
    for section in [data["verifier"], *data["arms"]]:
        for side in ("baseline", "candidate"):
            section[side]["no_match"] = []
    for arm in data["arms"]:
        for side in ("baseline", "candidate"):
            arm["parity"][side].pop()
    card = assess(data)["scorecards"][0]
    assert card["candidate_metrics"]["no_match"]["rate"] is None
    assert card["status"] == "missing"
    assert card["complete"] is False


def test_sorted_rows_are_deterministic_and_interval_metadata_is_explicit() -> None:
    data = fixture()
    original = assess(data)
    for section in [data["verifier"], *data["arms"]]:
        for side in ("baseline", "candidate"):
            section[side]["scoreable"].reverse()
            section[side]["no_match"].reverse()
    for arm in data["arms"]:
        for side in ("baseline", "candidate"):
            arm["parity"][side].reverse()
    data["arms"].reverse()
    reordered = assess(data)
    assert original.pop("input_sha256") != reordered.pop("input_sha256")
    assert original == reordered
    assert assess(data) == assess(data)
    interval = original["scorecards"][0]["consumer_f1_interval"]
    assert (interval["n_units"], interval["n_resamples"], interval["seed"], interval["alpha"]) == (
        2,
        2000,
        20260727,
        0.05,
    )
