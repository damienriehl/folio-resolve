"""Synthetic cross-stack comparison orchestration (U10c)."""

from __future__ import annotations

import json
import subprocess
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import folio_eval.comparison as comparison_module
import pytest
from folio_eval.answer_rule import AnswerRuleConfig
from folio_eval.comparison import (
    DEFAULT_COMPARISON_PUBLIC_METADATA_PATH,
    ComparisonError,
    IncumbentInstallMismatch,
    StackContractError,
    StackRun,
    VersionSkewError,
    assert_incumbent_probe,
    build_comparison,
    classify_verdict,
    emit_items_file,
    load_public_comparison_metadata,
    parse_stack_output,
    preflight_comparison_publication,
    run_consumer_stack,
    run_local_stack,
    score_stack,
    write_comparison,
    write_stage_snapshots,
)
from folio_eval.downstream import ConsumerRunError, ConsumerSpec
from folio_eval.leakcheck import build_manifest
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem
from folio_eval.synthetic_score import AdapterResult, CandidateTrace

from folio_resolve.pipeline import MatchCandidate


def _corpus(tmp_path: Path) -> LoadedCorpus:
    config = AnswerRuleConfig()
    scoreable = (
        SyntheticItem("one", "brief", "US", "Alpha beta", ("A",), frozenset({"iri:a"}), "human"),
        SyntheticItem("two", "brief", "US", "Gamma", ("B",), frozenset({"iri:b"}), "human"),
    )
    nomatch = (SyntheticItem("none", "brief", "US", "Nothing here", provenance={"no_match": True}),)
    manifest = CorpusManifest(
        version=7,
        content_sha256="corpus-hash",
        nomatch_content_sha256="nomatch-hash",
        ontology_cache_sha256="ontology-hash",
        answer_rule_config_sha256=config.content_sha256(),
        item_counts={},
        non_lexical_fraction=0.0,
        non_lexical_floor=0.0,
        scoreable=True,
        seed=1,
        created="2026-08-16",
        manifest_path=tmp_path / "manifest.json",
    )
    return LoadedCorpus(manifest, scoreable, nomatch)


def _run(stack: str, lane: str, rows: dict[str, set[str]], *, py: str = "1.2.3") -> StackRun:
    return StackRun(
        stack=stack,
        lane=lane,
        folio_resolve_version="0.4.0" if lane == "incumbent" else "0.5.0",
        folio_python_version=py,
        config={"top_k": 3},
        rows={key: frozenset(value) for key, value in rows.items()},
        stages={key: {} for key in rows},
    )


