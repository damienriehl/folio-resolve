"""Offline evidence for uncapped, resumable attribution collection."""

import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from folio_eval.answer_rule import load_config
from folio_eval.leakcheck import build_manifest
from folio_eval.synthetic_checkpoint import CheckpointError, SyntheticCheckpointStore
from folio_eval.synthetic_score import AdapterResult, CandidateTrace
from test_eval_verifier_depth import candidates, fixture_corpus


@pytest.fixture
def runner(tmp_path, monkeypatch):
    from folio_eval import recall_attribution as module
    from folio_eval.synthetic_checkpoint import CheckpointFingerprint

    config = load_config(Path("eval/synthetic/answer_rule_config_synthetic_v1.json"))
    corpus = replace(fixture_corpus(config), nomatch_items=())
    corpus = replace(
        corpus, corpus_items=(replace(corpus.corpus_items[0], item_id="2"), corpus.corpus_items[1])
    )
    fingerprint = CheckpointFingerprint(
        "a" * 64,
        "b" * 64,
        config.content_sha256(),
        "c" * 64,
        "fixture",
        "0",
        "fixture",
        "fixture",
        "fixture",
        "d" * 64,
    )
    calls = []
    survivors = candidates(205)
    traces = (
        *tuple(
            CandidateTrace(c.iri, "", "", "", "", c.score, c.score, "survived", False, "")
            for c in survivors
        ),
        CandidateTrace("blocked", "", "", "", "", 90, None, "blocklist", True, "blocked_alias"),
    )

    def adapt(text):
        calls.append(text)
        return AdapterResult(
            survivors,
            206,
            dict(blocklist=1, place_gate=0, short_label_gate=0, score_floor=0),
            traces,
        )

    monkeypatch.setattr(module, "ensure_hash_seed", lambda: None)
    monkeypatch.setattr(module, "load_corpus", lambda path: corpus)
    monkeypatch.setattr(module, "load_config", lambda path: config)
    monkeypatch.setattr(module, "build_checkpoint_fingerprint", lambda *a, **kw: fingerprint)
    monkeypatch.setattr(module, "make_adapter", lambda corpus: SimpleNamespace(adapt=adapt))

    def manifest(words):
        value = build_manifest(
            words, b"fixture", gold_version="fixture", gold_content_sha256="e" * 64
        )
        monkeypatch.setattr(module, "load_manifest", lambda path: value)

    manifest(["unlikely secret surface"])
    salt = tmp_path / "salt"
    salt.write_bytes(b"fixture")
    args = [
        "--corpus-manifest",
        "unused",
        "--leak-manifest",
        "unused",
        "--salt-file",
        str(salt),
        "--checkpoint-dir",
        str(tmp_path / "checkpoint"),
    ]
    return SimpleNamespace(
        module=module,
        corpus=corpus,
        config=config,
        fingerprint=fingerprint,
        calls=calls,
        args=args,
        root=tmp_path,
        manifest=manifest,
    )


def test_uncapped_survivors_and_blocklist_roundtrip(runner):
    assert runner.module.main(runner.args) == 0
    from folio_eval.synthetic_checkpoint import AttributionCheckpointStore

    store = AttributionCheckpointStore.create(
        runner.root / "checkpoint",
        fingerprint=runner.fingerprint,
        shard_count=1,
        expected_item_count=2,
    )
    assert len(store.item_paths()) == 2
    for item in runner.corpus.scoreable_items:
        result = store.load_item("scoreable", item.item_id)
        assert len(result.candidates) == result.survivor_count == 205
        assert len(result.traces) == result.raw_candidate_count == 206
        blocked = next(t for t in result.traces if t.iri == "blocked")
        assert (
            blocked.gate_disposition,
            blocked.gate_reason,
            blocked.pre_gate_score,
            blocked.post_gate_score,
        ) == ("blocklist", "blocked_alias", 90, None)


def test_resume_only_missing_and_finalize_without_adapt(runner):
    args = [*runner.args, "--shard-count", "2"]
    runner.module.main([*args, "--shard-index", "0"])
    count = len(runner.calls)
    assert 0 < count < 2
    runner.module.main([*args, "--shard-index", "0"])
    assert len(runner.calls) == count
    with pytest.raises(CheckpointError, match="incomplete"):
        runner.module.main([*args, "--finalize-only"])
    assert len(runner.calls) == count
    runner.module.main([*args, "--shard-index", "1"])
    assert len(runner.calls) == 2
    runner.module.main([*args, "--finalize-only"])
    assert len(runner.calls) == 2


def test_stage_one_checkpoint_rejected_untouched(runner):
    store = SyntheticCheckpointStore.create(
        runner.root / "checkpoint",
        fingerprint=runner.fingerprint,
        shard_count=1,
        expected_item_count=2,
        retained_limit=200,
    )
    before = store.manifest_path.read_bytes()
    with pytest.raises(CheckpointError, match="manifest"):
        runner.module.main(runner.args)
    assert store.manifest_path.read_bytes() == before
    assert runner.calls == []


@pytest.mark.parametrize("status", [" M tracked.py\n", "?? untracked.py\n"])
def test_dirty_fingerprint_fails_before_adapt(runner, monkeypatch, status):
    from folio_eval import synthetic_checkpoint as checkpoint

    def git_status(cmd, **kwargs):
        assert cmd == ["git", "status", "--porcelain"]
        return subprocess.CompletedProcess(cmd, 0, stdout=status)

    monkeypatch.setattr(checkpoint.subprocess, "run", git_status)
    monkeypatch.setattr(
        runner.module, "build_checkpoint_fingerprint", checkpoint.build_checkpoint_fingerprint
    )
    with pytest.raises(CheckpointError, match="working tree is dirty"):
        runner.module.main(runner.args)
    assert runner.calls == []


def test_fixed_prose_collision_precedes_adapt(runner):
    runner.manifest(["Recall attribution"])
    with pytest.raises(ValueError, match="leak check"):
        runner.module.main(runner.args)
    assert runner.calls == []
    assert not (runner.root / "checkpoint").exists()


@pytest.mark.parametrize("damage", ["missing_trace", "score", "duplicate", "schema"])
def test_finalize_rejects_corrupt_lifecycle_evidence(runner, damage):
    import json

    from folio_eval.synthetic_checkpoint import _payload_sha256

    runner.module.main(runner.args)
    path = next((runner.root / "checkpoint/items").glob("*.json"))
    payload = json.loads(path.read_text())
    if damage == "missing_trace":
        payload["traces"].pop()
    elif damage == "score":
        payload["traces"][-1]["post_gate_score"] = -1
    elif damage == "duplicate":
        payload["traces"][-1] = payload["traces"][0]
    else:
        payload["schema_version"] = 3
    del payload["payload_sha256"]
    payload["payload_sha256"] = _payload_sha256(payload)
    path.write_text(json.dumps(payload))
    runner.calls.clear()
    with pytest.raises(CheckpointError):
        runner.module.main([*runner.args, "--finalize-only"])
    assert runner.calls == []
