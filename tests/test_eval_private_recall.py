"""Aggregate-only privacy boundary for the owner-authorized recall run."""

from __future__ import annotations

import json

from folio_eval.private_recall import aggregate_payload


def test_aggregate_payload_excludes_row_level_fields() -> None:
    record = {
        "attempt_id": "attempt-0002",
        "commit_sha": "abc123",
        "gold_version": 3,
        "ontology_hash": "a" * 64,
        "config_hash": "b" * 64,
        "scores_before": {
            "tune": {"precision": 0.2, "recall": 0.21, "f1": 0.205},
            "firm2": {
                "aggregate": {"precision": 0.1, "recall": 0.15, "f1": 0.12},
                "items": [{"item_id": "private-row", "exact": True}],
            },
        },
        "scores_after": {
            "tune": {"precision": 0.22, "recall": 0.23, "f1": 0.225},
            "firm2": {
                "aggregate": {"precision": 0.11, "recall": 0.16, "f1": 0.13},
                "items": [{"item_id": "private-row", "exact": False}],
            },
        },
        "tripwire": {
            "flagged": True,
            "ci_negative": False,
            "any_regression": True,
            "breakdown": {"items": 1, "improved": 0, "regressed": 1, "unchanged": 0, "net": -1},
            "ci": {"low": -1.0, "high": 0.0, "point": -1.0},
        },
        "decision": "park",
        "reason": "automatic decision: AE4 flagged cross-firm risk",
    }

    payload = aggregate_payload(record)
    rendered = json.dumps(payload, sort_keys=True)

    assert payload["decision"] == "park"
    assert payload["tune"]["delta_f1"] == 0.02
    assert payload["firm2"]["changed"] == {
        "items": 1,
        "improved": 0,
        "regressed": 1,
        "unchanged": 0,
        "net": -1,
    }
    assert "private-row" not in rendered
    assert "item_id" not in rendered
    assert '"items": [' not in rendered
