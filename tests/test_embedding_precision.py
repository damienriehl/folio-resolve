"""Approval-bound paired collection exercises actual ranking."""

import importlib.util
from dataclasses import asdict
from pathlib import Path

from folio_resolve import InMemoryOntology, MatchCandidate, MatchPipeline


def runner():
    spec = importlib.util.spec_from_file_location(
        "embedding_precision", Path(__file__).parents[1] / "benchmarks/embedding_precision.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_characterize_real_rank():
    pipe = MatchPipeline(InMemoryOntology([]))
    c = MatchCandidate(
        "target", "Negligence", 80, surface_term="careless conduct", extraction_path="semantic"
    )
    assert pipe._rank([c], domains=[], heading_terms=set()) == []
    assert c.score == 40


def test_paired_real_rank_preserves_inputs_and_semantic_only():
    mod = runner()
    pipe = MatchPipeline(InMemoryOntology([]))
    inputs = [
        asdict(
            MatchCandidate(
                "target", "Negligence", 80, surface_term="careless conduct", extraction_path=path
            )
        )
        for path in ("label_search", "semantic")
    ]
    result = mod.rank_pair(pipe, inputs, ["target"])
    assert result["baseline"]["candidates"] == []
    assert result["selective"]["candidates"][0]["extraction_path"] == "semantic"
    assert inputs[0]["score"] == inputs[1]["score"] == 80
    assert result["baseline"]["candidates_after"][1]["score"] == 40
    assert result["selective"]["candidates_after"][1]["score"] == 80


def test_owner_approval_binds_sixteen_cases():
    mod = runner()
    fixture = mod.load_fixtures()
    assert len(fixture["cases"]) == 16
    assert fixture["approval"]["decision"] == "approve all eight"
    assert fixture["approval"]["payload_sha256"] == mod.baseline.stable_digest(
        mod.approval_payload(fixture)
    )


def test_reject_altered_approval_and_invalid_duplicate_targets(tmp_path):
    import copy
    import json

    import pytest

    mod = runner()
    original = mod.load_fixtures()
    for alteration, message in [
        (lambda f: f.pop("approval"), "Approval"),
        (lambda f: f["cases"][-1].update(query="different query"), "Approval"),
        (
            lambda f: f["cases"][0]["acceptable_iris"].append(f["cases"][0]["acceptable_iris"][0]),
            "Duplicate",
        ),
        (lambda f: f["cases"][0]["acceptable_iris"].append("bad"), "Invalid"),
        (lambda f: f["approval"].update(decision="approve"), "Approved"),
    ]:
        fixture = copy.deepcopy(original)
        alteration(fixture)
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(fixture))
        with pytest.raises(ValueError, match=message):
            mod.load_fixtures(path)
    altered = copy.deepcopy(original)
    altered["cases"][-1]["query"] = "changed"
    altered["approval"]["payload_sha256"] = mod.baseline.stable_digest(
        mod.approval_payload(altered)
    )
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="Approved"):
        mod.load_fixtures(path)


def test_transfer_configuration_and_real_guard_dedup_ties():
    from folio_resolve import Concept
    from folio_resolve.gates import ShortLabelGate

    mod = runner()
    pipe = MatchPipeline(
        InMemoryOntology([Concept("actual", "Actual")]),
        score_floor=47,
        short_gate=ShortLabelGate(near_exact_threshold=97),
    )
    selective = mod.selective_from(pipe)
    assert selective.ontology is pipe.ontology
    assert selective.place_gate is pipe.place_gate
    assert selective.blocklist is pipe.blocklist
    assert selective.score_floor == 47
    assert selective.short_gate._near_exact == 97
    pipe.blocklist.block("care", "blocked")

    def c(iri, score=80, path="semantic", branch=""):
        return asdict(
            MatchCandidate(
                iri, "Negligence", score, surface_term="care", extraction_path=path, branch=branch
            )
        )

    inputs = [
        c("blocked"),
        c("place", branch="Location"),
        c("low", 46),
        c("same", 80, "label_search"),
        c("same"),
        c("a"),
        c("b"),
        c("unknown", path="unknown"),
    ]
    result = mod.rank_pair(pipe, inputs)
    assert result["baseline"]["candidates"] == []
    assert [c["iri"] for c in result["selective"]["candidates"]] == ["a", "b", "same"]
    assert result["selective"]["candidates_after"][1]["score"] == 40
    tie = [c("same", 99, "label_search"), c("same", 99)]
    assert (
        mod.rank_pair(pipe, tie)["selective"]["candidates"][0]["extraction_path"] == "label_search"
    )
    assert (
        mod.rank_pair(pipe, tie[::-1])["selective"]["candidates"][0]["extraction_path"]
        == "semantic"
    )


def test_retrieve_once_real_pipeline_and_rank_restoration():
    from folio_resolve import Concept

    mod = runner()

    class Index:
        calls = 0

        def query(self, query, top_k):
            self.calls += 1
            assert top_k == 5
            return [("target", "Negligence", 0.8)]

    index = Index()
    pipe = MatchPipeline(InMemoryOntology([Concept("target", "Negligence")]), semantic_index=index)
    before = pipe._rank
    inputs = mod.retrieve_once(pipe, "careless conduct")
    assert index.calls == 1 and pipe._rank == before
    result = mod.rank_pair(pipe, inputs)
    assert index.calls == 1
    assert result["selective"]["candidates"][0]["iri"] == "target"


