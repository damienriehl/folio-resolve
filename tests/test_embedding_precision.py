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


def scoring_cases():
    def case(qid, kind, left, right):
        return {
            "id": qid,
            "kind": kind,
            "query": qid,
            "acceptable_iris": ["r"] if kind != "negative" else [],
            **{
                arm: {"candidates": [dict(iri=i, extraction_path="semantic") for i in iris]}
                for arm, iris in zip(("baseline", "selective"), (left, right), strict=True)
            },
        }

    return [
        case("P", "exact", ["r", "i", "u"], ["r", "i", "r2"]),
        case("E", "paraphrase", [], []),
        case("N", "negative", ["i", "u"], []),
    ]


def test_scoring_partial_fixed_denominators_and_negative_counts():
    mod = runner()
    labels = {
        ("P", "r"): "relevant",
        ("P", "r2"): "relevant",
        ("P", "i"): "irrelevant",
        ("P", "u"): "uncertain",
        ("N", "i"): "irrelevant",
    }
    result = mod.summarize(scoring_cases(), labels, ["P", "E"])
    positive, empty, negative = result["queries"]
    assert positive["baseline"]["p_at_5"] is None
    assert positive["selective"]["p_at_5"] is None
    assert positive["baseline"]["p_at_5_bounds"] == [0.2, 0.4]
    assert positive["selective"]["p_at_5_bounds"] == [0.4, 0.4]
    assert empty["baseline"]["p_at_5"] == 0
    macro = result["positive_groups"]["combined"]
    assert macro["query_count"] == 2
    assert macro["baseline"]["p_at_5"] is None
    assert macro["baseline"]["p_at_5_bounds"] == [0.1, 0.2]
    assert macro["selective"]["p_at_5_bounds"] == [0.2, 0.2]
    assert negative["baseline"]["returned"] == 2
    assert negative["baseline"]["irrelevant"] == 1
    assert negative["baseline"]["missing"] == 1
    assert "p_at_5" not in negative["baseline"]
    assert positive["added"] == ["r2"] and positive["removed"] == ["u"]


def test_judgment_identity_and_approval_contract():
    import copy

    import pytest

    mod = runner()
    collection = {
        "fixture": mod.load_fixtures(),
        "pool": [
            {
                "query_id": "P",
                "query": "P",
                "iri": "r",
                "label": "R",
                "definition": None,
                "aliases": [],
                "parents": [],
            }
        ],
    }
    collection["pool_sha256"] = mod.baseline.stable_digest(collection["pool"])
    sheet = mod.prepare_judgments(collection)
    assert mod.validate_judgments(collection, sheet) == {("P", "r"): None}
    for mutate in [
        lambda s: s["judgments"].clear(),
        lambda s: s["judgments"].append(copy.deepcopy(s["judgments"][0])),
        lambda s: s["judgments"][0].update(label="Changed"),
        lambda s: s.update(rubric="Changed"),
    ]:
        bad = copy.deepcopy(sheet)
        mutate(bad)
        with pytest.raises(ValueError):
            mod.validate_judgments(collection, bad)
    sheet["judgments"][0]["judgment"] = "relevant"
    with pytest.raises(ValueError, match="approval"):
        mod.validate_judgments(collection, sheet)


def test_full_scoring_empty_and_one_result_and_path_identity():
    mod = runner()
    cases = scoring_cases()
    cases[0]["baseline"]["candidates"] = [{"iri": "r", "extraction_path": "label_search"}]
    cases[0]["selective"]["candidates"] = [{"iri": "r", "extraction_path": "semantic"}]
    labels = {("P", "r"): "relevant", ("N", "i"): "irrelevant", ("N", "u"): "irrelevant"}
    result = mod.summarize(cases, labels, ["P", "E"])
    assert result["queries"][0]["baseline"]["p_at_5"] == 0.2
    assert result["positive_groups"]["combined"]["baseline"]["p_at_5"] == 0.1
    assert result["queries"][0]["winning_path_changes"] == [
        {"iri": "r", "baseline": "label_search", "selective": "semantic"}
    ]
    assert result["queries"][0]["added"] == []
    assert result["positive_groups"]["new"]["query_count"] == 0
    assert result["positive_groups"]["new"]["baseline"]["p_at_5_bounds"] is None
    assert result["queries"][2]["selective"]["returned"] == 0
    assert "p_at_5" not in result["queries"][2]["selective"]


def test_negative_relevance_requires_owner_resolution():
    import pytest

    mod = runner()
    with pytest.raises(ValueError, match=r"Annotation conflict.*owner resolution"):
        mod.summarize(scoring_cases(), {("N", "i"): "relevant"}, ["P", "E"])