def test_parse_join_and_metrics_math(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "synthetic-stack-run",
                        "stack": "folio-mapper",
                        "lane": "incumbent",
                        "folio_resolve_version": "0.4.0",
                        "folio_python_version": "1.2.3",
                        "config": {},
                    }
                ),
                json.dumps({"item_id": "one", "iris": ["iri:a", "iri:x"], "stages": {"ranked": 2}}),
                json.dumps({"item_id": "two", "iris": [], "stages": {}}),
                json.dumps({"item_id": "none", "iris": ["iri:x"], "stages": {}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run = parse_stack_output(path)
    metrics, item_f1 = score_stack(_corpus(tmp_path), run)
    assert metrics["tp"] == 1
    assert metrics["fp"] == 1
    assert metrics["fn"] == 1
    assert metrics["f1"] == 0.5
    assert metrics["nomatch_fp_rate"] == 1.0
    assert item_f1 == {"one": pytest.approx(2 / 3), "two": 0.0}


@pytest.mark.parametrize(
    ("candidate", "incumbent", "expected"),
    [
        ([1.0] * 20, [0.0] * 20, "win"),
        ([0.0] * 20, [1.0] * 20, "loss"),
        ([1.0, 0.0], [0.0, 1.0], "hold"),
    ],
)
def test_verdict_ci_classification(
    candidate: list[float], incumbent: list[float], expected: str
) -> None:
    verdict = classify_verdict(candidate, incumbent, n_resamples=500, seed=11)
    assert verdict["verdict"] == expected


def test_version_skew_aborts(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    runs = [
        _run(
            "folio-resolve",
            "candidate",
            {"one": {"iri:a"}, "two": {"iri:b"}, "none": set()},
            py="1",
        ),
        _run("folio-mapper", "incumbent", {"one": set(), "two": set(), "none": set()}, py="2"),
    ]
    with pytest.raises(VersionSkewError, match="folio-python version skew"):
        build_comparison(corpus, runs, AnswerRuleConfig())


@pytest.mark.parametrize("field", ["folio_resolve_version", "folio_python_version"])
def test_parse_rejects_empty_version_strings(tmp_path: Path, field: str) -> None:
    header = {
        "kind": "synthetic-stack-run", "stack": "s", "lane": "candidate",
        "folio_resolve_version": "1", "folio_python_version": "1", "config": {},
    }
    header[field] = "  "
    path = tmp_path / "empty.jsonl"
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")
    with pytest.raises(StackContractError, match="empty"):
        parse_stack_output(path)


def test_incumbent_assertion_logic() -> None:
    probe = {
        "folio_resolve_version": "0.4.0",
        "folio_resolve_file": "/venv/lib/python3.12/site-packages/folio_resolve/__init__.py",
        "folio_python_version": "1.2.3",
    }
    assert assert_incumbent_probe(probe, "0.4.0")["folio_python_version"] == "1.2.3"
    with pytest.raises(IncumbentInstallMismatch):
        assert_incumbent_probe({**probe, "folio_resolve_version": "0.3.0"}, "0.4.0")
    with pytest.raises(IncumbentInstallMismatch):
        assert_incumbent_probe(
            {**probe, "folio_resolve_file": "/checkout/src/folio_resolve/__init__.py"}, "0.4.0"
        )


def test_items_file_emits_shared_segments_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def extractor(text: str) -> tuple[str, ...]:
        calls.append(text)
        return (text.upper(),)

    out = emit_items_file(_corpus(tmp_path), tmp_path / "items.jsonl", extractor=extractor)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [row["segments"] for row in rows] == [["ALPHA BETA"], ["GAMMA"], ["NOTHING HERE"]]
    assert calls == ["Alpha beta", "Gamma", "Nothing here"]


def test_items_file_supports_explicit_resumable_shards(tmp_path: Path) -> None:
    out = emit_items_file(
        _corpus(tmp_path),
        tmp_path / "items.jsonl",
        item_ids=("two", "none"),
    )
    rows = [json.loads(line) for line in out.read_text().splitlines()]

    assert [row["item_id"] for row in rows] == ["two", "none"]


@pytest.mark.parametrize("item_ids", [("missing",), ("one", "one")])
def test_explicit_shard_rejects_unknown_or_duplicate_ids(
    tmp_path: Path, item_ids: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        emit_items_file(_corpus(tmp_path), tmp_path / "items.jsonl", item_ids=item_ids)


def test_explicit_shard_and_limit_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        emit_items_file(
            _corpus(tmp_path),
            tmp_path / "items.jsonl",
            limit=1,
            item_ids=("one",),
        )


def test_live_gate_can_emit_one_scoreable_item_without_nomatch_rows(tmp_path: Path) -> None:
    out = emit_items_file(
        _corpus(tmp_path),
        tmp_path / "live-gate-items.jsonl",
        limit=1,
        include_nomatch=False,
    )

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [row["item_id"] for row in rows] == ["one"]


def test_items_and_stage_snapshots_are_leak_gated(tmp_path: Path) -> None:
    salt = b"0123456789abcdef"
    manifest = build_manifest(["Alpha beta"], salt=salt, gold_version="g", gold_content_sha256="h")
    with pytest.raises(ComparisonError, match="items leak"):
        emit_items_file(_corpus(tmp_path), tmp_path / "items.jsonl", leak_manifest=manifest, salt=salt)
    run = _run("folio-resolve", "candidate", {"one": set()})
    run = replace(run, stages={"one": {"note": "Alpha beta"}})
    with pytest.raises(ComparisonError, match="stage snapshot leak"):
        write_stage_snapshots([run], tmp_path / "stages", leak_manifest=manifest, salt=salt)


def test_duplicate_snapshots_rejected_before_write(tmp_path: Path) -> None:
    run = _run("folio-resolve", "candidate", {"one": set()})
    with pytest.raises(StackContractError, match="duplicate"):
        write_stage_snapshots([run, run], tmp_path / "stages")
    assert not (tmp_path / "stages").exists()


def test_stage_snapshot_fingerprint_matches_the_written_full_snapshot(tmp_path: Path) -> None:
    run = replace(
        _run("folio-resolve", "candidate", {"one": {"iri:a"}}),
        stages={"one": {"ranked": ["iri:a"], "committed": ["iri:a"]}},
    )
    out_dir = tmp_path / "stages"
    fingerprints = write_stage_snapshots([run], out_dir)

    content = (out_dir / "folio-resolve" / "candidate" / "stages.json").read_bytes()
    assert fingerprints[run.key] == {
        "path": "folio-resolve/candidate/stages.json",
        "sha256": sha256(content).hexdigest(),
        "bytes": len(content),
    }


def test_git_repository_state_rejects_dirty_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = " M tracked.py\n?? local-note.txt\n"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["git", "rev-parse", "HEAD"], 0, "a" * 40 + "\n", ""
        ),
    )
    monkeypatch.setattr(comparison_module, "git_status_porcelain", lambda _root: status)

    with pytest.raises(ComparisonError, match="must be clean"):
        comparison_module._git_repository_state(tmp_path)


def test_git_repository_state_records_clean_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["git", "rev-parse", "HEAD"], 0, "a" * 40 + "\n", ""
        ),
    )
    monkeypatch.setattr(comparison_module, "git_status_porcelain", lambda _root: "")

    assert comparison_module._git_repository_state(tmp_path) == {
        "git_sha": "a" * 40,
        "initial_status_clean": True,
        "initial_status_entries": 0,
        "initial_status_sha256": sha256(b"").hexdigest(),
        "initial_status_format": "git status --porcelain",
    }


