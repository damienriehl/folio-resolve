"""Tests for folio_eval.downstream — U9, R13, KTD10, synthetic fixtures only.

No consumer repo, no venv, no network: the editable-install/subprocess seams
(``run_pytest``, ``run_mapper_probe``, ``run_enrich_harness``, ``editable_install``) are exercised
in the real snapshot run, not here. This file proves the pure logic that governs KTD10's contract:
the blocking/advisory diff classifier, the snapshot round-trip, the junitxml parser, the leak
scan on the committed aggregate, and the ``__file__``-containment assertion's failure path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from folio_eval.downstream import (
    ConsumerSnapshot,
    ConsumerTreeDirty,
    DiffVerdict,
    EditableInstallMismatch,
    HarnessResult,
    ProbeAreaResult,
    ProbeItemResult,
    TestSuiteResult,
    _execution_receipt,
    _parser,
    assert_within_root,
    build_aggregate,
    canonical_json,
    classify_probe_diff,
    classify_probe_item_delta,
    classify_test_diff,
    clean_tree_guard,
    diff_snapshots,
    load_row_snapshot,
    main,
    parse_junitxml,
    write_aggregate,
    write_row_snapshot,
)

# --------------------------------------------------------------------------------------
# __file__-containment assertion — the failure path
# --------------------------------------------------------------------------------------


def test_synthetic_comparison_cli_has_explicit_scoreable_only_live_gate() -> None:
    args = _parser().parse_args(
        [
            "run_synthetic_comparison",
            "--corpus-manifest",
            "corpus.json",
            "--config",
            "config.json",
            "--out",
            "comparison.json",
            "--items",
            "items.jsonl",
            "--row-snapshot-dir",
            "snapshots",
            "--leak-manifest",
            "leaks.json",
            "--salt-file",
            "salt",
            "--limit",
            "1",
            "--scoreable-only",
        ]
    )

    assert args.limit == 1
    assert args.scoreable_only is True
    assert args.public_metadata.name == "public_comparison_metadata_v1.json"


def test_synthetic_comparison_cli_accepts_repeatable_item_shards() -> None:
    args = _parser().parse_args([*_comparison_argv(), "--item-id", "one", "--item-id", "none"])

    assert args.item_id == ["one", "none"]


def _comparison_argv(*extra: str) -> list[str]:
    return [
        "run_synthetic_comparison",
        "--corpus-manifest",
        "corpus.json",
        "--config",
        "config.json",
        "--out",
        "comparison.json",
        "--items",
        "items.jsonl",
        "--row-snapshot-dir",
        "snapshots",
        "--leak-manifest",
        "leaks.json",
        "--salt-file",
        "salt",
        *extra,
    ]


def test_synthetic_comparison_cli_rejects_abbreviated_metadata_options() -> None:
    abbreviated = _comparison_argv()
    abbreviated[abbreviated.index("--config")] = "--conf"

    with pytest.raises(SystemExit) as error:
        _parser().parse_args(abbreviated)

    assert error.value.code == 2


@pytest.mark.parametrize("extra", [(), ("--limit", "30")])
def test_scoreable_only_rejects_non_live_gate_limits(extra: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit) as error:
        main([*_comparison_argv(*extra), "--scoreable-only"])

    assert error.value.code == 2


def test_item_shard_rejects_limit_or_scoreable_only() -> None:
    with pytest.raises(SystemExit):
        main([*_comparison_argv("--limit", "1"), "--item-id", "one"])
    with pytest.raises(SystemExit):
        main([*_comparison_argv("--limit", "1"), "--scoreable-only", "--item-id", "one"])


def test_execution_receipt_records_resolved_process_and_determinism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "executable", "/tmp/venv/bin/python")
    monkeypatch.setattr(sys, "argv", ["eval/run_downstream.py", "run_synthetic_comparison"])
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    receipt = _execution_receipt(("run_synthetic_comparison",), supplied_argv=False)

    assert receipt["kind"] == "executed_process"
    assert receipt["argv"] == [
        "/tmp/venv/bin/python",
        "eval/run_downstream.py",
        "run_synthetic_comparison",
    ]
    assert receipt["environment"] == {"PYTHONHASHSEED": "0"}


def test_assert_within_root_passes_for_a_path_inside_the_checkout(tmp_path: Path) -> None:
    root = tmp_path / "folio-resolve"
    inside = root / "src" / "folio_resolve" / "__init__.py"
    inside.parent.mkdir(parents=True)
    inside.touch()
    assert assert_within_root(inside, root) == inside


def test_assert_within_root_raises_for_a_fake_module_path_outside_the_checkout(
    tmp_path: Path,
) -> None:
    """The KTD10 abort condition: folio_resolve resolves somewhere other than the local checkout
    (e.g. the consumer venv's site-packages, if a bare ``uv run`` silently reverted the editable
    install)."""
    root = tmp_path / "folio-resolve"
    root.mkdir()
    fake_site_packages = (
        tmp_path / "some-consumer" / "backend" / ".venv" / "lib" / "site-packages" / "folio_resolve"
        / "__init__.py"
    )
    with pytest.raises(EditableInstallMismatch, match="NOT inside"):
        assert_within_root(fake_site_packages, root)


def test_assert_within_root_raises_when_the_pinned_root_is_a_sibling_not_an_ancestor(
    tmp_path: Path,
) -> None:
    """A near-miss: same parent directory, different repo -- must not pass by string-prefix luck."""
    root = tmp_path / "folio-resolve"
    sibling = tmp_path / "folio-resolve-fork" / "src" / "folio_resolve" / "__init__.py"
    root.mkdir()
    with pytest.raises(EditableInstallMismatch):
        assert_within_root(sibling, root)


# --------------------------------------------------------------------------------------
# Diff classifier (KTD10): previously-correct->incorrect = blocking, else advisory
# --------------------------------------------------------------------------------------


def test_dropped_candidate_is_blocking_and_gained_candidate_is_advisory() -> None:
    verdict = classify_probe_item_delta(
        "solo-criminal#3",
        before_iris=frozenset({"R-arson", "R-burglary"}),
        after_iris=frozenset({"R-arson", "R-wire-fraud"}),
    )
    assert verdict.blocking == ("solo-criminal#3: lost candidate R-burglary",)
    assert verdict.advisory == ("solo-criminal#3: gained candidate R-wire-fraud",)


def test_unchanged_candidate_set_produces_no_deltas() -> None:
    verdict = classify_probe_item_delta(
        "k", before_iris=frozenset({"R-a"}), after_iris=frozenset({"R-a"})
    )
    assert verdict.blocking == ()
    assert verdict.advisory == ()


def test_classify_probe_diff_covers_missing_and_new_items() -> None:
    before = {"a#1": frozenset({"R-x"}), "a#2": frozenset({"R-y"})}
    after = {"a#1": frozenset({"R-x"}), "a#3": frozenset({"R-z"})}
    verdict = classify_probe_diff(before, after)
    assert "a#2: item missing from the new run entirely" in verdict.blocking
    assert "a#3: new item in the new run" in verdict.advisory
    # a#1 unchanged -> no entry either way
    assert not any("a#1" in line for line in verdict.blocking + verdict.advisory)


def test_classify_test_diff_previously_passing_now_failing_is_blocking() -> None:
    before = {"tests/test_x.py::test_a": "passed", "tests/test_x.py::test_b": "passed"}
    after = {"tests/test_x.py::test_a": "passed", "tests/test_x.py::test_b": "failed"}
    verdict = classify_test_diff(before, after)
    assert verdict.blocking == ("tests/test_x.py::test_b: passed -> failed",)
    assert verdict.advisory == ()


def test_classify_test_diff_previously_passing_now_error_is_also_blocking() -> None:
    verdict = classify_test_diff({"t": "passed"}, {"t": "error"})
    assert verdict.blocking == ("t: passed -> error",)


def test_classify_test_diff_new_test_and_newly_failing_never_passed_are_advisory() -> None:
    before = {"t1": "failed"}
    after = {"t1": "failed", "t2": "passed"}  # t1 stayed failed (no delta), t2 is brand new
    verdict = classify_test_diff(before, after)
    assert verdict.advisory == ("t2: new -> passed",)
    assert verdict.blocking == ()


def test_classify_test_diff_a_previously_failing_test_that_now_passes_is_advisory() -> None:
    verdict = classify_test_diff({"t": "failed"}, {"t": "passed"})
    assert verdict.advisory == ("t: failed -> passed",)
    assert verdict.blocking == ()


def test_diff_verdict_add_concatenates_both_buckets() -> None:
    a = DiffVerdict(blocking=("b1",), advisory=("a1",))
    b = DiffVerdict(blocking=("b2",), advisory=())
    combined = a + b
    assert combined.blocking == ("b1", "b2")
    assert combined.advisory == ("a1",)
    assert combined.has_blocking is True
    assert DiffVerdict().has_blocking is False


def test_diff_snapshots_combines_probes_harness_and_tests_into_one_verdict() -> None:
    before = {
        "probes": {
            "solo-criminal": {
                "items": [
                    {"index": 1, "item": "x", "top_relevant_iris": ["R-a", "R-b"]},
                ]
            }
        },
        "harness": {"label_resolution": [{"id": "h-1", "iri": "R-h"}]},
        "tests": {"outcomes": {"t1": "passed", "t2": "passed"}},
    }
    after = {
        "probes": {
            "solo-criminal": {
                "items": [
                    {"index": 1, "item": "x", "top_relevant_iris": ["R-a"]},  # lost R-b
                ]
            }
        },
        "harness": {"label_resolution": [{"id": "h-1", "iri": ""}]},  # lost its resolution
        "tests": {"outcomes": {"t1": "passed", "t2": "failed"}},  # regressed
    }
    verdict = diff_snapshots(before, after)
    assert any("lost candidate R-b" in line for line in verdict.blocking)
    assert any("lost candidate R-h" in line for line in verdict.blocking)
    assert "t2: passed -> failed" in verdict.blocking
    assert verdict.has_blocking is True


def test_diff_snapshots_on_an_unchanged_library_reports_zero_blocking() -> None:
    """The U9 acceptance bar: 'a diff run reports zero blocking regressions on an unchanged
    library.'"""
    snapshot = {
        "probes": {"a": {"items": [{"index": 1, "item": "x", "top_relevant_iris": ["R-1"]}]}},
        "tests": {"outcomes": {"t1": "passed", "t2": "failed"}},
    }
    verdict = diff_snapshots(snapshot, snapshot)
    assert verdict == DiffVerdict()


