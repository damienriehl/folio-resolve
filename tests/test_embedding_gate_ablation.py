"""Controlled ablation uses the real retrieval, ranking, and protections."""

import importlib.util
from dataclasses import asdict
from pathlib import Path

from folio_resolve import Concept, InMemoryOntology, MatchPipeline
from folio_resolve.embedding import BruteForceIndex


def runner():
    spec = importlib.util.spec_from_file_location(
        "embedding_gate_ablation",
        Path(__file__).parents[1] / "benchmarks/embedding_gate_ablation.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixedProvider:
    def dimension(self):
        return 2

    def embed(self, text):
        return [1.0, 0.0]

    def embed_batch(self, texts):
        return [[0.8, 0.6] for _ in texts]


def pipeline_for(label="Negligence", semantic=True, branch=""):
    concept = Concept(iri="test:target", label=label, branch=branch)
    index = None
    if semantic:
        index = BruteForceIndex(FixedProvider())
        index.build([concept.iri], [label], [None])
    return MatchPipeline(InMemoryOntology([concept]), semantic_index=index)


def test_characterize_real_pipeline_short_gate_and_exact_dedup():
    pipeline = pipeline_for()
    assert pipeline.semantic_index.query("zzzz yyyy")[0][2] == 0.8
    assert pipeline.match("zzzz yyyy") == []
    exact = pipeline.match("Negligence")
    assert len(exact) == 1
    assert exact[0].score == 99
    assert exact[0].extraction_path == "label_search"


def test_paired_report_recovers_semantic_target_and_preserves_control():
    ablation = runner()
    pipeline = pipeline_for()
    case = {
        "id": "P",
        "kind": "paraphrase",
        "query": "zzzz yyyy",
        "acceptable_iris": ["test:target"],
    }
    result = ablation.compare_case(pipeline, ablation.bypass_pipeline(pipeline), case, 1)
    assert result["baseline"]["candidates"] == []
    assert result["bypass"]["targets"][0]["final_rank"] == 1
    assert result["bypass"]["candidates"][0]["score"] == 80.0
    assert result["deltas"][0]["extraction_path"] == "semantic"
    assert result["deltas"][0]["change"] == "added"
    assert pipeline.match("zzzz yyyy") == []
    assert next(asdict(c) for c in pipeline.match("Negligence"))["score"] == 99


def test_exact_short_label_keeps_strongest_duplicate_in_both_arms():
    ablation = runner()
    pipeline = pipeline_for()
    case = {"id": "E", "kind": "exact", "query": "Negligence", "acceptable_iris": ["test:target"]}
    result = ablation.compare_case(pipeline, ablation.bypass_pipeline(pipeline), case, 1)
    for arm in ("baseline", "bypass"):
        assert len(result[arm]["candidates"]) == 1
        assert result[arm]["candidates"][0]["score"] == 99
        assert result[arm]["candidates"][0]["extraction_path"] == "label_search"
        assert {r["outcome"] for r in result[arm]["retrievals"]} == {"survived", "deduplicated"}
    assert result["deltas"] == []


def test_disabled_lexical_fuzzy_hit_is_newly_eligible_without_semantic_deltas():
    ablation = runner()
    pipe = pipeline_for("Negligence", semantic=False)
    case = {"id": "guard", "query": "negligent", "acceptable_iris": []}
    result = ablation.compare_case(pipe, ablation.bypass_pipeline(pipe), case, 1)
    assert result["baseline"]["candidates"] == []
    assert result["bypass"]["candidates"]
    assert {d["extraction_path"] for d in result["deltas"]} == {"label_search"}


def test_place_blocklist_and_below_floor_still_suppress_in_real_matches():
    ablation = runner()
    place = pipeline_for("Northern Mariana Islands", branch="Location")
    blocked = pipeline_for("Auction")
    blocked.blocklist.block("zzzz yyyy", "test:target")
    low = pipeline_for()
    low.semantic_index._provider.embed = lambda text: [-1.0, 0.0]
    for pipe in (place, blocked, low):
        result = ablation.compare_case(
            pipe,
            ablation.bypass_pipeline(pipe),
            {"query": "zzzz yyyy", "acceptable_iris": ["test:target"]},
            1,
        )
        assert result["baseline"]["candidates"] == result["bypass"]["candidates"] == []
    assert result["bypass"]["targets"][0]["outcome"] == "below_floor"


def test_named_guards_show_short_protection_lost_and_other_protections_intact():
    guards = {g["name"]: g for g in runner().guard_checks()}
    assert guards["short_fuzzy_law"]["protection_lost"]
    assert guards["short_fuzzy_law"]["arms"]["bypass"]["candidates"][0]["score"] == 88
    for name in ("uncorroborated_place", "blocked_action_auction"):
        assert not guards[name]["protection_lost"]
        assert guards[name]["arms"]["bypass"]["candidates"] == []
    assert guards["short_exact_tax"]["arms"]["bypass"]["candidates"]


def test_deltas_identify_path_replacement_and_score_rank_changes():
    ablation = runner()
    old = [
        {"iri": "x", "extraction_path": "label_search", "score": 55},
        {"iri": "y", "extraction_path": "semantic", "score": 50},
    ]
    new = [
        {"iri": "y", "extraction_path": "semantic", "score": 80},
        {"iri": "x", "extraction_path": "semantic", "score": 60},
    ]
    deltas = ablation.candidate_deltas(old, new)
    assert [(d["iri"], d["change"]) for d in deltas] == [
        ("x", "removed"),
        ("x", "added"),
        ("y", "changed"),
    ]
    assert deltas[-1]["score_delta"] == 30
    assert deltas[-1]["baseline_rank"] == 2
    assert deltas[-1]["bypass_rank"] == 1


def small_control(ablation):
    fixture = {
        "ontology": {"sha256": "owl"},
        "model": {},
        "cases": [
            {
                "id": "E",
                "kind": "exact",
                "query": "Tax",
                "acceptable_iris": ["x"],
                "expected_labels": {"x": "Tax"},
            }
        ],
    }
    concepts = [Concept(iri="x", label="Tax")]
    p = {
        "fixture_sha256": ablation.baseline.stable_digest(fixture),
        "corpus_sha256": ablation.baseline.corpus_digest(concepts),
        "library_source_sha256": "source",
        "corpus_policy": ablation.baseline.CORPUS_POLICY,
        "concept_count": 1,
        "variant": "disabled",
        "ontology": fixture["ontology"],
        "model": None,
        "model_files_sha256": None,
        "hashing_dimension": None,
        "pipeline": {
            "score_floor": 45.0,
            "label_search_limit": 10,
            "semantic_top_k": 5,
            "entity_ruler": None,
            "recall_engine": None,
            "judge": False,
            "context": None,
        },
        "benchmark_source_sha256": ablation.baseline.file_digest(Path(ablation.baseline.__file__)),
    }
    frozen = {
        "provenance": p,
        "configuration_sha256": ablation.baseline.stable_digest(p),
        "positive_results": [{**fixture["cases"][0], "candidates": []}],
        "negative_controls": [],
    }
    return fixture, concepts, frozen


def test_control_identity_rejects_each_pin_drift():
    import copy

    import pytest

    ablation = runner()
    fixture, concepts, frozen = small_control(ablation)
    ablation.verify_control(fixture, concepts, frozen, "source", "disabled", None)
    for key in (
        "fixture_sha256",
        "corpus_sha256",
        "library_source_sha256",
        "model",
        "model_files_sha256",
        "pipeline",
    ):
        changed = copy.deepcopy(frozen)
        changed["provenance"][key] = "drift"
        changed["configuration_sha256"] = ablation.baseline.stable_digest(changed["provenance"])
        with pytest.raises(ValueError, match=key):
            ablation.verify_control(fixture, concepts, changed, "source", "disabled", None)
    frozen["positive_results"][0]["query"] = "changed query"
    with pytest.raises(ValueError, match="case identity"):
        ablation.verify_control(fixture, concepts, frozen, "source", "disabled", None)


def test_run_rejects_wrong_import_before_any_data(tmp_path, monkeypatch):
    import pytest

    import folio_resolve

    ablation = runner()
    monkeypatch.setattr(folio_resolve, "__file__", str(tmp_path / "elsewhere/__init__.py"))
    with pytest.raises(ValueError, match="imported folio_resolve"):
        ablation.run_ablation(tmp_path / "missing.owl", "disabled")


def test_baseline_output_drift_stops_before_any_experimental_comparison(tmp_path, monkeypatch):
    import json

    import pytest

    ablation = runner()
    fixture, concepts, frozen = small_control(ablation)
    monkeypatch.setattr(ablation.baseline, "load_fixtures", lambda: fixture)
    monkeypatch.setattr(ablation.baseline, "load_corpus", lambda *args: concepts)
    monkeypatch.setattr(ablation, "verify_control", lambda *args: {})
    monkeypatch.setattr(ablation, "ROOT", tmp_path)
    dest = tmp_path / "docs/benchmarks/embedding-baseline-disabled.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(frozen))  # Incorrect empty control; real Tax match survives.
    monkeypatch.setattr(
        ablation, "compare_case", lambda *args: pytest.fail("comparison ran after drift")
    )
    with pytest.raises(ValueError, match=r"Final candidates differ.*E"):
        ablation.run_ablation(tmp_path / "unused.owl", "disabled")


def test_metrics_exclude_guards_and_reject_loss_of_exact_targets():
    ablation = runner()

    def case(identity, kind, old, new):
        return {
            "id": identity,
            "kind": kind,
            "acceptable_iris": [] if kind == "negative" else [identity],
            **{
                arm: {
                    "candidates": [{"iri": identity}] if rank else [],
                    "targets": [{"final_rank": rank}] if kind != "negative" else [],
                }
                for arm, rank in (("baseline", old), ("bypass", new))
            },
        }

    cases = [
        case("E", "exact", 1, 1),
        case("P", "paraphrase", None, 1),
        case("N", "negative", None, None),
    ]
    summary = ablation.summarize(cases, ablation.guard_checks())
    assert summary["metrics"]["baseline"]["hit_at_5"] == 0.5
    assert summary["metrics"]["bypass"]["hit_at_5"] == 1
    assert summary["metrics"]["baseline"]["positive_queries"] == 2
    assert summary["metrics"]["bypass"]["negative_candidate_counts"] == {"N": 0}
    assert summary["further_investigation_warranted"]
    cases[0] = case("E", "exact", 1, None)
    assert not ablation.summarize(cases, [])["further_investigation_warranted"]


def test_hashing_run_builds_once_and_records_diagnostic_identity(tmp_path, monkeypatch):
    import json

    ablation = runner()
    fixture, concepts, frozen = small_control(ablation)
    source = ablation.baseline.verified_library_source()
    provenance = frozen["provenance"]
    provenance["library_source_sha256"] = ablation.baseline.stable_digest(
        {
            str(p.relative_to(source.parent.parent)): ablation.baseline.file_digest(p)
            for p in sorted(source.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json"}
        }
    )
    provenance.update(variant="hashing", hashing_dimension=256)
    frozen["configuration_sha256"] = ablation.baseline.stable_digest(provenance)
    build = ablation.baseline.build_pipeline
    frozen["positive_results"][0]["candidates"] = [
        asdict(c) for c in build(concepts, "hashing").match("Tax")
    ]
    monkeypatch.setattr(ablation.baseline, "load_fixtures", lambda: fixture)
    monkeypatch.setattr(ablation.baseline, "load_corpus", lambda *args: concepts)
    monkeypatch.setattr(ablation, "ROOT", tmp_path)
    dest = tmp_path / "docs/benchmarks/embedding-baseline-hashing.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(frozen))
    builds = []

    def observed_build(*args):
        builds.append(args[1])
        return build(*args)

    monkeypatch.setattr(ablation.baseline, "build_pipeline", observed_build)
    # Only git metadata is synthetic; pipeline, identity comparisons, traces and guards are real.
    monkeypatch.setattr(
        ablation.subprocess, "run", lambda *a, **kw: type("Git", (), {"stdout": "test-revision"})()
    )
    result = ablation.run_ablation(tmp_path / "unused.owl", "hashing")
    assert builds == ["hashing"]
    assert (
        result["provenance"]["hashing_role"] == "diagnostic provider, not a semantic quality model"
    )
    assert result["summary"]["metrics"]["baseline"]["hit_at_1"] == 1
    assert (
        result["results"][0]["baseline"]["candidates"]
        == frozen["positive_results"][0]["candidates"]
    )


def test_run_checks_actual_ontology_bytes_before_build(tmp_path, monkeypatch):
    import pytest

    ablation = runner()
    fixture, _, _ = small_control(ablation)
    monkeypatch.setattr(ablation.baseline, "load_fixtures", lambda: fixture)
    owl = tmp_path / "changed.owl"
    owl.write_text("changed ontology")
    with pytest.raises(ValueError, match="Ontology SHA-256"):
        ablation.run_ablation(owl, "disabled")


def test_run_checks_actual_model_bytes_before_build(tmp_path, monkeypatch):
    import json

    import pytest

    ablation = runner()
    fixture, concepts, frozen = small_control(ablation)
    fixture["model"] = {"repo_id": "test", "revision": "revision"}
    monkeypatch.setattr(ablation.baseline, "load_fixtures", lambda: fixture)
    monkeypatch.setattr(ablation.baseline, "load_corpus", lambda *args: concepts)
    monkeypatch.setattr(ablation, "ROOT", tmp_path)
    dest = tmp_path / "docs/benchmarks/embedding-baseline-local.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(frozen))
    model = tmp_path / "revision"
    model.mkdir()
    (model / "weights").write_bytes(b"tampered")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({**fixture["model"], "files": {"weights": "expected-digest"}}))
    monkeypatch.setattr(ablation.baseline, "MODEL_FILES_PATH", manifest)
    monkeypatch.setattr(
        ablation.baseline,
        "build_pipeline",
        lambda *args: pytest.fail("model constructed before validation"),
    )
    with pytest.raises(ValueError, match="Model file SHA-256"):
        ablation.run_ablation(tmp_path / "unused.owl", "local", model)