def test_materialized_items_fingerprint_matches_exact_bytes(tmp_path: Path) -> None:
    path = emit_items_file(_corpus(tmp_path), tmp_path / "items.jsonl")

    assert comparison_module._file_fingerprint(path, root=tmp_path) == {
        "path": "items.jsonl",
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "bytes": len(path.read_bytes()),
    }


def test_synthetic_comparison_guards_candidate_tree_after_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    items_path = tmp_path / "items.jsonl"
    exits: list[Path] = []

    class Guard:
        def __init__(self, root: Path) -> None:
            self.root = root

        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            exits.append(self.root)

    def fake_emit(*_args: object, **_kwargs: object) -> Path:
        items_path.write_text('{"item_id":"one"}\n', encoding="utf-8")
        return items_path

    monkeypatch.setattr(comparison_module, "FOLIO_RESOLVE_ROOT", candidate_root)
    monkeypatch.setattr(comparison_module, "_git_repository_state", lambda _root: {})
    monkeypatch.setattr(comparison_module, "clean_tree_guard", Guard)
    monkeypatch.setattr(comparison_module, "emit_items_file", fake_emit)
    monkeypatch.setattr(
        comparison_module,
        "run_local_stack",
        lambda *_args, **_kwargs: _run("folio-resolve", "candidate", {"one": {"iri:a"}}),
    )
    monkeypatch.setattr(comparison_module, "write_stage_snapshots", lambda *_a, **_k: {})
    monkeypatch.setattr(comparison_module, "build_comparison", lambda *_a, **_k: {"ok": True})

    result = comparison_module.run_synthetic_comparison(
        _corpus(tmp_path),
        adapter=SimpleNamespace(phrase_extractor=lambda _text: ()),
        config=AnswerRuleConfig(),
        consumers=(),
        items_path=items_path,
        row_snapshot_dir=tmp_path / "snapshots",
        leak_manifest=SimpleNamespace(),
        salt=b"salt",
        limit=1,
        include_nomatch=False,
    )

    assert result == {"ok": True}
    assert exits == [candidate_root]


