"""Generated attestations only: no campaign artifacts or corpus fixtures are opened."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from folio_eval.campaign_readiness import SnapshotError, parse_snapshot, summarize_snapshot


# Deliberately independent of the production inventory: a removed check must fail tests.
U10_BINDINGS = (
    "candidate_source_runtime", "corpus_config", "leak_manifest",
    "public_metadata_output", "pinned_consumer_environments",
)
U10_RECEIPTS = (
    "shard_receipt_validation", "final_completion", "independent_regeneration",
    "zero_collision_acceptance", "campaign_report_publication",
)
RETRIEVAL_BINDINGS = (
    "corpus_config_population", "attribution_input_artifacts", "pinned_consumer_environments",
    "shared_campaign_directory", "cumulative_budget_ledger",
)
RETRIEVAL_RECEIPTS = (
    "attribution_finalization_r4", "canary_projection_review", "folio-enrich-deterministic",
    "folio-mapper-deterministic", "folio-mapper-gemini-3-flash-preview",
    "folio-mapper-gpt-6-luna", "embedding_ceiling", "llm_ceiling", "final_report_pair",
    "zero_collision_acceptance", "experiment_park_entry",
)


def favorable(campaign="u10"):
    bindings = U10_BINDINGS if campaign == "u10" else RETRIEVAL_BINDINGS
    receipts = U10_RECEIPTS if campaign == "u10" else RETRIEVAL_RECEIPTS
    common = {"assignment": "assigned", "role": "controller", "currentness": "current"}
    return {
        "schema_version": 1,
        "campaign": campaign,
        "checks": [
            *[{"check": check, **common, "binding": "matched"} for check in bindings],
            {"check": "controller_assignment", **common},
            *[{
                "check": check, **common, "presence": "present", "outcome": "passed",
            } for check in receipts],
        ],
    }


@pytest.mark.parametrize("campaign,total", [("u10", 11), ("retrieval", 17)])
def test_missing_checks_cannot_shrink_required_inventory(campaign, total):
    summary = summarize_snapshot({"schema_version": 1, "campaign": campaign})
    assert len(summary["checks"]) == total
    assert summary["counts"] == {
        "required_checks": total, "supplied_checks": 0, "fully_attested_checks": 0,
        "unresolved_checks": total,
    }
    assert summary["attestation_coverage"] == "none"
    for check in summary["checks"]:
        assert check["attestation"] == "missing"
        assert check["currentness"] == "unknown"
        assert "attestation_missing" in check["unresolved_reasons"]


@pytest.mark.parametrize("campaign", ["u10", "retrieval"])
def test_full_attestations_never_claim_verification_or_authorization(campaign):
    summary = summarize_snapshot(favorable(campaign))
    assert summary["attestation_coverage"] == "complete"
    assert summary["counts"]["unresolved_checks"] == 0
    assert summary["evidence_basis"] == "operator_attestation"
    assert summary["underlying_verification"] == "not_performed"
    assert summary["execution_authorization"] == "not_granted"
    assert summary["adoption_authorization"] == "not_granted"
    assert all(not row["unresolved_reasons"] for row in summary["checks"])


def test_passed_receipts_do_not_hide_other_adverse_dimensions():
    snapshot = favorable()
    snapshot["checks"][0].update(binding="mismatched", currentness="stale")
    snapshot["checks"][5].update(assignment="unassigned", role="unassigned")
    summary = summarize_snapshot(snapshot)
    assert summary["attestation_coverage"] == "complete"  # Coverage is not success.
    assert summary["checks"][0]["unresolved_reasons"] == ["binding_mismatched", "currentness_stale"]
    assert summary["checks"][5]["unresolved_reasons"] == ["ownership_unassigned", "role_unassigned"]
    assert summary["counts"]["unresolved_checks"] == 2
    assert summary["checks"][6]["outcome"] == "passed"


def test_omitted_fields_are_partial_even_when_normalized_values_are_supplied_elsewhere():
    summary = summarize_snapshot({
        "schema_version": 1, "campaign": "u10", "checks": [{"check": "final_completion", "outcome": "passed"}],
    })
    row = next(row for row in summary["checks"] if row["check"] == "final_completion")
    assert row["attestation"] == "partial"
    assert row["presence"] == "unknown"
    assert row["unresolved_reasons"] == [
        "attestation_partial", "receipt_presence_unknown", "ownership_unknown",
        "role_unassigned", "currentness_unknown",
    ]


@pytest.mark.parametrize("presence,outcome,reason", [
    ("absent", "not_checked", "receipt_absent"),
    ("present", "not_checked", "receipt_not_checked"),
    ("present", "failed", "receipt_failed"),
    ("unknown", "passed", "receipt_presence_unknown"),
    ("present", "conflict", "receipt_conflict"),
])
def test_receipt_dimensions_stay_distinct(presence, outcome, reason):
    snapshot = favorable()
    snapshot["checks"][6].update(presence=presence, outcome=outcome)
    row = summarize_snapshot(snapshot)["checks"][6]
    assert row["presence"] == presence
    assert row["outcome"] == outcome
    assert reason in row["unresolved_reasons"]


def test_all_conflicts_survive_in_fixed_order():
    snapshot = favorable()
    snapshot["checks"][0].update(binding="conflict", assignment="conflict", currentness="conflict")
    assert summarize_snapshot(snapshot)["checks"][0]["unresolved_reasons"] == [
        "binding_conflict", "ownership_conflict", "currentness_conflict",
    ]


@pytest.mark.parametrize("patch", [
    {"presence": "absent", "outcome": "passed"},
    {"presence": "absent", "outcome": "failed"},
    {"assignment": "assigned", "role": "unassigned"},
    {"assignment": "unassigned", "role": "reviewer"},
    {"binding": "matched"}, {"outcome": None}, {"role": "private-sentinel"},
    {"evidence_path": "private-sentinel"}, {"currentness": True},
])
def test_invalid_receipt_fields_fail_without_values(patch):
    snapshot = favorable()
    snapshot["checks"][6].update(patch)
    with pytest.raises(SnapshotError, match="^invalid_snapshot$"):
        summarize_snapshot(snapshot)


@pytest.mark.parametrize("snapshot", [
    None, [], True, {}, {"schema_version": True, "campaign": "u10"},
    {"schema_version": 1.0, "campaign": "u10"}, {"schema_version": 2, "campaign": "u10"},
    {"schema_version": 1, "campaign": "private-sentinel"},
    {"schema_version": 1, "campaign": "u10", "private-sentinel": "private-sentinel"},
    {"schema_version": 1, "campaign": "u10", "checks": {}},
    {"schema_version": 1, "campaign": "u10", "checks": [None]},
    {"schema_version": 1, "campaign": "u10", "checks": [{}]},
    {"schema_version": 1, "campaign": "u10", "checks": [{"check": []}]},
    {"schema_version": 1, "campaign": "u10", "checks": [{"check": "controller_assignment", "presence": "present"}]},
])
def test_strict_snapshot_schema(snapshot):
    with pytest.raises(SnapshotError, match="^invalid_snapshot$"):
        summarize_snapshot(snapshot)


@pytest.mark.parametrize("raw", [
    '{"schema_version":1,"schema_version":1,"campaign":"u10"}',
    '{"schema_version":1,"campaign":"u10","checks":[{"check":"final_completion","outcome":"passed","outcome":"failed"}]}',
    '{"schema_version":NaN,"campaign":"u10"}',
    '{"schema_version":Infinity,"campaign":"u10"}',
    '{private-sentinel', '[' * 2000,
])
def test_json_errors_are_fixed_and_duplicate_keys_are_rejected(raw):
    with pytest.raises(SnapshotError, match="^invalid_snapshot$"):
        parse_snapshot(raw)


def test_duplicate_and_disabled_checks_are_rejected():
    snapshot = favorable("retrieval")
    snapshot["checks"].append(deepcopy(snapshot["checks"][0]))
    with pytest.raises(SnapshotError):
        summarize_snapshot(snapshot)
    for arm in ("folio-enrich-gemini-3-flash-preview", "folio-enrich-gpt-6-luna"):
        with pytest.raises(SnapshotError):
            summarize_snapshot({"schema_version": 1, "campaign": "retrieval", "checks": [{"check": arm}]})


def test_normalization_does_not_mutate_supplied_snapshot():
    snapshot = favorable()
    before = deepcopy(snapshot)
    assert parse_snapshot(json.dumps(snapshot)) == summarize_snapshot(snapshot)
    assert snapshot == before
