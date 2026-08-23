"""Power-loss-safe U10 comparison pilot checkpoints."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import folio_eval.comparison_pilot as pilot_module
import pytest
from folio_eval.answer_rule import AnswerRuleConfig
from folio_eval.comparison_pilot import (
    PilotCheckpointError,
    _checkpoint_manifest,
    _create_or_validate_manifest,
    _load_shard,
    _merge_stack_runs,
    _pilot_ids,
    _run_shard,
)
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem


def _corpus(tmp_path: Path) -> LoadedCorpus:
    config = AnswerRuleConfig()
    manifest = CorpusManifest(
        version=1,
        content_sha256="corpus",
        nomatch_content_sha256="nomatch",
        ontology_cache_sha256="ontology",
        answer_rule_config_sha256=config.content_sha256(),
        item_counts={},
        non_lexical_fraction=0.0,
        non_lexical_floor=0.0,
        scoreable=True,
        seed=1,
        created="2026-08-23",
        manifest_path=tmp_path / "manifest.json",
    )
    return LoadedCorpus(
        manifest,
        (
            SyntheticItem("one", "brief", "US", "One", (), frozenset({"iri:one"}), "human"),
            SyntheticItem("two", "brief", "US", "Two", (), frozenset({"iri:two"}), "human"),
        ),
        (SyntheticItem("none", "brief", "US", "None", provenance={"no_match": True}),),
    )


def _stack(stack: str, lane: str, item_id: str) -> dict[str, object]:
    return {
        "stack": stack,
        "lane": lane,
        "versions": {"folio-resolve": "0.4.0", "folio-python": "0.3.6"},
        "config": {"top_k": 3},
        "invocation": {
            "argv": ["python", f"/repo/{stack}/runner.py"],
            "working_directory": f"/repo/{stack}",
        },
        "repository": {"git_sha": "a" * 40, "initial_status_clean": True},
        "items": {item_id: [f"iri:{item_id}"]},
        "stage_snapshot": {"by_item": {item_id: {"committed": [f"iri:{item_id}"]}}},
    }


def _shard(item_id: str, *, nomatch: bool = False) -> dict[str, object]:
    return {
        "kind": "synthetic_comparison",
        "run_kind": "shard",
        "provenance": {
            "cohort_selection": {
                "scoreable_item_ids": [] if nomatch else [item_id],
                "nomatch_item_ids": [item_id] if nomatch else [],
            }
        },
        "stacks": {
            "folio-enrich:incumbent": _stack("folio-enrich", "incumbent", item_id),
            "folio-mapper:incumbent": _stack("folio-mapper", "incumbent", item_id),
            "folio-resolve:candidate": _stack("folio-resolve", "candidate", item_id),
        },
    }


def test_pilot_ids_are_fixed_scoreable_prefix_plus_all_nomatch(tmp_path: Path) -> None:
    assert _pilot_ids(_corpus(tmp_path), 1) == ("one", "none")


def test_checkpoint_manifest_is_create_once_and_fingerprint_bound(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    expected = _checkpoint_manifest(fingerprint={"git": "abc"}, item_ids=("one", "none"))

    _create_or_validate_manifest(path, expected)
    _create_or_validate_manifest(path, expected)

    with pytest.raises(PilotCheckpointError, match="fingerprint"):
        _create_or_validate_manifest(
            path,
            _checkpoint_manifest(fingerprint={"git": "changed"}, item_ids=("one", "none")),
        )


def test_load_shard_rejects_item_or_stack_drift(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(_shard("one")), encoding="utf-8")
    assert _load_shard(path, "one")["run_kind"] == "shard"

    with pytest.raises(PilotCheckpointError, match="item mismatch"):
        _load_shard(path, "two")

    payload = _shard("one")
    del payload["stacks"]["folio-mapper:incumbent"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PilotCheckpointError, match="stack set"):
        _load_shard(path, "one")


def test_load_shard_is_bound_to_checkpoint_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    payload = _shard("one")
    payload["corpus"] = {"content_sha256": "corpus", "nomatch_content_sha256": "nomatch"}
    payload["folio_python_version"] = "0.3.6"
    payload["provenance"]["config_selection"] = {"answer_rule_config_sha256": "config"}
    fingerprint = {
        "corpus_content_sha256": "corpus",
        "nomatch_content_sha256": "nomatch",
        "answer_rule_config_sha256": "config",
        "folio_python_version": "0.3.6",
        "folio_resolve_version": "0.4.0",
        "candidate_repository": payload["stacks"]["folio-resolve:candidate"]["repository"],
        "enrich_repository": payload["stacks"]["folio-enrich:incumbent"]["repository"],
        "mapper_repository": payload["stacks"]["folio-mapper:incumbent"]["repository"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert _load_shard(path, "one", fingerprint)["run_kind"] == "shard"
    fingerprint["answer_rule_config_sha256"] = "changed"
    with pytest.raises(PilotCheckpointError, match="answer-rule config"):
        _load_shard(path, "one", fingerprint)


def test_merge_stack_runs_preserves_every_item_and_rejects_static_drift() -> None:
    shards = [_shard("one"), _shard("none", nomatch=True)]
    runs = _merge_stack_runs(shards)

    assert len(runs) == 3
    assert all(set(run.rows) == {"one", "none"} for run in runs)
    assert all(set(run.stages) == {"one", "none"} for run in runs)

    drifted = deepcopy(shards)
    drifted[1]["stacks"]["folio-mapper:incumbent"]["repository"] = {"git_sha": "b" * 40}
    with pytest.raises(PilotCheckpointError, match="repository drifted"):
        _merge_stack_runs(drifted)


def test_run_shard_uses_one_explicit_item_and_suppresses_large_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pilot_module.subprocess, "run", fake_run)
    monkeypatch.setattr(pilot_module, "_load_shard", lambda *_args, **_kwargs: {})
    args = SimpleNamespace(
        checkpoint_dir=tmp_path / "checkpoint",
        corpus_manifest=Path("eval/synthetic/corpus_v1.manifest.json"),
        config=Path("eval/synthetic/answer_rule_config_synthetic_v1.json"),
        leak_manifest=Path("eval/synthetic/firm-surface-manifest-v1.json"),
        salt_file=Path("eval/data/leakcheck-salt"),
        public_metadata=Path("eval/synthetic/public_comparison_metadata_v1.json"),
        mapper_root=Path("/repos/mapper"),
        enrich_root=Path("/repos/enrich"),
    )

    _run_shard(args, "one", {})

    command = observed["command"]
    assert command[command.index("--item-id") + 1] == "one"
    assert observed["stdout"] is pilot_module.subprocess.DEVNULL
    assert observed["cwd"] == pilot_module.FOLIO_RESOLVE_ROOT