def test_synthetic_comparison_rejects_items_changed_by_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items_path = tmp_path / "items.jsonl"

    def fake_emit(*_args: object, **_kwargs: object) -> Path:
        items_path.write_text('{"item_id":"one"}\n', encoding="utf-8")
        return items_path

    def mutate_items(*_args: object, **_kwargs: object) -> StackRun:
        items_path.write_text('{"item_id":"changed"}\n', encoding="utf-8")
        return _run("folio-mapper", "incumbent", {"one": set()})

    monkeypatch.setattr(comparison_module, "_git_repository_state", lambda _root: {})
    monkeypatch.setattr(comparison_module, "clean_tree_guard", lambda _root: nullcontext())
    monkeypatch.setattr(comparison_module, "emit_items_file", fake_emit)
    monkeypatch.setattr(comparison_module, "run_consumer_stack", mutate_items)

    with pytest.raises(ComparisonError, match="changed during folio-mapper execution"):
        comparison_module.run_synthetic_comparison(
            _corpus(tmp_path),
            adapter=SimpleNamespace(phrase_extractor=lambda _text: ()),
            config=AnswerRuleConfig(),
            consumers=(ConsumerSpec("folio-mapper", tmp_path, tmp_path / "python"),),
            items_path=items_path,
            row_snapshot_dir=tmp_path / "snapshots",
            leak_manifest=SimpleNamespace(),
            salt=b"salt",
            limit=1,
            include_nomatch=False,
        )


def test_write_comparison_leakchecks_every_string(tmp_path: Path) -> None:
    salt = b"0123456789abcdef"
    manifest = build_manifest(
        ["Secret Surface"], salt=salt, gold_version="g", gold_content_sha256="h"
    )
    clean = {"kind": "synthetic_comparison", "note": "safe"}
    assert write_comparison(
        tmp_path / "clean.json", clean, leak_manifest=manifest, salt=salt
    ).exists()
    with pytest.raises(Exception, match="leak"):
        write_comparison(
            tmp_path / "bad.json",
            {**clean, "note": "Secret Surface"},
            leak_manifest=manifest,
            salt=salt,
        )


def _comparison_public_metadata_payload() -> dict[str, object]:
    argv = ["safe"] * 15
    argv[2] = "run_synthetic_comparison"
    argv[3] = "--corpus-manifest"
    argv[4] = "eval/synthetic/corpus_v1.manifest.json"
    argv[5] = "--config"
    argv[6] = "eval/synthetic/answer_rule_config_synthetic_v1.json"
    argv[13] = "--leak-manifest"
    argv[14] = "eval/synthetic/firm-surface-manifest-v1.json"
    rationale = "synthetic lane v1: top_k sized to corpus gold density (uncalibrated)"
    return {
        "kind": "synthetic_comparison",
        "provenance": {
            "comparison_invocation": {"argv": argv},
            "config_selection": {
                "answer_rule_config_sha256": (
                    "ac413db665602cdde841a7e7590adc4eba02cded82266df5e763d7be7dd87c03"
                ),
                "answer_rule_config": {"rationale": rationale},
            },
        },
        "stacks": {
            "folio-enrich:incumbent": {
                "invocation": {
                    "argv": ["python", "/repos/enrich/backend/eval/synthetic_runner.py"],
                    "working_directory": "/repos/enrich",
                }
            },
            "folio-mapper:incumbent": {
                "invocation": {
                    "argv": ["python", "/repos/mapper/backend/scripts/synthetic_runner.py"],
                    "working_directory": "/repos/mapper",
                }
            },
            "folio-resolve:candidate": {"config": {"rationale": rationale}},
        },
    }