def offline_collection(mod):
    """Real frozen controls + approved new cases with explicitly empty synthetic retrievals."""
    import json

    from folio_resolve import Concept

    fixture = mod.load_fixtures()
    frozen = json.loads(mod.CONTROL_PATH.read_text())["variants"]["local"]["results"]
    results = [
        {**case, **{arm: control[arm] for arm in mod.ARMS}}
        for case, control in zip(fixture["cases"][:8], frozen, strict=True)
    ]
    pipe = MatchPipeline(InMemoryOntology([]))
    results.extend(
        {**case, **mod.rank_pair(pipe, [], case["acceptable_iris"])}
        for case in fixture["cases"][8:]
    )
    concepts = {
        c["iri"]: Concept(c["iri"], c["label"])
        for result in results
        for arm in mod.ARMS
        for c in result[arm]["candidates"]
    }
    pool = mod.candidate_pool(results, list(concepts.values()))
    frozen_p = json.loads((mod.ROOT / "docs/benchmarks/embedding-baseline-local.json").read_text())[
        "provenance"
    ]
    p = {
        key: frozen_p[key]
        for key in (
            "library_source_sha256",
            "corpus_sha256",
            "corpus_policy",
            "concept_count",
            "ontology",
            "model",
            "model_files_sha256",
            "pipeline",
        )
    }
    p.update(
        measurement="one pinned local retrieval per query; paired real ranking of fresh copies",
        source_sha256=mod.source_hashes(),
        control_artifact_sha256=mod.CONTROL_SHA256,
        fixture_sha256=mod.baseline.stable_digest(fixture),
        approval_payload_sha256=fixture["approval"]["payload_sha256"],
        ranking_context={"domains": [], "heading_terms": [], "context_text": None},
        pool_depth=5,
    )
    return dict(
        schema_version=1,
        fixture=fixture,
        provenance=p,
        configuration_sha256=mod.baseline.stable_digest(p),
        results=results,
        pool=pool,
        pool_sha256=mod.baseline.stable_digest(pool),
        controls_reproduced=True,
    )


def approve_test_sheet(mod, sheet):
    sheet["approval"] = dict(
        owner="Test owner",
        date="2026-09-20",
        decision="Test judgments",
        decision_reference="Test-only controlled fixture, not a real owner decision",
        payload_sha256=mod.baseline.stable_digest(mod.judgment_payload(sheet)),
    )
    return mod.baseline.stable_digest(sheet["approval"])


def test_offline_scoring_integration_and_fixed_approved_strata(monkeypatch):
    mod = runner()
    collection = offline_collection(mod)
    sheet = mod.prepare_judgments(collection)

    def forbidden(*args, **kwargs):
        raise AssertionError("Scoring must not load or retrieve")

    monkeypatch.setattr(mod.baseline, "build_pipeline", forbidden)
    monkeypatch.setattr(mod.baseline, "load_corpus", forbidden)
    monkeypatch.setattr(mod, "retrieve_once", forbidden)
    result = mod.run_score(collection, sheet)
    assert result["coverage"] == dict(
        pooled_pairs=23, relevant=0, irrelevant=0, uncertain=0, missing=23
    )
    assert {key: row["query_count"] for key, row in result["positive_groups"].items()} == {
        "combined": 12,
        "original": 6,
        "new": 6,
        "exact": 6,
        "paraphrase": 4,
        "geographic": 2,
    }
    assert result["positive_groups"]["original"]["baseline"]["target_hits_at_5"] == 2
    assert result["positive_groups"]["original"]["selective"]["target_hits_at_5"] == 3
    assert result["positive_groups"]["combined"]["baseline"]["p_at_5"] is None
    assert result["negative_query_count"] == 4
    for row in sheet["judgments"]:
        row["judgment"] = "irrelevant"
    receipt = approve_test_sheet(mod, sheet)
    result = mod.run_score(collection, sheet, receipt)
    assert result["positive_groups"]["combined"]["baseline"]["p_at_5"] == 0
    assert result["coverage"]["missing"] == 0


def test_collection_drift_rejected_before_scoring():
    import copy

    import pytest

    mod = runner()
    collection = offline_collection(mod)
    mutations = [
        lambda c: c["provenance"].update(source_sha256={}),
        lambda c: c["provenance"].update(model={}),
        lambda c: c["provenance"].update(corpus_sha256="changed"),
        lambda c: c["provenance"]["pipeline"].update(score_floor=40),
        lambda c: c["fixture"]["approval"].update(decision="copied"),
        lambda c: c["results"].pop(),
        lambda c: c["results"][-1].update(query="changed"),
        lambda c: c["pool"].pop(),
        lambda c: c["pool"].append(copy.deepcopy(c["pool"][0])),
        lambda c: c.update(configuration_sha256="changed"),
        lambda c: c.update(pool_sha256="changed"),
        lambda c: c["results"][0]["baseline"]["candidates"][0].update(score=1),
    ]
    for mutate in mutations:
        bad = copy.deepcopy(collection)
        mutate(bad)
        with pytest.raises(ValueError):
            mod.run_score(bad, mod.prepare_judgments(bad))


