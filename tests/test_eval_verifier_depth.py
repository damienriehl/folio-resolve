"""Small offline fixtures for shortlist recall and faithful baseline replay."""

import json
from pathlib import Path

import pytest
from folio_eval.answer_rule import load_config
from folio_eval.score import score_items
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem
from folio_eval.verifier import Thresholds, emit, load_collection, score_collection
from folio_eval.verifier_depth import build_baseline_collection, depth_curve

from folio_resolve.pipeline import MatchCandidate


def candidates(n):
    return tuple(
        MatchCandidate(iri=f"i{i}", label="", score=100 - i / 10, extraction_path="fixture")
        for i in range(1, n + 1)
    )


def test_ranks_and_unreachable():
    result = depth_curve({"s": candidates(60)}, {"s": frozenset({"i3", "i12", "i40"})})
    assert result["curve"]["10"]["micro_recall"] == pytest.approx(1 / 3)
    assert result["curve"]["20"]["mean_item_recall"] == pytest.approx(2 / 3)
    assert result["curve"]["50"]["micro_recall"] == 1
    assert result["chosen_n"] == 50
    missing = depth_curve({"s": candidates(2)}, {"s": frozenset({"absent"})})
    assert all(row["unreachable_gold_count"] == 1 for row in missing["curve"].values())
    assert missing["unreachable_gold_count_at_200"] == 1


def test_micro_differs_from_mean_and_short_lists():
    result = depth_curve(
        {"a": candidates(1), "b": ()}, {"a": frozenset({"i1"}), "b": frozenset({"x", "y", "z"})}
    )
    assert result["curve"]["200"]["micro_recall"] == 0.25
    assert result["curve"]["200"]["mean_item_recall"] == 0.5
    assert result["chosen_n"] == 6


def test_depth_choice_tolerance_and_cap():
    # Exactly one percentage point below the depth-200 reference qualifies.
    gold = {f"s{i}": frozenset({"i6" if i < 99 else "i200"}) for i in range(100)}
    result = depth_curve({key: candidates(200) for key in gold}, gold)
    assert result["chosen_n"] == 6
    capped = depth_curve({"s": candidates(200)}, {"s": frozenset({"i150"})})
    assert capped["chosen_n"] == 100
    assert capped["uncapped_n"] == 150


@pytest.mark.parametrize("depths", [(201,), (0,), (True,)])
def test_invalid_depth(depths):
    with pytest.raises(ValueError):
        depth_curve({"s": ()}, {"s": frozenset({"x"})}, depths=depths)


def fixture_corpus(config):
    manifest = CorpusManifest(
        version=1,
        content_sha256="a" * 64,
        nomatch_content_sha256="b" * 64,
        ontology_cache_sha256="c" * 64,
        answer_rule_config_sha256=config.content_sha256(),
        item_counts={"scoreable": 2, "nomatch": 1},
        non_lexical_fraction=1,
        non_lexical_floor=0.3,
        scoreable=True,
        seed=7,
        created="test",
        manifest_path=Path("unused.json"),
    )

    def item(key, gold=()):
        return SyntheticItem(
            item_id=key,
            doc_type="motion",
            jurisdiction="US",
            text="fixture",
            gold_iris=frozenset(gold),
            verification="deterministic",
        )

    return LoadedCorpus(manifest, (item("a", ("i1", "i7")), item("b", ("i2",))), (item("n"),))


def test_baseline_roundtrip_and_score_equivalence(tmp_path):
    config = load_config(Path("eval/synthetic/answer_rule_config_synthetic_v1.json"))
    corpus = fixture_corpus(config)
    survivors = {"a": candidates(10), "b": candidates(2), "n": candidates(8)}
    survivors["b"][1].score = 49  # Survives the adapter, below commitment threshold.
    collection = build_baseline_collection(
        corpus,
        survivors,
        config,
        shortlist_depth=10,
        adapter_source="fixture",
        adapter_sha256="d" * 64,
    )
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(collection.to_json()))
    loaded = load_collection(path, corpus)
    assert loaded.arm_name == "baseline-attempt-0004"
    assert all(d.no_match_p == 0 for d in loaded.decisions)
    assert all(
        d.shortlist == tuple(c.iri for c in survivors[d.item_id][:10]) for d in loaded.decisions
    )
    assert emit(loaded.decisions[-1], Thresholds(0.5, None)) == tuple(f"i{i}" for i in range(1, 7))
    expected = score_items(
        corpus.gold_item_records(),
        lambda item: survivors[item.item_id],
        config=config,
        slice_name="synthetic",
    )
    actual = score_collection(loaded, corpus, thresholds=Thresholds(0.5, None))
    assert actual.run.overall == expected.overall
    assert [(s.item_id, s.committed_iris, s.tp, s.fp, s.fn) for s in actual.run.item_scores] == [
        (s.item_id, s.committed_iris, s.tp, s.fp, s.fn) for s in expected.item_scores
    ]
    assert actual.nomatch_fp_rate == 1


