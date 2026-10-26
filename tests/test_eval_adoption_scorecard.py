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
