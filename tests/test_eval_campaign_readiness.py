"""Generated attestations only: no campaign artifacts or corpus fixtures are opened."""

from __future__ import annotations

import builtins
import fcntl
import io
import json
import os
import re
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import folio_eval.campaign_readiness as readiness
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


@pytest.mark.parametrize("role", ["reviewer", "evaluation_operator"])
def test_campaign_controller_assignment_requires_controller_role(role):
    snapshot = favorable()
    snapshot["checks"][5]["role"] = role
    with pytest.raises(SnapshotError):
        summarize_snapshot(snapshot)


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


@pytest.mark.parametrize("campaign", ["u10", "retrieval"])
def test_formats_are_deterministic_and_describe_same_summary(campaign):
    snapshot = favorable(campaign)
    snapshot["checks"][0]["binding"] = "mismatched"
    first = summarize_snapshot(snapshot)
    snapshot["checks"].reverse()
    snapshot = dict(reversed(list(snapshot.items())))
    second = parse_snapshot(json.dumps(snapshot))
    for render in (readiness.render_json, readiness.render_markdown):
        assert render(first) == render(second) == render(first)
        assert render(first).endswith("\n")
    assert json.loads(readiness.render_json(first)) == first
    markdown = readiness.render_markdown(first)
    for field in ("campaign", "evidence_basis", "underlying_verification", "attestation_coverage",
                  "execution_authorization", "adoption_authorization"):
        assert f"{field}: {first[field]}" in markdown
    for row in first["checks"]:
        assert row["check"] in markdown
        for reason in row["unresolved_reasons"]:
            assert reason in markdown
        for follow_up in row["follow_up"]:
            assert follow_up in markdown
    assert "evaluation_operator: reconcile binding attestations outside this command." in markdown


