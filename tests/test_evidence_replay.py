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
        view.run_score(data, view.prepare_judgments(data))
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
