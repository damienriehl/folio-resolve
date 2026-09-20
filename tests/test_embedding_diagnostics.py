"""Observe actual pipeline decisions and index ranks without substituting them."""

import importlib.util
from dataclasses import asdict
from pathlib import Path

import pytest

from folio_resolve import Concept, InMemoryOntology, MatchPipeline
from folio_resolve.embedding import BruteForceIndex

_SPEC = importlib.util.spec_from_file_location(
    "embedding_diagnostics", Path(__file__).parents[1] / "benchmarks/embedding_diagnostics.py"
)
assert _SPEC is not None and _SPEC.loader is not None
diagnostics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(diagnostics)


class FixedProvider:
    """Known vectors isolate retrieval order from model quality."""

    def dimension(self):
        return 2

    def embed(self, text):
        return [1.0, 0.0]

    def embed_batch(self, texts):
        return [[0.8 - i * 0.05, 0.6 + i * 0.05] for i, _ in enumerate(texts)]


def pipeline_for(labels):
    concepts = [Concept(iri=f"test:{i}", label=label) for i, label in enumerate(labels)]
    index = BruteForceIndex(FixedProvider())
    index.build([c.iri for c in concepts], labels, [None] * len(labels))
    return MatchPipeline(InMemoryOntology(concepts), semantic_index=index), concepts


def test_target_outside_window_is_not_misreported_as_gated():
    pipeline, concepts = pipeline_for([f"Remote Concept {i}" for i in range(6)])
    trace = diagnostics.trace_query(pipeline, "zzzz yyyy", ["test:5"], len(concepts))
    target = trace["targets"][0]
    assert target["semantic_rank"] == 6
    assert target["outcome"] == "not_retrieved"
    assert target["retrievals"] == []
    assert target["semantic_cosine"] < trace["semantic_top_five"][0]["cosine"]


def test_real_short_gate_demotes_retrieved_target_and_exact_survives():
    pipeline, concepts = pipeline_for(["Negligence"])
    trace = diagnostics.trace_query(pipeline, "zzzz yyyy", ["test:0"], len(concepts))
    target = trace["targets"][0]
    assert target["semantic_rank"] == 1
    assert target["outcome"] == "demoted_below_floor"
    assert target["retrievals"][0]["before"]["score"] == 80.0
    assert target["retrievals"][0]["after"]["score"] == 40.0
    exact = diagnostics.trace_query(pipeline, "Negligence", ["test:0"], len(concepts))
    assert exact["targets"][0]["outcome"] == "survived"
    assert {r["outcome"] for r in exact["retrievals"]} == {"survived", "deduplicated"}
    assert exact["candidates"] == [asdict(c) for c in pipeline.match("Negligence")]


def test_actual_blocklist_decision_is_recorded():
    pipeline, concepts = pipeline_for(["Negligence"])
    pipeline.blocklist.block("Negligence", "test:0")
    trace = diagnostics.trace_query(pipeline, "Negligence", ["test:0"], len(concepts))
    assert trace["targets"][0]["outcome"] == "blocked"
    assert {r["outcome"] for r in trace["retrievals"]} == {"blocked"}


def test_timing_observation_preserves_results_and_exclusive_accounting():
    pipeline, _ = pipeline_for(["Negligence"])
    expected = [asdict(c) for c in pipeline.match("Negligence")]
    candidates, times = diagnostics.timed_match(pipeline, "Negligence")
    assert candidates == expected
    assert times["lexical_calls"] == times["semantic_calls"] == 1
    assert times["semantic_seconds"] >= times["embedding_seconds"] > 0
    assert times["other_seconds"] >= 0
    assert times["total_seconds"] == pytest.approx(
        times["lexical_seconds"] + times["semantic_seconds"] + times["other_seconds"]
    )
    assert [asdict(c) for c in pipeline.match("Negligence")] == expected


def test_disabled_trace_and_timing_have_no_semantic_measurement():
    pipeline = MatchPipeline(InMemoryOntology([Concept(iri="x", label="Negligence")]))
    trace = diagnostics.trace_query(pipeline, "Negligence", ["x"], 1)
    assert trace["targets"][0]["semantic_rank"] is None
    assert trace["targets"][0]["outcome"] == "survived"
    _, times = diagnostics.timed_match(pipeline, "Negligence")
    assert times["semantic_calls"] == times["semantic_seconds"] == times["embedding_seconds"] == 0


def test_rejects_wrong_imported_library_before_loading_any_data(tmp_path, monkeypatch):
    import folio_resolve

    monkeypatch.setattr(folio_resolve, "__file__", str(tmp_path / "site-packages/folio_resolve.py"))
    with pytest.raises(ValueError, match="imported folio_resolve"):
        diagnostics.run_diagnostics(tmp_path / "absent.owl", "disabled", None, 1)


def test_rejects_zero_repeats_before_loading_data(tmp_path):
    with pytest.raises(ValueError, match="repeats must be positive"):
        diagnostics.run_diagnostics(tmp_path / "absent.owl", "disabled", None, 0)


def test_lexical_target_survives_despite_being_outside_semantic_window():
    pipeline, concepts = pipeline_for([f"Remote Concept {i}" for i in range(12)])
    # The target is retrieved lexically despite being outside the semantic window.
    trace = diagnostics.trace_query(pipeline, "Remote Concept 11", ["test:11"], len(concepts))
    assert trace["targets"][0]["semantic_rank"] == 12
    assert trace["targets"][0]["outcome"] == "survived"
    assert trace["targets"][0]["retrievals"][0]["before"]["extraction_path"] == "label_search"


def test_score_already_below_floor_is_not_attributed_to_gate():
    class LowQueryProvider(FixedProvider):
        def embed(self, text):
            return [-1.0, 0.0]

    concepts = [Concept(iri="x", label="Remote Concept")]
    index = BruteForceIndex(LowQueryProvider())
    index.build(["x"], ["Remote Concept"], [None])
    pipeline = MatchPipeline(InMemoryOntology(concepts), semantic_index=index)
    trace = diagnostics.trace_query(pipeline, "zzzz yyyy", ["x"], 1)
    assert trace["targets"][0]["outcome"] == "below_floor"
    assert trace["targets"][0]["retrievals"][0]["before"]["score"] == -80.0
