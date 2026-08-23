from __future__ import annotations

import hashlib
import json
import math
import subprocess
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import folio_eval.synthetic_checkpoint as checkpoint_module
import pytest
from folio_eval.answer_rule import AnswerRuleConfig
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem
from folio_eval.synthetic_checkpoint import (
    CheckpointError,
    CheckpointFingerprint,
    SyntheticCheckpointStore,
    build_checkpoint_fingerprint,
    checkpoint_item_key,
    shard_for_item,
)
from folio_eval.synthetic_score import (
    DEPTH_PROBE_MAX,
    AdapterResult,
    DocumentAdapter,
    SyntheticScoreResult,
    build_synthetic_report,
    depth_probe,
    score_corpus,
    score_corpus_checkpointed,
)

from folio_resolve import Concept, InMemoryOntology


def _ontology() -> InMemoryOntology:
    return InMemoryOntology(
        [
            Concept(iri="R-arb", label="Arbitration Rules"),
            Concept(iri="R-proof", label="Burden of Proof"),
        ]
    )


def _corpus(config: AnswerRuleConfig) -> LoadedCorpus:
    return LoadedCorpus(
        manifest=CorpusManifest(
            version=1,
            content_sha256="a" * 64,
            nomatch_content_sha256="b" * 64,
            ontology_cache_sha256="c" * 64,
            answer_rule_config_sha256=config.content_sha256(),
            item_counts={"nomatch": 1, "scoreable": 2},
            non_lexical_fraction=0.5,
            non_lexical_floor=0.3,
            scoreable=True,
            seed=7,
            created="2026-08-16T00:00:00Z",
            manifest_path=Path("corpus_v1.manifest.json"),
        ),
        corpus_items=(
            SyntheticItem(
                item_id="score-1",
                doc_type="motion",
                jurisdiction="US",
                text="The motion invokes Arbitration Rules and discusses the Burden of Proof.",
                gold_iris=frozenset({"R-arb"}),
                verification="deterministic",
            ),
            SyntheticItem(
                item_id="score-2",
                doc_type="brief",
                jurisdiction="US",
                text="The brief allocates the Burden of Proof.",
                gold_iris=frozenset({"R-proof"}),
                verification="human",
            ),
        ),
        nomatch_items=(
            SyntheticItem(
                item_id="no-1",
                doc_type="contract",
                jurisdiction="US",
                text="An ordinary signature page follows.",
                provenance={"no_match": True},
            ),
        ),
    )


def _fingerprint(config: AnswerRuleConfig) -> CheckpointFingerprint:
    return CheckpointFingerprint(
        corpus_content_sha256="a" * 64,
        nomatch_content_sha256="b" * 64,
        answer_rule_config_sha256=config.content_sha256(),
        ontology_cache_sha256="c" * 64,
        git_head="d" * 40,
        python_hash_seed="0",
        python_version="3.11.0",
        folio_python_version="0.3.6",
        folio_resolve_version="0.4.0",
        lockfile_sha256="e" * 64,
    )


def _report(
    result: SyntheticScoreResult, corpus: LoadedCorpus, config: AnswerRuleConfig
) -> dict[str, object]:
    return build_synthetic_report(
        result,
        corpus=corpus,
        config=config,
        label="synthetic-baseline-v1",
        ontology_pin=corpus.manifest.ontology_cache_sha256,
        depth_probe_result=depth_probe(result.run, config, depths=(1,)),
        determinism_selftest={"matched": True},
    )


class CountingAdapter(DocumentAdapter):
    calls: int = 0

    def adapt(self, passage: str, *, segments: Sequence[str] | None = None) -> AdapterResult:
        self.calls += 1
        return super().adapt(passage, segments=segments)


CheckpointPayload = dict[str, Any]


def _reverse_candidates(payload: CheckpointPayload) -> None:
    payload["candidates"] = list(reversed(payload["candidates"]))


def _duplicate_candidate(payload: CheckpointPayload) -> None:
    payload["candidates"].append(payload["candidates"][0])
    payload["survivor_count"] += 1
    payload["raw_candidate_count"] += 1


