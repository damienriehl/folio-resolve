"""Historical receipts bind current behavior, not unrelated source bytes."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from folio_resolve import MatchPipeline

ROOT = Path(__file__).parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "benchmarks" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offline(module):
    return load("evidence_replay").offline(module)


def historical_validators(module):
    """Test original validator rules with replay bindings, including synthetic inputs.

    This is deliberately not the public frozen-evidence authentication entry point.
    """
    return load("evidence_replay")._offline(module)


def receipt(name):
    return json.loads((ROOT / "docs/benchmarks" / f"{name}.json").read_text())


@pytest.mark.parametrize(
    "name",
    [
        "embedding_semantic_gate_replay",
        "embedding_gate_ablation",
        "embedding_precision",
        "embedding_precision_relations",
        "embedding_lexical_boundaries",
    ],
)
def test_unrelated_library_edit_preserves_frozen_evidence(name, monkeypatch):
    module = load(name)
    if name == "embedding_gate_ablation":
        replay = load("embedding_semantic_gate_replay")
        replay.ablation = module
        module = replay
    replay = module
    while hasattr(replay, "precision"):
        replay = replay.precision
    if hasattr(replay, "replay"):
        replay = replay.replay
    monkeypatch.setattr(replay, "source_identity", lambda: "a" * 64)
    view = offline(module)
    if name in ("embedding_semantic_gate_replay", "embedding_gate_ablation"):
        assert view.run_replay() == receipt("embedding-semantic-gate-replay")
    elif name == "embedding_precision":
        view.validate_collection(receipt("embedding-precision-collection"))
    elif name == "embedding_precision_relations":
        data = receipt("embedding-precision-collection")
        view.run_score(
            data,
            receipt("embedding-precision-judgments"),
            load("embedding_lexical_boundaries").OWNER_RECEIPT,
        )
    else:
        view.validate_collection(receipt("embedding-lexical-boundaries-collection"))
    assert replay.source_identity() == "a" * 64


def test_behavioral_drift_names_recorded_and_current_sources(monkeypatch):
    module = load("embedding_semantic_gate_replay")
    recorded = receipt("embedding-semantic-gate-replay")["provenance"]["library_source_sha256"]
    monkeypatch.setattr(module, "source_identity", lambda: "b" * 64)
    monkeypatch.setattr(MatchPipeline, "_rank", lambda *args, **kwargs: [])
    with pytest.raises(ValueError, match="library source drift changed replayed results") as error:
        offline(module).run_replay()
    assert recorded in str(error.value)
    assert "b" * 64 in str(error.value)


@pytest.mark.parametrize("rehash", [False, True])
def test_library_digest_tampering_still_fails(rehash):
    module = load("embedding_precision")
    data = copy.deepcopy(receipt("embedding-precision-collection"))
    data["provenance"]["library_source_sha256"] = "c" * 64
    if rehash:
        data["configuration_sha256"] = module.baseline.stable_digest(data["provenance"])
    with pytest.raises(ValueError, match="provenance"):
        offline(module).validate_collection(data)


def test_offline_view_keeps_collection_and_cli_strict(monkeypatch):
    module = load("embedding_precision")
    monkeypatch.setattr(module.replay, "source_identity", lambda: "d" * 64)
    view = offline(module)
    view.validate_collection(receipt("embedding-precision-collection"))
    assert view.run_collect is module.run_collect
    assert view.main is module.main
    assert view.verify_pins is module.verify_pins
    assert module.replay.source_identity() == "d" * 64
    with pytest.raises(ValueError, match="library_source_sha256"):
        module.replay.run_replay()
    with pytest.raises(ValueError, match="library_source_sha256"):
        view.run_collect(Path("unused.owl"), None)


def test_lexical_scoring_drift_is_reported(monkeypatch):
    module = load("embedding_lexical_boundaries")
    monkeypatch.setattr(module.precision.replay, "source_identity", lambda: "e" * 64)
    monkeypatch.setattr(module, "concept_score", lambda *args: -1)
    with pytest.raises(ValueError, match="library source drift changed replayed results") as error:
        offline(module).validate_collection(receipt("embedding-lexical-boundaries-collection"))
    assert "e" * 64 in str(error.value)


def test_semantic_retrieval_drift_names_both_sources(monkeypatch):
    module = load("embedding_lexical_boundaries")
    data = receipt("embedding-lexical-boundaries-collection")
    recorded = data["provenance"]["library_source_sha256"]
    monkeypatch.setattr(module.precision.replay, "source_identity", lambda: "f" * 64)
    expand = MatchPipeline._expand

    def changed_window(pipeline, query, *args, **kwargs):
        pipeline.semantic_index.query(query, top_k=6)
        return expand(pipeline, query, *args, **kwargs)

    monkeypatch.setattr(MatchPipeline, "_expand", changed_window)
    with pytest.raises(ValueError, match="library source drift changed replayed results") as error:
        offline(module).validate_collection(data)
    assert recorded in str(error.value)
    assert "f" * 64 in str(error.value)


def test_selective_drift_with_replaced_receipt_is_rejected(tmp_path, monkeypatch):
    import shutil

    module = load("embedding_semantic_gate_replay")
    shutil.copytree(ROOT / "docs/benchmarks", tmp_path / "docs/benchmarks")
    shutil.copytree(ROOT / "benchmarks/fixtures", tmp_path / "benchmarks/fixtures")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    original_rank = MatchPipeline._rank

    def changed_rank(pipeline, *args, **kwargs):
        result = original_rank(pipeline, *args, **kwargs)
        if isinstance(pipeline, module.SelectivePipeline):
            for candidate in result:
                candidate.score += 0.125
        return result

    monkeypatch.setattr(MatchPipeline, "_rank", changed_rank)
    recorded = receipt("embedding-semantic-gate-replay")["provenance"]["library_source_sha256"]
    monkeypatch.setattr(module, "source_identity", lambda: recorded)
    changed = module.run_replay()
    assert changed != receipt("embedding-semantic-gate-replay")
    (tmp_path / "docs/benchmarks/embedding-semantic-gate-replay.json").write_text(
        json.dumps(changed)
    )
    with pytest.raises(ValueError, match="Frozen artifact SHA-256"):
        offline(module).run_replay()


@pytest.mark.parametrize("replace_file", [False, True])
def test_rehashed_pool_metadata_is_rejected(tmp_path, monkeypatch, replace_file):
    import shutil

    module = load("embedding_precision")
    shutil.copytree(ROOT / "docs/benchmarks", tmp_path / "docs/benchmarks")
    shutil.copytree(ROOT / "benchmarks/fixtures", tmp_path / "benchmarks/fixtures")
    monkeypatch.setattr(module.replay, "ROOT", tmp_path)
    path = tmp_path / "docs/benchmarks/embedding-precision-collection.json"
    data = json.loads(path.read_text())
    data["pool"][0]["definition"] = "TAMPERED DEFINITION"
    data["pool_sha256"] = module.baseline.stable_digest(data["pool"])
    if replace_file:
        path.write_text(json.dumps(data))
    message = "Frozen artifact SHA-256" if replace_file else "authenticated frozen"
    with pytest.raises(ValueError, match=message):
        offline(module).validate_collection(data)


def test_pin_table_covers_offline_reads(monkeypatch):
    adapter = load("evidence_replay")
    opened = set()
    original_open = Path.open

    def track(path, *args, **kwargs):
        if (
            path.is_relative_to(ROOT)
            and path.suffix == ".json"
            and path.relative_to(ROOT).parts[0] in {"benchmarks", "docs"}
        ):
            opened.add(path.relative_to(ROOT).as_posix())
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track)
    assert adapter.offline(load("embedding_semantic_gate_replay")).run_replay() == receipt(
        "embedding-semantic-gate-replay"
    )
    adapter.offline(load("embedding_precision")).validate_collection(
        receipt("embedding-precision-collection")
    )
    module = load("embedding_precision_relations")
    adapter.offline(module).run_score(
        receipt("embedding-precision-collection"),
        receipt("embedding-precision-judgments"),
        load("embedding_lexical_boundaries").OWNER_RECEIPT,
    )
    adapter.offline(load("embedding_lexical_boundaries")).run_score(
        receipt("embedding-lexical-boundaries-collection")
    )
    assert not (opened - adapter.FROZEN_SHA256.keys()), opened - adapter.FROZEN_SHA256.keys()


@pytest.mark.parametrize("relative", sorted(load("evidence_replay").FROZEN_SHA256))
def test_every_frozen_input_is_authenticated_before_replay(relative, tmp_path, monkeypatch):
    import shutil

    module = load("embedding_semantic_gate_replay")
    shutil.copytree(ROOT / "docs/benchmarks", tmp_path / "docs/benchmarks")
    shutil.copytree(ROOT / "benchmarks/fixtures", tmp_path / "benchmarks/fixtures")
    (tmp_path / relative).write_text("not even JSON")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("Replay ran before all frozen inputs were authenticated")

    monkeypatch.setattr(module, "replay_controls", forbidden)
    with pytest.raises(ValueError, match="Frozen artifact SHA-256"):
        offline(module).run_replay()


def test_nested_offline_view_authenticates_dictionary_inputs():
    module = load("embedding_lexical_boundaries")
    data = receipt("embedding-precision-collection")
    data["pool"][0]["definition"] = "TAMPERED DEFINITION"
    data["pool_sha256"] = module.baseline.stable_digest(data["pool"])
    with pytest.raises(ValueError, match="authenticated frozen"):
        offline(module).precision.validate_collection(collection=data)