@pytest.fixture
def runner(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from folio_eval import verifier_depth as module
    from folio_eval.leakcheck import build_manifest
    from folio_eval.synthetic_checkpoint import CheckpointFingerprint
    from folio_eval.synthetic_contract import SUPPRESSION_CATEGORIES

    config = load_config(Path("eval/synthetic/answer_rule_config_synthetic_v1.json"))
    corpus = fixture_corpus(config)
    calls = []
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "ensure_hash_seed", lambda: None)
    monkeypatch.setattr(module, "load_corpus", lambda path: corpus)
    monkeypatch.setattr(module, "load_config", lambda path: config)
    fingerprint = CheckpointFingerprint(
        corpus.manifest.content_sha256,
        corpus.manifest.nomatch_content_sha256,
        config.content_sha256(),
        corpus.manifest.ontology_cache_sha256,
        "fixture-commit",
        "0",
        "fixture",
        "fixture",
        "fixture",
        "d" * 64,
    )
    monkeypatch.setattr(module, "build_checkpoint_fingerprint", lambda *a, **kw: fingerprint)
    monkeypatch.setattr(
        module,
        "assert_ontology_pin",
        lambda pin: (calls.append("pin"), SimpleNamespace(sha256=pin))[1],
    )
    salt = tmp_path / "salt"
    salt.write_bytes(b"fixture")

    def manifest(words):
        value = build_manifest(
            words, b"fixture", gold_version="fixture", gold_content_sha256="e" * 64
        )
        monkeypatch.setattr(module, "load_manifest", lambda path: value)

    manifest(["thresholds"])
    monkeypatch.setitem(
        sys.modules, "folio", SimpleNamespace(FOLIO=lambda: calls.append("ontology"))
    )

    def adapt(text):
        calls.append("adapt")
        return SimpleNamespace(
            candidates=candidates(8),
            raw_candidate_count=8,
            suppression_counters=dict.fromkeys(SUPPRESSION_CATEGORIES, 0),
        )

    adapter = SimpleNamespace(adapt=adapt)
    monkeypatch.setattr(module, "DocumentAdapter", lambda provider: adapter)
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
        args=args,
        calls=calls,
        manifest=manifest,
        corpus=corpus,
        config=config,
        root=tmp_path,
        fingerprint=fingerprint,
        adapter=adapter,
    )


def test_preflight_rejects_fixed_text_before_adapter(runner, monkeypatch):
    monkeypatch.setattr(runner.module, "render_markdown", lambda report: "Thresholds")
    with pytest.raises(ValueError, match="Markdown output"):
        runner.module.main(runner.args)
    assert runner.calls == []


def test_rendered_outputs_never_contain_forbidden_word(runner):
    report = depth_curve(
        {"a": candidates(8), "b": candidates(8)},
        {item.item_id: item.gold_iris for item in runner.corpus.scoreable_items},
    )
    collection = build_baseline_collection(
        runner.corpus,
        dict.fromkeys(("a", "b", "n"), candidates(8)),
        runner.config,
        shortlist_depth=10,
        adapter_source="fixture",
        adapter_sha256="d" * 64,
    )
    for output in (
        runner.module.render_markdown(report),
        json.dumps(report),
        json.dumps(collection.to_json()),
    ):
        assert "thresholds" not in output.lower()


def test_baseline_empty_controls_and_zero_probability_survivors():
    config = load_config(Path("eval/synthetic/answer_rule_config_synthetic_v1.json"))
    corpus = fixture_corpus(config)
    collection = build_baseline_collection(
        corpus,
        {"a": candidates(8), "b": (), "n": ()},
        config,
        shortlist_depth=10,
        adapter_source="fixture",
        adapter_sha256="d" * 64,
    )
    assert [c.p for c in collection.decisions[0].candidates] == [1] * 6 + [0] * 2
    assert collection.decisions[-1].no_match_p == 0
    assert emit(collection.decisions[-1], Thresholds(0.5, None)) == ()


def read_outputs(runner):
    paths = (
        "docs/benchmarks/verifier-shortlist-depth.json",
        "docs/benchmarks/verifier-shortlist-depth.md",
        "eval/synthetic/verifier/baseline-collection-v1.json",
    )
    outputs = [(runner.root / path).read_text() for path in paths]
    assert all("thresholds" not in text.lower() for text in outputs)
    return outputs


