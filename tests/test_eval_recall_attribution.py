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
    import json

    from folio_eval.grade import GraderVote
    from folio_eval.resolve_labels import IndexedConcept, LabelIndex
    from folio_eval.verifier_depth import depth_curve

    corpus = replace(
        corpus,
        corpus_items=tuple(
            replace(
                item,
                provenance={
                    "grader_votes": [
                        GraderVote(
                            item.item_id,
                            str(i),
                            str(i),
                            {iri: 0.6 for iri in item.gold_iris},
                            "gen",
                        ).to_json()
                        for i in range(3)
                    ]
                },
            )
            for item in corpus.corpus_items
        ),
    )
    dictionary = LabelIndex.from_concepts(
        [IndexedConcept(f"i{i}", (f"i{i}",)) for i in range(1, 206)]
    )
    monkeypatch.setattr(module, "assert_ontology_pin", lambda value: None)
    monkeypatch.setattr(
        module,
        "load_folio_index",
        lambda: (dictionary, corpus.manifest.ontology_cache_sha256, "fixture"),
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    depth_path = tmp_path / "docs/benchmarks/verifier-shortlist-depth.json"
    depth_path.parent.mkdir(parents=True)
    depth_path.write_text(
        json.dumps(
            depth_curve(
                {item.item_id: candidates(205) for item in corpus.scoreable_items},
                {item.item_id: item.gold_iris for item in corpus.scoreable_items},
            )
        )
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


def math_inputs():
    from folio_eval.synthetic_checkpoint import AttributionTrace

    survivors = {"s": candidates(240), "empty": (), "nomatch": candidates(2)}
    gold = {
        "s": frozenset({"i1", "i140", "i201", "i240", "blocked", "never", "absent"}),
        "empty": frozenset(),
    }
    traces = {
        "s": (
            *(AttributionTrace(c.iri, "survived", "", c.score, c.score) for c in survivors["s"]),
            AttributionTrace("blocked", "short_label_gate", "short", 90, None),
        )
    }
    return dict(
        survivors=survivors,
        traces=traces,
        gold=gold,
        ontology_iris=frozenset({c.iri for c in survivors["s"]} | {"blocked", "never"}),
        resolved_votes={
            "s": [{iri: 0.6 for iri in gold["s"]}] * 2 + [{iri: 0.599 for iri in gold["s"]}]
        },
        strata={"s": "motion"},
    )


def test_attribution_stages_rank_distance_and_no_match():
    from folio_eval.recall_attribution import attribute_relations

    report = attribute_relations(**math_inputs())
    rows = {row["iri"]: row for row in report["relations"]}
    assert len(rows) == report["gold_relation_count"] == 7
    assert rows["i140"]["stage"] == "rank_101_200"
    assert rows["i140"]["rank"] == 140
    assert rows["blocked"]["stage"] == "short_label_gate"
    assert rows["never"]["stage"] == "never_produced"
    assert rows["absent"]["stage"] == "ontology_absent"
    assert report["rank_distance_below_200"] == {"1": 1, "40": 1}
    assert {row["item_id"] for row in rows.values()} == {"s"}
    assert all(row["agreement"] == 2 for row in rows.values())
    assert report["overall"]["rank_below_200"] == {
        "count": 2,
        "share": 2 / 7,
        "by_agreement": {"2": 2, "3": 0},
    }
    assert report["by_stratum"]["motion"] == report["overall"]
    assert sum(row["count"] for row in report["overall"].values()) == 7
    assert (
        report["depth_curve"]["curve"]["200"]["retrieved_gold_count"]
        == report["overall"]["top_100"]["count"] + report["overall"]["rank_101_200"]["count"]
    )


@pytest.mark.parametrize("gate", ["blocklist", "place_gate", "short_label_gate", "score_floor"])
def test_named_gates(gate):
    from folio_eval.recall_attribution import attribute_relations

    inputs = math_inputs()
    inputs["traces"]["s"] = (
        *inputs["traces"]["s"][:-1],
        replace(inputs["traces"]["s"][-1], gate_disposition=gate),
    )
    report = attribute_relations(**inputs)
    assert next(r for r in report["relations"] if r["iri"] == "blocked")["stage"] == gate


def test_agreement_resolves_alternative_labels_and_max_confidence():
    from folio_eval.grade import GraderVote
    from folio_eval.recall_attribution import attribute_relations, resolve_grader_votes
    from folio_eval.resolve_labels import IndexedConcept, LabelIndex

    dictionary = LabelIndex.from_concepts([IndexedConcept("i1", ("Alpha",), ("Alternate",))])
    votes = [
        GraderVote("s", str(i), str(i), labels, "gen")
        for i, labels in enumerate(
            [{"Alpha": 0.1, "Alternate": 0.6}, {"Alpha": 0.6}, {"Alpha": 0.6}]
        )
    ]
    inputs = math_inputs()
    inputs["gold"] = {"s": frozenset({"i1"})}
    inputs["resolved_votes"] = resolve_grader_votes(votes, dictionary)
    report = attribute_relations(**inputs)
    assert report["relations"][0]["agreement"] == 3
    assert report["overall"]["top_100"]["by_agreement"] == {"2": 0, "3": 1}
    with pytest.raises(ValueError, match="duplicate"):
        resolve_grader_votes([votes[0], votes[0]], dictionary)


def test_insufficient_agreement_fails_closed():
    from folio_eval.recall_attribution import attribute_relations

    inputs = math_inputs()
    inputs["resolved_votes"]["s"] = inputs["resolved_votes"]["s"][1:]
    with pytest.raises(ValueError, match="fewer than two"):
        attribute_relations(**inputs)


def test_committed_depth_shape_reconciles_363_relations():
    import json

    from folio_eval.recall_attribution import attribute_relations, reconcile_depth

    expected = json.loads(Path("docs/benchmarks/verifier-shortlist-depth.json").read_text())
    survivors = {"s": candidates(200)}
    iris = frozenset(
        [
            *(f"i{i}" for i in range(1, 71)),
            *(f"i{i}" for i in range(101, 114)),
            *(f"missing{i}" for i in range(280)),
        ]
    )
    report = attribute_relations(
        survivors, {}, {"s": iris}, iris, {"s": [dict.fromkeys(iris, 0.6)] * 3}, {"s": "brief"}
    )
    assert sum(row["count"] for row in report["overall"].values()) == 363
    assert report["overall"]["top_100"]["count"] == 70
    assert report["overall"]["rank_101_200"]["count"] == 13
    with pytest.raises(ValueError, match="population"):
        reconcile_depth(survivors, {"s": iris}, expected)
    with pytest.raises(ValueError, match="reconciliation"):
        reconcile_depth({"s": candidates(112)}, {"s": iris}, expected)
    with pytest.raises(ValueError, match="reconciliation"):
        reconcile_depth({"s": candidates(69)}, {"s": iris}, expected)


def test_finalize_publishes_fingerprint_and_exact_byte_hash_without_adapt(runner):
    import hashlib
    import json

    runner.module.main(runner.args)
    output = runner.root / "checkpoint/attribution.json"
    first = output.read_bytes()
    payload = json.loads(first)
    assert payload["gold_relation_count"] == 3
    assert payload["checkpoint_fingerprint_sha256"] == runner.fingerprint.content_sha256()
    assert (
        output.with_suffix(".json.sha256").read_text().strip() == hashlib.sha256(first).hexdigest()
    )
    runner.calls.clear()
    runner.module.main([*runner.args, "--finalize-only"])
    assert runner.calls == []
    assert output.read_bytes() == first


def test_finalize_reconciliation_failure_does_not_publish(runner):
    import json

    args = [*runner.args, "--shard-count", "2"]
    runner.module.main([*args, "--shard-index", "0"])
    runner.module.main([*args, "--shard-index", "1"])
    depth_path = runner.root / "docs/benchmarks/verifier-shortlist-depth.json"
    expected = json.loads(depth_path.read_text())
    expected["curve"]["200"]["retrieved_gold_count"] += 1
    depth_path.write_text(json.dumps(expected))
    with pytest.raises(ValueError, match="reconciliation"):
        runner.module.main([*args, "--finalize-only"])
    assert not (runner.root / "checkpoint/attribution.json").exists()


def test_distinct_stratum_denominators_and_empty_population():
    from folio_eval.recall_attribution import attribute_relations

    inputs = math_inputs()
    inputs["gold"]["t"] = frozenset({"i1"})
    inputs["survivors"]["t"] = candidates(1)
    inputs["strata"]["t"] = "brief"
    inputs["resolved_votes"]["t"] = [{"i1": 0.6}] * 3
    report = attribute_relations(**inputs)
    assert report["overall"]["top_100"]["share"] == 2 / 8
    assert report["by_stratum"]["brief"]["top_100"]["share"] == 1
    assert report["by_stratum"]["motion"]["top_100"]["share"] == 1 / 7
    inputs["gold"] = {"empty": frozenset()}
    report = attribute_relations(**inputs)
    assert report["relations"] == []
    assert report["gold_relation_count"] == 0
    assert all(row["share"] == 0 for row in report["overall"].values())


def test_finalize_uses_gold_record_strata_and_excludes_nomatch(runner):
    from folio_eval.synthetic_checkpoint import AttributionCheckpointStore
    from folio_eval.verifier_depth import depth_curve

    runner.module.main(runner.args)
    store = AttributionCheckpointStore.create(
        runner.root / "checkpoint",
        fingerprint=runner.fingerprint,
        shard_count=1,
        expected_item_count=2,
    )
    first, second = runner.corpus.corpus_items
    corpus = replace(
        runner.corpus, corpus_items=(first, replace(second, provenance={"no_match": True}))
    )
    dictionary, _, _ = runner.module.load_folio_index()
    # The marked no-match's missing votes and absent checkpoint must never be read.
    assembled = {("scoreable", first.item_id): store.load_item("scoreable", first.item_id)}
    expected = depth_curve({first.item_id: candidates(205)}, {first.item_id: first.gold_iris})
    result = runner.module.finalize_attribution(corpus, store, assembled, dictionary, expected)
    assert {r["item_id"] for r in result["relations"]} == {first.item_id}
    assert set(result["by_stratum"]) == {corpus.gold_item_records()[0].stratum_id}


def test_review_wrong_population_rejected():
    import json

    from folio_eval.recall_attribution import reconcile_depth

    committed = json.loads(Path("docs/benchmarks/verifier-shortlist-depth.json").read_text())
    iris = frozenset([*(f"i{i}" for i in range(1, 71)), *(f"i{i}" for i in range(101, 114))])
    with pytest.raises(ValueError, match="reconciliation"):
        reconcile_depth({"s": candidates(200)}, {"s": iris}, committed)


def test_review_heading_collision_before_retrieval(runner):
    runner.manifest(["Distance past rank 200"])
    with pytest.raises(ValueError, match="leak check"):
        runner.module.main(runner.args)
    assert runner.calls == []


def reconciled_fixture():
    import json

    from folio_eval.recall_attribution import attribute_relations

    expected = json.loads(Path("docs/benchmarks/verifier-shortlist-depth.json").read_text())
    gold = {
        "s": frozenset(
            [
                *(f"i{i}" for i in range(1, 71)),
                *(f"i{i}" for i in range(101, 114)),
                *(f"m{i}" for i in range(56)),
            ]
        )
    }
    gold.update({f"p{i}": frozenset({f"missing{i}"}) for i in range(224)})
    survivors = {key: candidates(200) if key == "s" else () for key in gold}
    iris = frozenset().union(*gold.values())
    report = attribute_relations(
        survivors,
        {},
        gold,
        iris,
        {key: [dict.fromkeys(values, 0.6)] * 3 for key, values in gold.items()},
        dict.fromkeys(gold, "brief"),
    )
    report["fingerprint"] = {
        key: value for key, value in expected.items() if key.endswith("_sha256")
    }
    return report, expected, survivors, gold


@pytest.mark.parametrize(
    "key",
    [
        "corpus_content_sha256",
        "nomatch_content_sha256",
        "ontology_cache_sha256",
        "answer_rule_config_sha256",
        "adapter_sha256",
    ],
)
def test_review_provenance_fails_closed(key):
    from folio_eval.recall_attribution import reconcile_depth

    report, expected, survivors, gold = reconciled_fixture()
    reconcile_depth(survivors, gold, expected, report["fingerprint"])
    report["fingerprint"][key] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        reconcile_depth(survivors, gold, expected, report["fingerprint"])


def test_review_verify_finalized_read_only(tmp_path, monkeypatch):
    import hashlib
    import json

    from folio_eval import recall_attribution as module

    report, expected, _, _ = reconciled_fixture()
    adapter_hash = report["fingerprint"].pop("adapter_sha256")
    report["fingerprint"]["git_head"] = "a" * 40
    adapter = Path("eval/folio_eval/synthetic_score.py").read_bytes()
    assert hashlib.sha256(adapter).hexdigest() == adapter_hash
    calls = []

    def git_show(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=adapter)

    monkeypatch.setattr(module.subprocess, "run", git_show)
    path = tmp_path / "attribution.json"
    path.write_text(json.dumps(report))
    before = path.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    module.verify_finalized_attribution(path, expected, digest)
    assert path.read_bytes() == before
    assert calls == [["git", "show", "a" * 40 + ":eval/folio_eval/synthetic_score.py"]]
    expected["scoreable_item_count"] += 1
    with pytest.raises(ValueError, match="population"):
        module.verify_finalized_attribution(path, expected, digest)


def test_review_verify_cli_does_not_collect(runner, monkeypatch):
    import hashlib
    import json

    runner.module.main(runner.args)
    path = runner.root / "checkpoint/attribution.json"
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("reverification must not collect or require a pristine current tree")

    monkeypatch.setattr(runner.module, "collect_checkpoint", forbidden)
    monkeypatch.setattr(runner.module, "build_checkpoint_fingerprint", forbidden)
    args = [
        "--verify-finalized",
        str(path),
        "--leak-manifest",
        "unused",
        "--salt-file",
        str(runner.root / "salt"),
    ]
    assert runner.module.main(args) == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(before).hexdigest()
    value = json.loads(before)
    value["fingerprint"]["corpus_content_sha256"] = "0" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="SHA-256"):
        runner.module.main(args)