def test_pool_blinding_dedup_missing_definitions_and_bindings():
    import pytest

    from folio_resolve import Concept

    mod = runner()
    concepts = [
        Concept(
            "b",
            "B",
            definition="definition",
            alternative_labels=("Alias",),
            parent_iris=("parent",),
        ),
        Concept("a", "A"),
    ]
    results = [
        {
            "id": "Q",
            "query": "query",
            "baseline": {"candidates": [{"iri": "b"}, {"iri": "a"}]},
            "selective": {"candidates": [{"iri": "a"}]},
        }
    ]
    pool = mod.candidate_pool(results, concepts)
    assert [p["iri"] for p in pool] == ["a", "b"]
    assert pool[0]["definition"] is None
    assert pool[1]["aliases"] == ["Alias"] and pool[1]["parents"] == ["parent"]
    assert set(pool[0]) == {"query_id", "query", "iri", "label", "definition", "aliases", "parents"}
    collection = {
        "fixture": mod.load_fixtures(),
        "pool": pool,
        "pool_sha256": mod.baseline.stable_digest(pool),
    }
    sheet = mod.prepare_judgments(collection)
    assert sheet["collection_sha256"] == mod.baseline.stable_digest(collection)
    assert sheet["approval"] is None
    assert all(p["judgment"] is None and p["rationale"] is None for p in sheet["judgments"])
    assert "precision" not in collection
    with pytest.raises(ValueError, match="Duplicate corpus"):
        mod.candidate_pool(results, concepts + concepts)
    with pytest.raises(ValueError, match="missing"):
        mod.candidate_pool(results, concepts[:1])
    results[0]["selective"]["candidates"] *= 2
    with pytest.raises(ValueError, match="Duplicate ranked"):
        mod.candidate_pool(results, concepts)


def test_actual_frozen_controls_and_drift():
    import copy
    import json

    import pytest

    mod = runner()
    frozen = json.loads(mod.CONTROL_PATH.read_text())
    assert mod.baseline.file_digest(mod.CONTROL_PATH) == mod.CONTROL_SHA256
    controls = frozen["variants"]["local"]["results"]
    results = []
    pipe = MatchPipeline(InMemoryOntology([]))
    for case in controls:
        result = {k: case[k] for k in ("id", "kind", "query", "acceptable_iris", "expected_labels")}
        result.update(
            mod.rank_pair(pipe, case["baseline"]["candidate_inputs"], case["acceptable_iris"])
        )
        results.append(result)
    mod.verify_controls(results, controls)
    bad = copy.deepcopy(results)
    bad[0]["selective"]["candidates"][0]["score"] -= 1
    with pytest.raises(ValueError, match="snapshot"):
        mod.verify_controls(bad, controls)
    with pytest.raises(ValueError, match="count"):
        mod.verify_controls(results[:-1], controls)
    bad = copy.deepcopy(results)
    bad[0]["query"] = "changed"
    with pytest.raises(ValueError, match="case"):
        mod.verify_controls(bad, controls)


def test_controls_fail_before_new_retrieval(monkeypatch):
    import pytest

    mod = runner()
    calls = []
    case = {
        "id": "Q",
        "kind": "negative",
        "query": "query",
        "acceptable_iris": [],
        "expected_labels": {},
    }
    pipe = MatchPipeline(InMemoryOntology([]))
    expected = {**case, **mod.rank_pair(pipe, [])}
    expected["baseline"]["candidates_after"] = ["wrong"]

    def retrieve(pipeline, query):
        calls.append(query)
        return []

    monkeypatch.setattr(mod, "retrieve_once", retrieve)
    with pytest.raises(ValueError, match="snapshot"):
        mod.collect_cases(
            pipe, {"cases": [case, {**case, "id": "new", "query": "new"}]}, [expected]
        )
    assert calls == ["query"]


def test_pin_validation_source_model_and_corpus(monkeypatch):
    import copy

    import pytest

    mod = runner()
    fixture = mod.load_fixtures()
    monkeypatch.setattr(mod.baseline, "validate_answers", lambda *args: None)
    provenance = {
        "library_source_sha256": mod.replay.source_identity(),
        "fixture_sha256": mod.baseline.stable_digest(mod.baseline.load_fixtures()),
        "corpus_sha256": mod.baseline.corpus_digest([]),
        "corpus_policy": mod.baseline.CORPUS_POLICY,
        "concept_count": 0,
        "ontology": fixture["ontology"],
        "model": fixture["model"],
        "model_files_sha256": {"file": "hash"},
    }
    mod.verify_pins(fixture, [], {"provenance": provenance}, {"file": "hash"})
    for key in provenance:
        bad = copy.deepcopy(provenance)
        bad[key] = "different"
        with pytest.raises(ValueError, match=key):
            mod.verify_pins(fixture, [], {"provenance": bad}, {"file": "hash"})


def test_pool_depth_is_per_arm_not_union_and_is_order_independent():
    from folio_resolve import Concept

    mod = runner()
    concepts = [Concept(str(i), f"Concept {i}") for i in range(8)]
    results = [
        {
            "id": "Q",
            "query": "query",
            "baseline": {"candidates": [{"iri": str(i)} for i in range(6)]},
            "selective": {"candidates": [{"iri": str(i)} for i in range(2, 8)]},
        }
    ]
    pool = mod.candidate_pool(results, concepts)
    assert [p["iri"] for p in pool] == list("0123456")
    results[0]["baseline"], results[0]["selective"] = (
        results[0]["selective"],
        results[0]["baseline"],
    )
    assert mod.candidate_pool(results, concepts[::-1]) == pool


def test_retrieval_failure_restores_real_rank():
    import pytest

    mod = runner()

    class BrokenIndex:
        def query(self, *args, **kwargs):
            raise RuntimeError("retrieval probe")

    pipe = MatchPipeline(InMemoryOntology([]), semantic_index=BrokenIndex())
    original = pipe._rank
    with pytest.raises(RuntimeError, match="retrieval probe"):
        mod.retrieve_once(pipe, "query")
    assert pipe._rank == original