def test_two_shards_match_single_run(runner):
    from folio_eval.synthetic_checkpoint import checkpoint_item_key, shard_for_item

    assert runner.module.main(runner.args) == 0
    expected = read_outputs(runner)
    assert runner.calls.count("adapt") == 3
    for path in (runner.root / "docs/benchmarks").iterdir():
        path.unlink()
    (runner.root / "eval/synthetic/verifier/baseline-collection-v1.json").unlink()
    args = [*runner.args, "--checkpoint-dir", str(runner.root / "sharded"), "--shard-count", "2"]
    runner.calls.clear()
    assert runner.module.main([*args, "--shard-index", "0"]) == 0
    expected_count = sum(
        shard_for_item(checkpoint_item_key(kind, item.item_id), 2) == 0
        for kind, group in (
            ("scoreable", runner.corpus.scoreable_items),
            ("nomatch", runner.corpus.nomatch_items),
        )
        for item in group
    )
    assert runner.calls.count("adapt") == expected_count
    assert not (runner.root / "docs/benchmarks/verifier-shortlist-depth.json").exists()
    assert runner.module.main([*args, "--shard-index", "1"]) == 0
    assert runner.calls.count("adapt") == 3
    assert not (runner.root / "docs/benchmarks/verifier-shortlist-depth.json").exists()
    runner.calls.clear()
    assert runner.module.main([*args, "--finalize-only"]) == 0
    assert runner.calls == []
    assert read_outputs(runner) == expected
    expected_curve = depth_curve(
        dict.fromkeys(("a", "b", "n"), candidates(8)),
        {i.item_id: i.gold_iris for i in runner.corpus.scoreable_items},
    )
    assert json.loads(expected[0])["curve"] == expected_curve["curve"]
    payload = json.loads(expected[2])
    direct = build_baseline_collection(
        runner.corpus,
        dict.fromkeys(("a", "b", "n"), candidates(8)),
        runner.config,
        shortlist_depth=expected_curve["chosen_n"],
        adapter_source=payload["adapter_source"],
        adapter_sha256=payload["adapter_sha256"],
    )
    assert payload == direct.to_json()
    assert runner.module.main([*args, "--shard-index", "0"]) == 0
    assert runner.calls == []  # Resuming an already durable shard also does no work.


def test_finalize_failure_retains_synthetic_checkpoint(runner, monkeypatch):
    from folio_eval.synthetic_checkpoint import SyntheticCheckpointStore
    from folio_eval.synthetic_score import score_corpus_checkpointed

    store = SyntheticCheckpointStore.create(
        runner.root / "checkpoint",
        fingerprint=runner.fingerprint,
        shard_count=1,
        expected_item_count=3,
        retained_limit=200,
    )
    score_corpus_checkpointed(
        runner.corpus, None, runner.config, store=store, adapter=runner.adapter
    )
    before = {path: path.read_bytes() for path in store.item_paths()}
    assert len(before) == 3
    runner.calls.clear()
    original = runner.module.render_markdown

    def broken(report):
        # The preflight has zero retrieved gold; fail only on the completed report.
        return original(report) + ("Thresholds" if report["chosen_n"] == 10 else "")

    monkeypatch.setattr(runner.module, "render_markdown", broken)
    with pytest.raises(ValueError, match=r"Markdown output.*re-finalize needs no recomputation"):
        runner.module.main([*runner.args, "--finalize-only"])
    assert not (runner.root / "docs").exists()
    assert not (runner.root / "eval").exists()
    assert {path: path.read_bytes() for path in store.item_paths()} == before
    assert runner.calls == []
    monkeypatch.setattr(runner.module, "render_markdown", original)
    assert runner.module.main([*runner.args, "--finalize-only"]) == 0
    assert runner.calls == []
    assert {path: path.read_bytes() for path in store.item_paths()} == before
    read_outputs(runner)


def test_preflight_scans_json_keys(runner, monkeypatch):
    original = runner.module.depth_curve

    def broken(*args, **kwargs):
        return {**original(*args, **kwargs), "thresholds": 0}

    monkeypatch.setattr(runner.module, "depth_curve", broken)
    with pytest.raises(ValueError, match="depth JSON output"):
        runner.module.main(runner.args)
    assert runner.calls == []


def test_finalize_incomplete_never_adapts(runner):
    from folio_eval.synthetic_checkpoint import CheckpointError

    with pytest.raises(CheckpointError, match="incomplete"):
        runner.module.main([*runner.args, "--finalize-only"])
    assert runner.calls == []