def _nonfinite_score(payload: CheckpointPayload) -> None:
    payload["candidates"][0]["score"] = "NaN"


def _negative_raw_count(payload: CheckpointPayload) -> None:
    payload["raw_candidate_count"] = -1


def _remove_suppression_category(payload: CheckpointPayload) -> None:
    payload["suppression_counters"].pop("blocklist")


def _break_count_invariant(payload: CheckpointPayload) -> None:
    payload["raw_candidate_count"] += 1


def _change_valid_iri(payload: CheckpointPayload) -> None:
    payload["candidates"][0]["iri"] = "R-corrupted"


def _change_valid_finite_score(payload: CheckpointPayload) -> None:
    payload["candidates"][-1]["score"] = float(payload["candidates"][-1]["score"]) - 0.25


def _refresh_payload_digest(payload: CheckpointPayload) -> None:
    unsigned = dict(payload)
    unsigned.pop("payload_sha256", None)
    payload["payload_sha256"] = checkpoint_module._payload_sha256(unsigned)


def _clean_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".synthetic-checkpoints/\n", encoding="utf-8")
    (repo / "scorer.py").write_text("SCORING_VERSION = 1\n", encoding="utf-8")
    (repo / "uv.lock").write_text("locked dependencies\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "checkpoint@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Checkpoint Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "test checkpoint fingerprint"],
        cwd=repo,
        check=True,
    )
    return repo


def test_checkpointed_score_is_report_identical_and_reuse_skips_adapter(tmp_path: Path) -> None:
    config = AnswerRuleConfig(threshold=0.5, top_k=5)
    corpus = _corpus(config)
    ontology = _ontology()
    direct = score_corpus(corpus, ontology, config)
    adapter = CountingAdapter(ontology)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )

    resumed = score_corpus_checkpointed(
        corpus,
        ontology,
        config,
        store=store,
        shard_index=0,
        adapter=adapter,
    )
    assert resumed is not None
    assert adapter.calls == 3
    assert _report(resumed, corpus, config) == _report(direct, corpus, config)

    adapter.calls = 0
    reused = score_corpus_checkpointed(
        corpus,
        ontology,
        config,
        store=store,
        shard_index=0,
        adapter=adapter,
    )
    assert reused is not None
    assert adapter.calls == 0
    assert _report(reused, corpus, config) == _report(direct, corpus, config)

    def fail_if_adapter_is_built() -> DocumentAdapter:
        raise AssertionError("complete checkpoint must not construct the adapter")

    finalized = score_corpus_checkpointed(
        corpus,
        None,
        config,
        store=store,
        finalize_only=True,
        adapter_factory=fail_if_adapter_is_built,
    )
    assert finalized is not None
    assert _report(finalized, corpus, config) == _report(direct, corpus, config)