def test_versioned_public_metadata_exempts_only_exact_comparison_paths() -> None:
    salt = b"0123456789abcdef"
    payload = _comparison_public_metadata_payload()
    public_values = [
        "synthetic_comparison",
        "run_synthetic_comparison",
        "eval/synthetic/corpus_v1.manifest.json",
        "eval/synthetic/answer_rule_config_synthetic_v1.json",
        "eval/synthetic/firm-surface-manifest-v1.json",
        "synthetic lane v1: top_k sized to corpus gold density (uncalibrated)",
        "/repos/enrich/backend/eval/synthetic_runner.py",
        "/repos/mapper/backend/scripts/synthetic_runner.py",
        "Secret Surface",
    ]
    manifest = build_manifest(public_values, salt=salt, gold_version="g", gold_content_sha256="h")
    metadata = load_public_comparison_metadata(DEFAULT_COMPARISON_PUBLIC_METADATA_PATH)

    preflight_comparison_publication(payload, manifest, salt, public_metadata=metadata)

    reordered = deepcopy(payload)
    reordered["provenance"]["comparison_invocation"]["argv"] = [
        "python",
        "eval/run_downstream.py",
        "run_synthetic_comparison",
        "--limit",
        "1",
        "--leak-manifest",
        "eval/synthetic/firm-surface-manifest-v1.json",
        "--config",
        "eval/synthetic/answer_rule_config_synthetic_v1.json",
        "--corpus-manifest",
        "eval/synthetic/corpus_v1.manifest.json",
    ]
    preflight_comparison_publication(reordered, manifest, salt, public_metadata=metadata)

    equals_form = deepcopy(payload)
    equals_form["provenance"]["comparison_invocation"]["argv"] = [
        "python",
        "eval/run_downstream.py",
        "run_synthetic_comparison",
        "--corpus-manifest=eval/synthetic/corpus_v1.manifest.json",
        "--config=eval/synthetic/answer_rule_config_synthetic_v1.json",
        "--leak-manifest=eval/synthetic/firm-surface-manifest-v1.json",
        "--limit",
        "1",
    ]
    preflight_comparison_publication(equals_form, manifest, salt, public_metadata=metadata)

    duplicate_option = deepcopy(reordered)
    duplicate_option["provenance"]["comparison_invocation"]["argv"].extend(
        ["--config", "eval/synthetic/answer_rule_config_synthetic_v1.json"]
    )
    with pytest.raises(ComparisonError, match="missing or duplicated"):
        preflight_comparison_publication(
            duplicate_option, manifest, salt, public_metadata=metadata
        )

    mixed_duplicate_option = deepcopy(equals_form)
    mixed_duplicate_option["provenance"]["comparison_invocation"]["argv"].extend(
        ["--config", "eval/synthetic/answer_rule_config_synthetic_v1.json"]
    )
    with pytest.raises(ComparisonError, match="missing or duplicated"):
        preflight_comparison_publication(
            mixed_duplicate_option, manifest, salt, public_metadata=metadata
        )

    mapper_only = deepcopy(payload)
    del mapper_only["stacks"]["folio-enrich:incumbent"]
    preflight_comparison_publication(mapper_only, manifest, salt, public_metadata=metadata)

    enrich_only = deepcopy(payload)
    del enrich_only["stacks"]["folio-mapper:incumbent"]
    preflight_comparison_publication(enrich_only, manifest, salt, public_metadata=metadata)

    with pytest.raises(ComparisonError, match="collisions=1"):
        preflight_comparison_publication(
            {**payload, "note": "Secret Surface"},
            manifest,
            salt,
            public_metadata=metadata,
        )
    with pytest.raises(ComparisonError, match="collisions"):
        preflight_comparison_publication(
            {**payload, "note": {"escaped": "Secret\nSurface"}},
            manifest,
            salt,
            public_metadata=metadata,
        )


def test_comparison_public_metadata_rejects_value_drift() -> None:
    salt = b"0123456789abcdef"
    payload = _comparison_public_metadata_payload()
    payload["kind"] = "changed"
    metadata = load_public_comparison_metadata(DEFAULT_COMPARISON_PUBLIC_METADATA_PATH)
    manifest = build_manifest(
        ["unrelated protected value"],
        salt=salt,
        gold_version="g",
        gold_content_sha256="h",
    )

    with pytest.raises(ComparisonError, match="value mismatch"):
        preflight_comparison_publication(payload, manifest, salt, public_metadata=metadata)