# --------------------------------------------------------------------------------------
# junitxml parsing
# --------------------------------------------------------------------------------------


_JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
    <testcase classname="tests.test_a" name="test_pass" time="0.01"/>
    <testcase classname="tests.test_a" name="test_fail" time="0.01">
      <failure message="boom">traceback</failure>
    </testcase>
    <testcase classname="tests.test_a" name="test_error" time="0.01">
      <error message="kaboom">traceback</error>
    </testcase>
    <testcase classname="tests.test_a" name="test_skip" time="0.0">
      <skipped message="not today"/>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_parse_junitxml_classifies_each_outcome(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    path.write_text(_JUNIT_XML, encoding="utf-8")
    outcomes = parse_junitxml(path)
    assert outcomes == {
        "tests.test_a::test_pass": "passed",
        "tests.test_a::test_fail": "failed",
        "tests.test_a::test_error": "error",
        "tests.test_a::test_skip": "skipped",
    }


def test_parse_junitxml_missing_file_returns_empty() -> None:
    assert parse_junitxml(Path("/nonexistent/report.xml")) == {}


# --------------------------------------------------------------------------------------
# Snapshot round-trip (row-level) + aggregate leak scan (committed)
# --------------------------------------------------------------------------------------


def _sample_snapshot() -> ConsumerSnapshot:
    probes = {
        "solo-criminal": ProbeAreaResult(
            area="solo-criminal",
            items=(
                ProbeItemResult(
                    index=1,
                    item="Burglary",
                    level="leaf",
                    top_relevant_iris=("R-burglary", "R-theft"),
                    total_candidates=12,
                    relevant_candidates=5,
                    high_score_relevant=2,
                ),
            ),
            elapsed_s=1.234,
            returncode=0,
        )
    }
    tests = TestSuiteResult(
        consumer="folio-mapper",
        command=("python", "-m", "pytest", "tests"),
        returncode=1,
        elapsed_s=12.5,
        outcomes={"tests/test_a.py::test_x": "passed", "tests/test_a.py::test_y": "failed"},
    )
    return ConsumerSnapshot(
        consumer="folio-mapper",
        resolved_folio_resolve_file="/repo/src/folio_resolve/__init__.py",
        probes=probes,
        tests=tests,
    )


def test_snapshot_round_trips_through_row_json(tmp_path: Path) -> None:
    snap = _sample_snapshot()
    path = write_row_snapshot(snap, tmp_path)
    assert path == tmp_path / "folio-mapper" / "snapshot.json"
    loaded = load_row_snapshot(path)
    assert loaded["consumer"] == "folio-mapper"
    assert loaded["probes"]["solo-criminal"]["items"][0]["item"] == "Burglary"
    assert loaded["tests"]["outcomes"]["tests/test_a.py::test_y"] == "failed"
    # round-trips back into a diffable shape identical to the live object's own row_json
    assert loaded == snap.to_row_json()


def test_load_row_snapshot_rejects_a_non_object_payload(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(Exception, match="not a JSON object"):
        load_row_snapshot(path)


def test_harness_result_round_trips_and_counts_resolved() -> None:
    harness = HarnessResult(
        label_resolution=(("h-1", "R-a"), ("h-2", ""), ("h-3", "R-c")),
        elapsed_s=0.5,
        returncode=0,
        environment={"folio_resolve_present": True},
    )
    summary = harness.to_summary_json()
    assert summary["items"] == 3
    assert summary["resolved"] == 2
    assert summary["folio_resolve_present"] is True
    row = harness.to_row_json()
    assert {"id": "h-2", "iri": ""} in row["label_resolution"]


def test_build_aggregate_is_counts_and_hashes_only_no_surface_text() -> None:
    snap = _sample_snapshot()
    aggregate = build_aggregate([snap], label="baseline-v1")
    payload_text = canonical_json(aggregate)
    # The row-level surface string ("Burglary") must never reach the committed aggregate.
    assert "Burglary" not in payload_text
    assert "R-burglary" not in payload_text
    assert "content_sha256" in payload_text
    assert aggregate["consumers"]["folio-mapper"]["probes"]["solo-criminal"]["items"] == 1


def test_write_aggregate_leak_scan_catches_a_gold_surface_string_in_the_payload(
    tmp_path: Path,
) -> None:
    out = tmp_path / "downstream-baseline-v1.json"
    clean_payload = {"kind": "downstream_baseline", "consumers": {}}
    write_aggregate(clean_payload, out, leak_surfaces=["Fund Formation"])
    assert out.exists()

    leaking_payload = {"kind": "downstream_baseline", "note": "Fund Formation slipped in"}
    with pytest.raises(Exception, match="firm surface string"):
        write_aggregate(leaking_payload, tmp_path / "other.json", leak_surfaces=["Fund Formation"])


def test_write_aggregate_with_no_leak_surfaces_skips_the_scan(tmp_path: Path) -> None:
    out = tmp_path / "agg.json"
    # No gold available at call time (e.g. a dry run) -- must not require leak_surfaces.
    path = write_aggregate({"kind": "downstream_baseline"}, out, leak_surfaces=())
    assert path == out
    assert json.loads(out.read_text())["kind"] == "downstream_baseline"


# --------------------------------------------------------------------------------------
# clean_tree_guard — the working-tree-hygiene tripwire
# --------------------------------------------------------------------------------------


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_clean_tree_guard_passes_when_nothing_changes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    with clean_tree_guard(tmp_path):
        pass  # no-op: the tree really is unchanged


def test_clean_tree_guard_raises_when_a_tracked_file_is_left_modified(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    with pytest.raises(ConsumerTreeDirty), clean_tree_guard(tmp_path):
        (tmp_path / "tracked.txt").write_text("mutated by the runner\n", encoding="utf-8")


def test_clean_tree_guard_raises_when_a_new_untracked_file_is_left_behind(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    with pytest.raises(ConsumerTreeDirty), clean_tree_guard(tmp_path):
        (tmp_path / "leftover.json").write_text("{}", encoding="utf-8")


def test_clean_tree_guard_tolerates_pre_existing_untracked_cruft(tmp_path: Path) -> None:
    """folio-mapper has pre-existing untracked files unrelated to this runner (.claude/, a plan
    doc). The guard must accept a repo that starts non-clean, as long as nothing NEW appears."""
    _init_repo(tmp_path)
    (tmp_path / "pre-existing.json").write_text("{}", encoding="utf-8")
    with clean_tree_guard(tmp_path):
        pass  # the pre-existing untracked file is part of "before" -- not a new delta
    # still there afterward, untouched
    assert (tmp_path / "pre-existing.json").exists()


def test_clean_tree_guard_does_not_mask_the_original_exception(tmp_path: Path) -> None:
    """A failure inside the guard should propagate as itself, not get replaced by a dirty-tree
    error just because the failure path left something on disk."""
    _init_repo(tmp_path)

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom), clean_tree_guard(tmp_path):
        raise Boom("the subprocess call itself failed")