def test_missing_checkpoint_constructs_adapter_factory_once(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    constructed: list[CountingAdapter] = []

    def build_adapter() -> CountingAdapter:
        adapter = CountingAdapter(_ontology())
        constructed.append(adapter)
        return adapter

    result = score_corpus_checkpointed(
        corpus,
        None,
        config,
        store=store,
        adapter_factory=build_adapter,
    )

    assert result is not None
    assert len(constructed) == 1
    assert constructed[0].calls == 3


def test_shards_are_disjoint_exhaustive_and_finalize_only_after_all(tmp_path: Path) -> None:
    config = AnswerRuleConfig(threshold=0.5, top_k=5)
    corpus = _corpus(config)
    ontology = _ontology()
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=2,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    keys = {
        checkpoint_item_key("scoreable", "score-1"),
        checkpoint_item_key("scoreable", "score-2"),
        checkpoint_item_key("nomatch", "no-1"),
    }
    shard_keys = [{key for key in keys if shard_for_item(key, 2) == index} for index in range(2)]
    assert shard_keys[0].isdisjoint(shard_keys[1])
    assert shard_keys[0] | shard_keys[1] == keys

    assert score_corpus_checkpointed(corpus, ontology, config, store=store, shard_index=0) is None
    with pytest.raises(CheckpointError, match="incomplete"):
        score_corpus_checkpointed(corpus, ontology, config, store=store, finalize_only=True)

    completed = score_corpus_checkpointed(corpus, ontology, config, store=store, shard_index=1)
    assert completed is not None
    direct = score_corpus(corpus, ontology, config)
    assert _report(completed, corpus, config) == _report(direct, corpus, config)


def test_checkpoint_payload_omits_passages_gold_and_candidate_surfaces(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    score_corpus_checkpointed(corpus, _ontology(), config, store=store, shard_index=0)

    serialized = "\n".join(path.read_text(encoding="utf-8") for path in store.item_paths())
    for forbidden in (
        "Arbitration Rules",
        "Burden of Proof",
        "signature page",
        "gold",
        "surface",
        '"label":',
        '"trace":',
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "mutator, message",
    [
        (_reverse_candidates, "canonical"),
        (_duplicate_candidate, "duplicate"),
        (_nonfinite_score, "finite"),
        (_negative_raw_count, "nonnegative"),
        (_remove_suppression_category, "categories"),
        (_break_count_invariant, "count invariant"),
    ],
)
def test_corrupt_checkpoint_fails_closed(
    tmp_path: Path, mutator: Callable[[CheckpointPayload], None], message: str
) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    score_corpus_checkpointed(corpus, _ontology(), config, store=store, shard_index=0)
    path = next(
        path for path in store.item_paths() if len(json.loads(path.read_text())["candidates"]) > 1
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutator(payload)
    _refresh_payload_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointError, match=message):
        score_corpus_checkpointed(corpus, _ontology(), config, store=store, finalize_only=True)


@pytest.mark.parametrize("score", [math.nan, math.inf, -math.inf])
def test_actual_nonfinite_checkpoint_score_fails_closed(tmp_path: Path, score: float) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    score_corpus_checkpointed(corpus, _ontology(), config, store=store, shard_index=0)
    path = next(path for path in store.item_paths() if json.loads(path.read_text())["candidates"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidates"][0]["score"] = score
    _refresh_payload_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointError, match="finite"):
        score_corpus_checkpointed(corpus, _ontology(), config, store=store, finalize_only=True)


@pytest.mark.parametrize("mutator", [_change_valid_iri, _change_valid_finite_score])
def test_shape_valid_checkpoint_corruption_fails_digest(
    tmp_path: Path, mutator: Callable[[CheckpointPayload], None]
) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    score_corpus_checkpointed(corpus, _ontology(), config, store=store, shard_index=0)
    path = next(path for path in store.item_paths() if json.loads(path.read_text())["candidates"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutator(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointError, match="digest mismatch"):
        score_corpus_checkpointed(corpus, _ontology(), config, store=store, finalize_only=True)


def test_fingerprint_drift_is_rejected(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    path = tmp_path / "checkpoint"
    SyntheticCheckpointStore.create(
        path,
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )

    with pytest.raises(CheckpointError, match="fingerprint"):
        SyntheticCheckpointStore.create(
            path,
            fingerprint=replace(_fingerprint(config), git_head="f" * 40),
            shard_count=1,
            expected_item_count=3,
            retained_limit=DEPTH_PROBE_MAX,
        )


def test_concurrent_divergent_fingerprints_accept_exactly_one_manifest(
    tmp_path: Path,
) -> None:
    config = AnswerRuleConfig()
    path = tmp_path / "checkpoint"
    fingerprints = [
        _fingerprint(config),
        replace(_fingerprint(config), git_head="f" * 40),
    ]
    barrier = threading.Barrier(2)

    def initialize(
        fingerprint: CheckpointFingerprint,
    ) -> tuple[str, SyntheticCheckpointStore | CheckpointError]:
        barrier.wait()
        try:
            return (
                "created",
                SyntheticCheckpointStore.create(
                    path,
                    fingerprint=fingerprint,
                    shard_count=2,
                    expected_item_count=3,
                    retained_limit=DEPTH_PROBE_MAX,
                ),
            )
        except CheckpointError as exc:
            return ("rejected", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(initialize, fingerprints))

    assert sorted(status for status, _result in outcomes) == ["created", "rejected"]
    winner = next(result for status, result in outcomes if status == "created")
    assert isinstance(winner, SyntheticCheckpointStore)
    manifest = json.loads(winner.manifest_path.read_text(encoding="utf-8"))
    assert manifest["fingerprint_sha256"] == winner.fingerprint.content_sha256()
    assert not list(path.glob("*.tmp"))


def test_checkpoint_items_are_bound_to_accepted_fingerprint(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    score_corpus_checkpointed(corpus, _ontology(), config, store=store, shard_index=0)
    path = store.item_paths()[0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["fingerprint_sha256"] = "f" * 64
    _refresh_payload_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointError, match="fingerprint"):
        score_corpus_checkpointed(corpus, _ontology(), config, store=store, finalize_only=True)


def test_build_checkpoint_fingerprint_uses_live_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    repo = _clean_git_repo(tmp_path)
    monkeypatch.setenv("PYTHONHASHSEED", "37")

    fingerprint = build_checkpoint_fingerprint(corpus, config, repo_root=repo)

    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert fingerprint.corpus_content_sha256 == corpus.manifest.content_sha256
    assert fingerprint.nomatch_content_sha256 == corpus.manifest.nomatch_content_sha256
    assert fingerprint.answer_rule_config_sha256 == config.content_sha256()
    assert fingerprint.ontology_cache_sha256 == corpus.manifest.ontology_cache_sha256
    assert fingerprint.git_head == git_head
    assert fingerprint.python_hash_seed == "37"
    assert (
        fingerprint.lockfile_sha256 == hashlib.sha256((repo / "uv.lock").read_bytes()).hexdigest()
    )


def test_build_checkpoint_fingerprint_rejects_untracked_but_ignores_ignored(
    tmp_path: Path,
) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    repo = _clean_git_repo(tmp_path)
    ignored = repo / ".synthetic-checkpoints" / "run"
    ignored.mkdir(parents=True)
    (ignored / "manifest.json").write_text("{}\n", encoding="utf-8")
    build_checkpoint_fingerprint(corpus, config, repo_root=repo)

    (repo / "untracked_scorer.py").write_text("SCORING_VERSION = 2\n", encoding="utf-8")

    with pytest.raises(CheckpointError, match="working tree is dirty"):
        build_checkpoint_fingerprint(corpus, config, repo_root=repo)


def test_atomic_item_write_failure_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AnswerRuleConfig()
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=1,
        retained_limit=DEPTH_PROBE_MAX,
    )
    adapted = DocumentAdapter(_ontology()).adapt("Arbitration Rules")

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(checkpoint_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        store.write_item(
            "scoreable",
            "score-1",
            candidates=adapted.candidates,
            raw_candidate_count=adapted.raw_candidate_count,
            suppression_counters=adapted.suppression_counters,
        )

    assert not store.item_path("scoreable", "score-1").exists()
    assert not list(store.items_dir.glob("*.tmp"))


def test_truncated_checkpoint_fails_closed(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    score_corpus_checkpointed(corpus, _ontology(), config, store=store, shard_index=0)
    store.item_paths()[0].write_text('{"candidates":', encoding="utf-8")

    with pytest.raises(CheckpointError, match="corrupt"):
        score_corpus_checkpointed(corpus, _ontology(), config, store=store, finalize_only=True)


def test_progress_is_ephemeral_and_reports_only_new_items(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    store = SyntheticCheckpointStore.create(
        tmp_path / "checkpoint",
        fingerprint=_fingerprint(config),
        shard_count=1,
        expected_item_count=3,
        retained_limit=DEPTH_PROBE_MAX,
    )
    progress: list[dict[str, object]] = []
    score_corpus_checkpointed(
        corpus,
        _ontology(),
        config,
        store=store,
        shard_index=0,
        progress=progress.append,
    )
    assert [entry["completed"] for entry in progress] == [1, 2, 3]
    assert all("eta_seconds" in entry for entry in progress)
    assert not any("eta" in path.read_text(encoding="utf-8") for path in store.item_paths())

    progress.clear()
    score_corpus_checkpointed(
        corpus,
        _ontology(),
        config,
        store=store,
        shard_index=0,
        progress=progress.append,
    )
    assert progress == []
