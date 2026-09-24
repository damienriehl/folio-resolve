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


@pytest.mark.parametrize("leak", [False, True])
def test_runner_outputs_and_leak_gate(tmp_path, monkeypatch, leak):
    import sys
    from types import SimpleNamespace

    from folio_eval import verifier_depth as module

    config = load_config(Path("eval/synthetic/answer_rule_config_synthetic_v1.json"))
    corpus = fixture_corpus(config)
    calls = []
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "ensure_hash_seed", lambda: calls.append("seed"))
    monkeypatch.setattr(module, "load_corpus", lambda path: corpus)
    monkeypatch.setattr(module, "load_config", lambda path: config)

    def fingerprint(*args, **kwargs):
        calls.append("pristine")
        return SimpleNamespace(git_head="fixture-commit")

    monkeypatch.setattr(module, "build_checkpoint_fingerprint", fingerprint)
    monkeypatch.setattr(module, "assert_ontology_pin", lambda pin: SimpleNamespace(sha256=pin))
    monkeypatch.setattr(module, "load_manifest", lambda path: object())

    def scan_json(value, manifest, salt):
        calls.append("json")
        return int(leak)

    monkeypatch.setattr(module, "scan_json_value", scan_json)
    monkeypatch.setattr(module, "scan_text", lambda *args: 0)
    monkeypatch.setitem(sys.modules, "folio", SimpleNamespace(FOLIO=lambda: object()))

    def adapt(text):
        calls.append("adapt")
        return SimpleNamespace(candidates=candidates(8))

    monkeypatch.setattr(module, "DocumentAdapter", lambda provider: SimpleNamespace(adapt=adapt))
    salt = tmp_path / "salt"
    salt.write_bytes(b"fixture")
    args = ["--corpus-manifest", "unused", "--leak-manifest", "unused", "--salt-file", str(salt)]
    if leak:
        with pytest.raises(ValueError, match="leak check"):
            module.main(args)
        assert not (tmp_path / "docs").exists()
        assert not (tmp_path / "eval").exists()
    else:
        assert module.main(args) == 0
        report = json.loads(
            (tmp_path / "docs/benchmarks/verifier-shortlist-depth.json").read_text()
        )
        markdown = (tmp_path / "docs/benchmarks/verifier-shortlist-depth.md").read_text()
        loaded = load_collection(
            tmp_path / "eval/synthetic/verifier/baseline-collection-v1.json", corpus
        )
        assert loaded.shortlist_depth == report["chosen_n"] == 10
        assert "Chosen N: 10" in markdown
        assert calls.count("json") == 2
    assert calls[:2] == ["seed", "pristine"]
    assert calls.count("adapt") == 3


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