def test_build_comparison_records_pilot_iri_sets_and_reproducibility_provenance(
    tmp_path: Path,
) -> None:
    corpus = _corpus(tmp_path)
    candidate = replace(
        _run("folio-resolve", "candidate", {"one": {"iri:a"}}),
        invocation=(
            "folio_eval.comparison.run_local_stack",
            "--items",
            "$COMPARISON_ITEMS",
        ),
        invocation_working_directory="$FOLIO_RESOLVE_REPOSITORY_ROOT",
        repository={
            "git_sha": "a" * 40,
            "initial_status_clean": True,
            "initial_status_entries": 0,
            "initial_status_sha256": sha256(b"").hexdigest(),
        },
        stages={"one": {"ranked": ["iri:a", "iri:x"], "committed": ["iri:a"]}},
    )
    incumbent = replace(
        _run("folio-mapper", "incumbent", {"one": set()}),
        config=dict(comparison_module.MAPPER_DETERMINISTIC_CONFIG),
        invocation=(
            ".venv/bin/python",
            "backend/scripts/synthetic_runner.py",
            "--items",
            "$COMPARISON_ITEMS",
            "--out",
            "$STACK_OUTPUT",
            "--lane",
            "deterministic",
        ),
        repository={
            "git_sha": "c" * 40,
            "initial_status_clean": True,
            "initial_status_entries": 0,
            "initial_status_sha256": sha256(b"").hexdigest(),
        },
        stages={
            "one": {
                "stage1_filter": [],
                "embedding_rerank": [],
                "committed": [],
            }
        },
    )
    runs = [
        candidate,
        incumbent,
    ]
    result = build_comparison(
        corpus,
        runs,
        AnswerRuleConfig(),
        limit=1,
        include_nomatch=False,
        n_resamples=100,
        comparison_invocation=(
            "python",
            "eval/run_downstream.py",
            "run_synthetic_comparison",
            "--limit",
            "1",
            "--scoreable-only",
        ),
        stage_snapshot_files={
            "folio-resolve:candidate": {
                "path": "folio-resolve/candidate/stages.json",
                "sha256": "d" * 64,
                "bytes": 123,
            }
        },
        items_file={"path": "items.jsonl", "sha256": "e" * 64, "bytes": 321},
    )
    assert result["pilot"] is True
    assert result["scoreable_items"] == 1
    assert result["nomatch_items"] == 0
    assert result["stacks"]["folio-resolve:candidate"]["items"] == {"one": ["iri:a"]}
    assert result["stacks"]["folio-resolve:candidate"]["invocation"]["argv"][0] == (
        "folio_eval.comparison.run_local_stack"
    )
    assert result["stacks"]["folio-resolve:candidate"]["invocation"]["kind"] == "in_process"
    assert result["stacks"]["folio-resolve:candidate"]["repository"]["git_sha"] == "a" * 40
    assert result["stacks"]["folio-resolve:candidate"]["stage_snapshot"]["by_item"] == {
        "one": {"committed": ["iri:a"], "ranked": ["iri:a", "iri:x"]}
    }
    assert result["stacks"]["folio-resolve:candidate"]["stage_snapshot"]["file"] == {
        "path": "folio-resolve/candidate/stages.json",
        "sha256": "d" * 64,
        "bytes": 123,
    }
    provenance = result["provenance"]
    assert provenance["comparison_invocation"] == {
        "kind": "equivalent",
        "argv": [
            "python",
            "eval/run_downstream.py",
            "run_synthetic_comparison",
            "--limit",
            "1",
            "--scoreable-only",
        ],
        "working_directory": "$FOLIO_RESOLVE_REPOSITORY_ROOT",
    }
    assert provenance["cohort_selection"] == {
        "rule": "corpus_manifest_order_prefix",
        "scoreable_limit": 1,
        "include_nomatch": False,
        "scoreable_item_ids": ["one"],
        "nomatch_item_ids": [],
    }
    assert provenance["config_selection"]["answer_rule_config_sha256"] == (
        AnswerRuleConfig().content_sha256()
    )
    assert provenance["committed_set_rule"]["metric"] == "strict_item_level_iri_set"
    assert provenance["gold_by_item"] == {"one": ["iri:a"]}
    assert provenance["items_file"] == {
        "path": "items.jsonl",
        "sha256": "e" * 64,
        "bytes": 321,
    }
    assert result["verdicts"]["folio-mapper"]["verdict"] == "win"


