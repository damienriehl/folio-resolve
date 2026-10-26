"""Content-free operator attestations, never campaign verification or authorization."""

from __future__ import annotations

import json
from typing import NotRequired, TypedDict


class SnapshotError(ValueError):
    """A fixed diagnostic that never includes supplied content."""

    def __init__(self) -> None:
        super().__init__("invalid_snapshot")


# Ordered schema-v1 inventory. Keep independent from producer imports and their I/O.
INVENTORIES: dict[str, tuple[tuple[str, str], ...]] = {
    "u10": (
        ("candidate_source_runtime", "binding"),
        ("corpus_config", "binding"),
        ("leak_manifest", "binding"),
        ("public_metadata_output", "binding"),
        ("pinned_consumer_environments", "binding"),
        ("controller_assignment", "ownership"),
        ("shard_receipt_validation", "receipt"),
        ("final_completion", "receipt"),
        ("independent_regeneration", "receipt"),
        ("zero_collision_acceptance", "receipt"),
        ("campaign_report_publication", "receipt"),
    ),
    "retrieval": (
        ("corpus_config_population", "binding"),
        ("attribution_input_artifacts", "binding"),
        ("pinned_consumer_environments", "binding"),
        ("shared_campaign_directory", "binding"),
        ("cumulative_budget_ledger", "binding"),
        ("controller_assignment", "ownership"),
        ("attribution_finalization_r4", "receipt"),
        ("canary_projection_review", "receipt"),
        ("folio-enrich-deterministic", "receipt"),
        ("folio-mapper-deterministic", "receipt"),
        ("folio-mapper-gemini-3-flash-preview", "receipt"),
        ("folio-mapper-gpt-6-luna", "receipt"),
        ("embedding_ceiling", "receipt"),
        ("llm_ceiling", "receipt"),
        ("final_report_pair", "receipt"),
        ("zero_collision_acceptance", "receipt"),
        ("experiment_park_entry", "receipt"),
    ),
}

# Each field has an explicit omission default; none is favorable.
VOCABULARY = {
    "binding": ("not_checked", "matched", "mismatched", "conflict"),
    "presence": ("unknown", "present", "absent"),
    "outcome": ("not_checked", "passed", "failed", "conflict"),
    "assignment": ("unknown", "assigned", "unassigned", "conflict"),
    "role": ("unassigned", "controller", "evaluation_operator", "reviewer"),
    "currentness": ("unknown", "current", "stale", "conflict"),
}
KIND_FIELDS = {"binding": ("binding",), "receipt": ("presence", "outcome"), "ownership": ()}
COMMON_FIELDS = ("assignment", "role", "currentness")


class CheckSummary(TypedDict):
    check: str
    kind: str
    attestation: str
    assignment: str
    role: str
    currentness: str
    binding: NotRequired[str]
    presence: NotRequired[str]
    outcome: NotRequired[str]
    unresolved_reasons: list[str]


class Summary(TypedDict):
    schema_version: int
    campaign: str
    evidence_basis: str
    underlying_verification: str
    execution_authorization: str
    adoption_authorization: str
    attestation_coverage: str
    counts: dict[str, int]
    checks: list[CheckSummary]


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise SnapshotError()
    return value


def _enum(record: dict[str, object], field: str) -> str:
    choices = VOCABULARY[field]
    value = record.get(field, choices[0])
    if not isinstance(value, str) or value not in choices:
        raise SnapshotError()
    return value


def _normalize(check: str, kind: str, raw: dict[str, object] | None) -> CheckSummary:
    fields = (*KIND_FIELDS[kind], *COMMON_FIELDS)
    record = raw if raw is not None else {}
    if record.keys() - {"check", *fields}:
        raise SnapshotError()
    values = {field: _enum(record, field) for field in fields}
    if kind == "receipt" and values["presence"] == "absent" and values["outcome"] in {"passed", "failed"}:
        raise SnapshotError()
    # Missing fields remain partial; reject contradictions only when both were supplied.
    if "assignment" in record and "role" in record:
        if values["assignment"] == "assigned" and values["role"] == "unassigned":
            raise SnapshotError()
        if values["assignment"] == "unassigned" and values["role"] != "unassigned":
            raise SnapshotError()
    attestation = "missing" if raw is None else "complete" if set(fields) <= record.keys() else "partial"
    reasons = [] if attestation == "complete" else [f"attestation_{attestation}"]
    row: CheckSummary = {
        "check": check, "kind": kind, "attestation": attestation,
        "assignment": values["assignment"], "role": values["role"],
        "currentness": values["currentness"], "unresolved_reasons": reasons,
    }
    if kind == "binding":
        row["binding"] = values["binding"]
        if row["binding"] != "matched":
            reasons.append(f"binding_{row['binding']}")
    elif kind == "receipt":
        row["presence"], row["outcome"] = values["presence"], values["outcome"]
        if row["presence"] != "present":
            reasons.append("receipt_absent" if row["presence"] == "absent" else "receipt_presence_unknown")
        if row["outcome"] != "passed":
            reasons.append(f"receipt_{row['outcome']}")
    if row["assignment"] != "assigned":
        reasons.append(f"ownership_{row['assignment']}")
    if row["role"] == "unassigned":
        reasons.append("role_unassigned")
    if row["currentness"] != "current":
        reasons.append(f"currentness_{row['currentness']}")
    return row


def summarize_snapshot(snapshot: object) -> Summary:
    """Validate the entire snapshot and expand its inventory without external I/O."""
    data = _object(snapshot)
    if data.keys() - {"schema_version", "campaign", "checks"}:
        raise SnapshotError()
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise SnapshotError()
    campaign = data.get("campaign")
    if not isinstance(campaign, str) or campaign not in INVENTORIES:
        raise SnapshotError()
    supplied = data.get("checks", [])
    if not isinstance(supplied, list):
        raise SnapshotError()
    inventory = INVENTORIES[campaign]
    kinds = dict(inventory)
    records: dict[str, dict[str, object]] = {}
    for value in supplied:
        record = _object(value)
        check = record.get("check")
        if not isinstance(check, str) or check not in kinds or check in records:
            raise SnapshotError()
        records[check] = record
    checks = [_normalize(check, kind, records.get(check)) for check, kind in inventory]
    full = sum(row["attestation"] == "complete" for row in checks)
    return {
        "schema_version": 1, "campaign": campaign,
        "evidence_basis": "operator_attestation", "underlying_verification": "not_performed",
        "execution_authorization": "not_granted", "adoption_authorization": "not_granted",
        "attestation_coverage": "complete" if full == len(checks) else "partial" if records else "none",
        "counts": {
            "required_checks": len(checks), "supplied_checks": len(records),
            "fully_attested_checks": full,
            "unresolved_checks": sum(bool(row["unresolved_reasons"]) for row in checks),
        },
        "checks": checks,
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError()
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise SnapshotError()


def parse_snapshot(text: str) -> Summary:
    """Reject duplicate keys and non-JSON constants before normalization."""
    try:
        data = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        return summarize_snapshot(data)
    except (ValueError, RecursionError):
        raise SnapshotError() from None