def test_receipt_binding_and_all_judgment_failure_modes():
    import copy

    import pytest

    mod = runner()
    collection = offline_collection(mod)
    sheet = mod.prepare_judgments(collection)
    sheet["judgments"][0]["judgment"] = "uncertain"
    receipt = approve_test_sheet(mod, sheet)
    assert mod.validate_judgments(collection, sheet, receipt)
    for mutate in [
        lambda s: s["judgments"][0].update(judgment="relevant"),
        lambda s: s["judgments"][0].update(judgment="unknown"),
        lambda s: s["judgments"][0].update(rationale=42),
        lambda s: s["judgments"][0].update(iri="other"),
        lambda s: s["judgments"][1].update(**s["judgments"][0]),
        lambda s: s.update(collection_sha256="changed"),
        lambda s: s.update(pool_sha256="changed"),
        lambda s: s.update(rubric_sha256="changed"),
        lambda s: s["approval"].update(decision_reference=""),
        lambda s: s["approval"].update(decision="copied quote"),
    ]:
        bad = copy.deepcopy(sheet)
        mutate(bad)
        with pytest.raises(ValueError):
            mod.validate_judgments(collection, bad, receipt)
    with pytest.raises(ValueError, match="approval"):
        mod.validate_judgments(collection, sheet)
    # Same concept under distinct queries never shares its judgment.
    cases = scoring_cases()
    cases[1]["baseline"]["candidates"] = [{"iri": "r", "extraction_path": "semantic"}]
    result = mod.summarize(cases, {("P", "r"): "relevant"}, ["P", "E"])
    assert result["queries"][1]["baseline"]["missing"] == 1


def test_score_cli_writes_frozen_offline_result(tmp_path, monkeypatch, capsys):
    import json
    import sys

    mod = runner()
    collection = offline_collection(mod)
    sheet = mod.prepare_judgments(collection)
    cpath, jpath, output = (
        tmp_path / name for name in ("collection.json", "judgments.json", "score.json")
    )
    cpath.write_text(json.dumps(collection))
    jpath.write_text(json.dumps(sheet))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "embedding_precision",
            "score",
            "--collection",
            str(cpath),
            "--judgments",
            str(jpath),
            "--output",
            str(output),
        ],
    )
    mod.main()
    scored = json.loads(output.read_text())
    assert scored["collection_sha256"] == mod.baseline.stable_digest(collection)
    assert scored["judgments_sha256"] == mod.baseline.stable_digest(sheet)
    assert json.loads(capsys.readouterr().out)["missing"] == 23
    assert json.loads(cpath.read_text()) == collection
    assert json.loads(jpath.read_text()) == sheet


def test_complete_macro_two_relevant_of_five_and_new_irrelevant_admission():
    mod = runner()
    cases = scoring_cases()
    cases[0]["baseline"]["candidates"] = cases[0]["selective"]["candidates"][:]
    labels = {
        ("P", "r"): "relevant",
        ("P", "r2"): "relevant",
        ("P", "i"): "irrelevant",
        ("N", "i"): "irrelevant",
        ("N", "u"): "uncertain",
    }
    summary = mod.summarize(cases, labels, ["P", "E"])
    assert summary["positive_groups"]["combined"]["baseline"]["p_at_5"] == 0.2
    assert summary["queries"][0]["baseline"]["p_at_5"] == 0.4
    # Remove the irrelevant result from the original arm to test admission attribution.
    cases[0]["baseline"]["candidates"].pop(1)
    summary = mod.summarize(cases, labels, ["P", "E"])
    assert summary["queries"][0]["new_irrelevant_pairs"] == ["i"]
    assert summary["queries"][0]["baseline"]["irrelevant"] == 0
    assert summary["queries"][0]["selective"]["irrelevant"] == 1
    assert summary["queries"][2]["baseline"]["uncertain"] == 1
    assert summary["queries"][2]["baseline"]["missing"] == 0


def test_new_case_rank_drift_rejected_even_with_rebound_sheet():
    import pytest

    mod = runner()
    collection = offline_collection(mod)
    collection["results"][8]["baseline"]["candidates"].append(
        dict(iri="invented", extraction_path="semantic")
    )
    with pytest.raises(ValueError, match="ranked snapshot"):
        mod.run_score(collection, mod.prepare_judgments(collection))
