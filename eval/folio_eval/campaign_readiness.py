"""Content-free operator attestations, never campaign verification or authorization."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import NoReturn, NotRequired, TypedDict


class SnapshotError(ValueError):
    """A fixed diagnostic that never includes supplied content."""

    def __init__(self) -> None:
        super().__init__("invalid_snapshot")


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

FOLLOW_UP = {
    "attestation": "controller: supply missing attestations outside this command.",
    "binding": "evaluation_operator: reconcile binding attestations outside this command.",
    "receipt": "reviewer: reconcile receipt presence and outcome attestations outside this command.",
    "ownership": "controller: reconcile responsible-role assignment outside this command.",
    "role": "controller: supply the responsible role outside this command.",
    "currentness": "controller: reconcile currentness against the intended snapshot outside this command.",
}


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
    follow_up: list[str]


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
    if kind == "ownership" and values["role"] not in {"controller", "unassigned"}:
        raise SnapshotError()
    if kind == "receipt" and values["presence"] == "absent" and values["outcome"] in {"passed", "failed"}:
        raise SnapshotError()
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
        "currentness": values["currentness"], "unresolved_reasons": reasons, "follow_up": [],
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
    row["follow_up"] = list(dict.fromkeys(FOLLOW_UP[reason.split("_", 1)[0]] for reason in reasons))
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


def render_json(summary: Summary) -> str:
    """Render a normalized summary with stable ordering and a final newline."""
    return json.dumps(summary, indent=2, ensure_ascii=True) + "\n"


def render_markdown(summary: Summary) -> str:
    """Render the same fields and follow-ups without interpolating snapshot content."""
    lines = ["# Campaign attestation summary", ""]
    metadata: Mapping[str, object] = summary
    for field in (
        "schema_version", "campaign", "evidence_basis", "underlying_verification",
        "execution_authorization", "adoption_authorization", "attestation_coverage",
    ):
        lines.append(f"{field}: {metadata[field]}")
    lines.extend(["", "Coverage describes supplied attestations only; underlying checks were not performed.", ""])
    lines.extend(f"{key}: {value}" for key, value in summary["counts"].items())
    lines.extend([
        "", "| Check | Kind | Attestation | Binding | Presence | Outcome | Assignment | Role | Currentness | Unresolved reasons |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])
    for row in summary["checks"]:
        cells = [str(row.get(key, "not_applicable")) for key in (
            "check", "kind", "attestation", "binding", "presence", "outcome", "assignment", "role", "currentness",
        )]
        cells.append(", ".join(row["unresolved_reasons"]) or "none")
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "## Follow-up", ""])
    for row in summary["checks"]:
        for action in row["follow_up"]:
            lines.append(f"- {row['check']} — {action}")
    if not any(row["follow_up"] for row in summary["checks"]):
        lines.append("none")
    return "\n".join(lines) + "\n"


class _UsageError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default errors echo paths, values and arguments.
        raise _UsageError()


def _diagnostic(code: str, status: int) -> int:
    try:
        sys.stderr.write(code + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        with suppress(OSError, ValueError):
            sys.stderr.close()
        return 1
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """Read one supplied snapshot; write only stdout and fixed stderr diagnostics."""
    parser = _Parser(
        prog="check_campaign_readiness.py", allow_abbrev=False, add_help=False,
        description="Summarize operator attestations offline; no verification or authorization.",
    )
    parser.add_argument("--snapshot", metavar="SNAPSHOT")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--help", action="store_true", help="show this help")
    try:
        args = parser.parse_args(argv)
        if not args.help and args.snapshot is None:
            raise _UsageError()
    except _UsageError:
        return _diagnostic("invalid_arguments", 2)
    if args.help:
        output = parser.format_help()
    else:
        try:
            text = Path(args.snapshot).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return _diagnostic("snapshot_unreadable", 2)
        try:
            summary = parse_snapshot(text)
        except SnapshotError:
            return _diagnostic("invalid_snapshot", 2)
        output = render_json(summary) if args.format == "json" else render_markdown(summary)
    try:
        sys.stdout.write(output)
        sys.stdout.flush()
    except (OSError, ValueError):
        # A buffered broken pipe must not fail again at interpreter shutdown.
        with suppress(OSError, ValueError):
            sys.stdout.close()
        return _diagnostic("output_failed", 1)
    return 0