@pytest.mark.parametrize("fmt", ["json", "markdown"])
def test_cli_success_with_missing_attestations_does_not_write_files(tmp_path, capsys, fmt):
    snapshot = tmp_path / "snapshot.json"
    contents = '{"schema_version":1,"campaign":"retrieval"}\n'
    snapshot.write_text(contents, encoding="utf-8")
    assert readiness.main(["--snapshot", str(snapshot), "--format", fmt]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "not_performed" in captured.out
    assert "attestation_missing" in captured.out
    assert snapshot.read_text(encoding="utf-8") == contents
    assert list(tmp_path.iterdir()) == [snapshot]


@pytest.mark.parametrize("raw", [
    '{"schema_version":1,"campaign":"u10","notes":"private-sentinel"}',
    '{"schema_version":1,"campaign":"private-sentinel"}',
    '{"schema_version":1,"campaign":"u10","checks":[{"check":"final_completion","outcome":"private-sentinel"}]}',
    '{"private-sentinel', '\ud800', '[',
])
def test_cli_invalid_input_never_echoes_content_or_path(tmp_path, capsys, raw):
    snapshot = tmp_path / "private-sentinel.json"
    snapshot.write_bytes(raw.encode("utf-8", errors="surrogatepass"))
    assert readiness.main(["--snapshot", str(snapshot)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err in {"invalid_snapshot\n", "snapshot_unreadable\n"}
    assert "private-sentinel" not in captured.err


@pytest.mark.parametrize("args", [
    [], ["--private-sentinel"], ["--snapshot"], ["--format", "private-sentinel"],
    ["private-sentinel"], ["--snap", "private-sentinel"],
])
def test_cli_usage_errors_are_fixed(args, capsys):
    assert readiness.main(args) == 2
    assert capsys.readouterr() == ("", "invalid_arguments\n")


@pytest.mark.parametrize("suffix", ["missing-private-sentinel", "\x00private-sentinel"])
def test_cli_unreadable_path_is_fixed(tmp_path, capsys, suffix):
    assert readiness.main(["--snapshot", str(tmp_path / suffix)]) == 2
    assert capsys.readouterr() == ("", "snapshot_unreadable\n")


def test_cli_help_contains_only_static_text(capsys):
    assert readiness.main(["--help"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "--snapshot" in captured.out
    assert "attestations" in captured.out


@pytest.mark.parametrize("operation", ["write", "flush"])
def test_output_failure_returns_one_with_fixed_diagnostic(tmp_path, monkeypatch, capsys, operation):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"schema_version":1,"campaign":"u10"}', encoding="utf-8")

    class BrokenOutput(io.StringIO):
        def write(self, value):
            if operation == "write":
                raise OSError("private-sentinel")
            return super().write(value)

        def flush(self):
            if operation == "flush":
                raise OSError("private-sentinel")
            return super().flush()

    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", BrokenOutput())
        assert readiness.main(["--snapshot", str(snapshot)]) == 1
    assert capsys.readouterr().err == "output_failed\n"


def test_cli_reads_only_explicit_snapshot_and_never_discovers_or_launches(tmp_path, monkeypatch, capsys):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"schema_version":1,"campaign":"u10"}', encoding="utf-8")
    original_open = io.open
    original_import = builtins.__import__
    reads = []

    def guarded_open(path, mode="r", *args, **kwargs):
        assert Path(path) == snapshot
        assert mode == "r"
        reads.append(Path(path))
        return original_open(path, mode, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("readiness attempted external I/O")

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith("folio_eval.") or name == "folio_eval.campaign_readiness"
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", guarded_open)
        patch.setattr(builtins, "open", guarded_open)
        patch.setattr(builtins, "__import__", guarded_import)
        patch.setattr(os, "scandir", forbidden)
        patch.setattr(os, "listdir", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        patch.setattr(os, "system", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(fcntl, "flock", forbidden)
        patch.setattr(fcntl, "lockf", forbidden)
        assert readiness.main(["--snapshot", str(snapshot)]) == 0
    assert reads == [snapshot]
    assert capsys.readouterr().err == ""


def test_launcher_runs_with_isolated_stdlib_and_disables_bytecode(tmp_path):
    # Only the new local CLI is executed; no consumers or provider tools are invoked.
    launcher = Path(__file__).resolve().parents[1] / "eval" / "check_campaign_readiness.py"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"schema_version":1,"campaign":"u10"}', encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-I", str(launcher), "--snapshot", str(snapshot)],
        cwd=tmp_path, check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["counts"]["required_checks"] == 11


def test_readme_inventories_and_examples_match_the_public_contract(capsys):
    readme = (Path(__file__).resolve().parents[1] / "eval" / "README.md").read_text(encoding="utf-8")
    for campaign in ("u10", "retrieval"):
        documented = []
        for line in readme.splitlines():
            if line.startswith(f"| `{campaign}` |"):
                documented.extend(re.findall(r"`([^`]+)`", line.split("|")[3]))
        assert documented == [row["check"] for row in favorable(campaign)["checks"]]
    partial = readme.split("<!-- readiness-partial-example -->", 1)[1].split("```json\n", 1)[1].split("```", 1)[0]
    summary = parse_snapshot(partial)
    assert summary["counts"]["required_checks"] == 11
    assert summary["counts"]["unresolved_checks"] == 11
    assert summary["attestation_coverage"] == "partial"
    code = readme.split("<!-- readiness-favorable-example -->", 1)[1].split("```python\n", 1)[1].split("```", 1)[0]
    for campaign in ("u10", "retrieval"):
        exec(code.replace('campaign = "u10"', f'campaign = "{campaign}"'), {})
        generated = json.loads(capsys.readouterr().out)
        assert generated == favorable(campaign)
        result = summarize_snapshot(generated)
        assert result["attestation_coverage"] == "complete"
        assert result["underlying_verification"] == "not_performed"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_closed_output_stream_returns_one(tmp_path, monkeypatch, capsys, stream):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"schema_version":1,"campaign":"u10"}', encoding="utf-8")
    closed = io.StringIO()
    closed.close()
    with monkeypatch.context() as patch:
        patch.setattr(sys, stream, closed)
        args = ["--snapshot", str(snapshot)] if stream == "stdout" else ["--invalid"]
        assert readiness.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ("output_failed\n" if stream == "stdout" else "")


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_launcher_broken_pipe_preserves_exit_one(tmp_path, stream):
    launcher = Path(__file__).resolve().parents[1] / "eval" / "check_campaign_readiness.py"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"schema_version":1,"campaign":"u10"}', encoding="utf-8")
    args = ["--snapshot", str(snapshot)] if stream == "stdout" else ["--invalid"]
    reader, writer = os.pipe()
    os.close(reader)
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(launcher), *args],
            stdout=writer if stream == "stdout" else subprocess.PIPE,
            stderr=writer if stream == "stderr" else subprocess.PIPE,
            check=False,
        )
    finally:
        os.close(writer)
    assert result.returncode == 1
    if stream == "stdout":
        assert result.stderr == b"output_failed\n"
    else:
        assert result.stdout == b""


def test_launcher_leaves_no_bytecode_or_other_artifacts(tmp_path):
    source = Path(__file__).resolve().parents[1] / "eval"
    package = tmp_path / "folio_eval"
    package.mkdir()
    for relative in (
        "check_campaign_readiness.py", "folio_eval/__init__.py", "folio_eval/campaign_readiness.py",
    ):
        (tmp_path / relative).write_bytes((source / relative).read_bytes())
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"schema_version":1,"campaign":"u10"}', encoding="utf-8")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "check_campaign_readiness.py"), "--snapshot", str(snapshot)],
        check=False, capture_output=True,
    )
    assert result.returncode == 0
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