def test_local_stack_emits_attribution_ready_candidate_stages(tmp_path: Path) -> None:
    candidate = MatchCandidate(
        iri="iri:a",
        label="A",
        score=88.0,
        extraction_path="multi_strategy_recall",
        gated=True,
    )
    adapted = AdapterResult(
        candidates=(candidate,),
        raw_candidate_count=3,
        suppression_counters={"blocklist": 1, "score_floor": 1},
        traces=(
            CandidateTrace(
                "iri:a", "A", "", "multi_strategy_recall", "Alpha beta", 90.0, 88.0,
                "survived", True, "short label demotion",
            ),
            CandidateTrace(
                "iri:b", "B", "", "aho_corasick", "B", 100.0, None,
                "blocklist", False, "alias_blocklist",
            ),
            CandidateTrace(
                "iri:c", "C", "", "multi_strategy_recall", "beta", 40.0, 40.0,
                "score_floor", False, "",
            ),
        ),
    )
    adapter = SimpleNamespace(
        phrase_extractor=lambda _text: ("Alpha beta",),
        adapt=lambda _text, *, segments: adapted,
    )

    run = run_local_stack(
        _corpus(tmp_path),
        adapter,
        AnswerRuleConfig(),
        limit=1,
        include_nomatch=False,
        extractor=lambda _text: ("Alpha beta",),
    )

    assert run.stages["one"] == {
        "segments": ["Alpha beta"],
        "counts": {
            "pre_gate_unique": 3,
            "survived": 1,
            "suppressed": {"blocklist": 1, "score_floor": 1},
            "committed": 1,
        },
        "candidates": [
            {
                "iri": "iri:a",
                "branch": "",
                "extraction_path": "multi_strategy_recall",
                "pre_gate_score": 90.0,
                "post_gate_score": 88.0,
                "gate_disposition": "survived",
                "gated": True,
                "gate_reason": "short label demotion",
                "rank": 1,
                "probability": 0.88,
                "commit_disposition": "committed",
            },
            {
                "iri": "iri:b",
                "branch": "",
                "extraction_path": "aho_corasick",
                "pre_gate_score": 100.0,
                "post_gate_score": None,
                "gate_disposition": "blocklist",
                "gated": False,
                "gate_reason": "alias_blocklist",
                "rank": None,
                "probability": None,
                "commit_disposition": "suppressed",
            },
            {
                "iri": "iri:c",
                "branch": "",
                "extraction_path": "multi_strategy_recall",
                "pre_gate_score": 40.0,
                "post_gate_score": 40.0,
                "gate_disposition": "score_floor",
                "gated": False,
                "gate_reason": "",
                "rank": None,
                "probability": None,
                "commit_disposition": "suppressed",
            },
        ],
        "ranked_iris": ["iri:a"],
        "committed_iris": ["iri:a"],
    }


def test_mapper_fallback_config_is_rejected() -> None:
    run = replace(
        _run("folio-mapper", "incumbent", {"one": set()}),
        config={
            "threshold": 0.3,
            "max_per_branch": 10,
            "rerank_top_k": 20,
            "commit_top_n": 10,
            "keyword_weight": 0.6,
            "embedding_weight": 0.4,
            "embedding_rerank": "unavailable",
            "llm_on": False,
        },
    )

    with pytest.raises(StackContractError, match="embedding_rerank"):
        comparison_module._assert_consumer_config(run)


def test_build_comparison_config_hash_mismatch_raises(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    runs = [_run("folio-resolve", "candidate", {"one": {"iri:a"}, "two": {"iri:b"}, "none": set()})]
    with pytest.raises(Exception, match="answer_rule_config_sha256"):
        build_comparison(corpus, runs, AnswerRuleConfig(threshold=0.9))


def test_committed_items_include_nomatch_predictions_for_metric_recomputation(
    tmp_path: Path,
) -> None:
    corpus = _corpus(tmp_path)
    run = _run(
        "folio-resolve",
        "candidate",
        {"one": {"iri:a"}, "two": {"iri:b"}, "none": {"iri:false-positive"}},
    )

    result = build_comparison(corpus, [run], AnswerRuleConfig())
    stack = result["stacks"]["folio-resolve:candidate"]

    assert stack["items"]["none"] == ["iri:false-positive"]
    assert stack["metrics"]["nomatch_false_positives"] == 1


def test_missing_item_is_stack_contract_error(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    run = _run("folio-resolve", "candidate", {"one": {"iri:a"}, "none": set()})
    with pytest.raises(StackContractError, match="omitted scoreable item"):
        build_comparison(corpus, [run], AnswerRuleConfig())


def test_consumer_runner_translates_deterministic_lane_to_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        out_path = Path(command[command.index("--out") + 1])
        out_path.write_text(
            json.dumps(
                {
                    "kind": "synthetic-stack-run",
                    "stack": "folio-mapper",
                    "lane": "deterministic",
                    "folio_resolve_version": "0.4.0",
                    "folio_python_version": "1.2.3",
                    "config": dict(comparison_module.MAPPER_DETERMINISTIC_CONFIG),
                }
            )
            + "\n"
            + json.dumps(
                {
                    "item_id": "one",
                    "iris": ["https://folio.openlegalstandard.org/Ra"],
                    "stages": {
                        "stage1_filter": ["https://folio.openlegalstandard.org/Ra"],
                        "embedding_rerank": ["https://folio.openlegalstandard.org/Ra"],
                        "committed": ["https://folio.openlegalstandard.org/Ra"],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(comparison_module, "prepare_incumbent", lambda *_args: {})
    monkeypatch.setattr(comparison_module, "clean_tree_guard", lambda _root: nullcontext())
    monkeypatch.setattr(
        comparison_module,
        "_git_repository_state",
        lambda _root: {"git_sha": "a" * 40, "initial_status_clean": True},
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    base_python = tmp_path / "base-python"
    base_python.touch()
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)
    spec = ConsumerSpec("folio-mapper", tmp_path, venv_python)
    items_path = tmp_path / "items.jsonl"
    items_path.write_text(json.dumps({"item_id": "one"}) + "\n", encoding="utf-8")

    run = run_consumer_stack(spec, items_path)

    assert run.lane == "incumbent"
    assert run.invocation_working_directory == str(tmp_path.resolve())
    assert run.repository["git_sha"] == "a" * 40
    assert commands[0][0] == str(venv_python)
    assert commands[0][commands[0].index("--lane") + 1] == "deterministic"


def test_consumer_rows_reject_bare_hashes_before_scoring() -> None:
    run = StackRun(
        stack="folio-mapper",
        lane="incumbent",
        folio_resolve_version="0.4.0",
        folio_python_version="0.3.6",
        config=dict(comparison_module.MAPPER_DETERMINISTIC_CONFIG),
        rows={"one": frozenset({"Rbare"})},
        stages={
            "one": {
                "stage1_filter": ["Rbare"],
                "embedding_rerank": ["Rbare"],
                "committed": ["Rbare"],
            }
        },
    )

    with pytest.raises(StackContractError, match="non-canonical FOLIO IRI"):
        comparison_module._assert_consumer_rows(run, ["one"])


def test_consumer_runner_translates_timeout_to_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def time_out(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 1.5)

    monkeypatch.setattr(comparison_module, "prepare_incumbent", lambda *_args: {})
    monkeypatch.setattr(comparison_module, "clean_tree_guard", lambda _root: nullcontext())
    monkeypatch.setattr(
        comparison_module,
        "_git_repository_state",
        lambda _root: {"git_sha": "a" * 40, "initial_status_clean": True},
    )
    monkeypatch.setattr(subprocess, "run", time_out)
    spec = ConsumerSpec("folio-enrich", tmp_path, tmp_path / "python")
    items_path = tmp_path / "items.jsonl"
    items_path.write_text(json.dumps({"item_id": "one"}) + "\n", encoding="utf-8")

    with pytest.raises(ConsumerRunError, match="timed out after 2s"):
        run_consumer_stack(spec, items_path, timeout=1.5)
