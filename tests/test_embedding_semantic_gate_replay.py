"""Real-ranker controls and path-selective replay contracts."""

import importlib.util
from dataclasses import replace
from pathlib import Path

from folio_resolve import InMemoryOntology, MatchCandidate, MatchPipeline


def runner():
    spec = importlib.util.spec_from_file_location(
        "embedding_semantic_gate_replay",
        Path(__file__).parents[1] / "benchmarks/embedding_semantic_gate_replay.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate(path="semantic", iri="target", **kwargs):
    return MatchCandidate(
        iri=iri,
        label="Negligence",
        score=80,
        surface_term="failure to take care",
        extraction_path=path,
        **kwargs,
    )


def rank(pipe, candidates):
    return pipe._rank(candidates, domains=[], heading_terms=set())


def test_characterize_normal_real_ranker():
    pipe = MatchPipeline(InMemoryOntology([]))
    assert rank(pipe, [candidate(), candidate("label_search", "lexical")]) == []
    exact = replace(candidate("label_search"), surface_term="Negligence", score=99)
    assert rank(pipe, [exact]) == [exact]


def test_selective_recovers_semantic_and_retains_lexical_protection():
    pipe = runner().selective_pipeline()
    survivors = rank(pipe, [candidate(), candidate("label_search", "lexical")])
    assert [(c.iri, c.score) for c in survivors] == [("target", 80)]


def test_nonsemantic_paths_and_exact_lexical():
    pipe = runner().selective_pipeline()
    for path in ("unknown", "decomposition", "entity_ruler", "label_search", "", "Semantic"):
        assert rank(pipe, [candidate(path)]) == []
    exact = replace(candidate("label_search"), surface_term="Negligence")
    assert rank(pipe, [exact]) == [exact]


def test_duplicate_path_identity_ties_and_secondary_order():
    pipe = runner().selective_pipeline()
    semantic = candidate()
    lexical = candidate("label_search")
    # Identical IRI, label and score must not cause lexical to inherit semantic bypass.
    assert rank(pipe, [lexical, semantic])[0] is semantic
    semantic = candidate()
    lexical = candidate("label_search")
    assert rank(pipe, [semantic, lexical])[0] is semantic
    first = replace(candidate(), score=99, rank_tiebreak_score=1)
    equal = replace(first, extraction_path="label_search")
    assert rank(pipe, [first, equal])[0] is first
    assert rank(pipe, [equal, first])[0] is equal
    stronger = replace(equal, rank_tiebreak_score=2)
    assert rank(pipe, [first, stronger])[0] is stronger
    b = candidate(iri="b")
    a = candidate(iri="a")
    assert [c.iri for c in rank(pipe, [b, a])] == ["a", "b"]


def test_place_blocklist_floor_and_context_alignment():
    replay = runner()
    for path in ("label_search", "semantic"):
        pipe = replay.selective_pipeline()
        pipe.blocklist.block("failure to take care", "blocked")
        blocked = candidate(path, "blocked")
        place = replace(candidate(path, "place"), branch="Location")
        low = replace(candidate(path, "low"), score=44)
        eligible = candidate("semantic", "eligible")
        assert [c.iri for c in rank(pipe, [blocked, place, low, eligible])] == ["eligible"]
        assert place.score == 40 and place.gated
        assert low.score < 45
        assert replay._CURRENT_PATH.get() is None
    # Blocked semantic followed by lexical must not accidentally bypass lexical gating.
    pipe = replay.selective_pipeline()
    pipe.blocklist.block("failure to take care", "blocked")
    assert rank(pipe, [candidate("semantic", "blocked"), candidate("label_search")]) == []


def test_exception_resets_scope_and_later_calls(monkeypatch):
    import pytest

    replay = runner()
    pipe = replay.selective_pipeline()
    evaluate = pipe.place_gate.evaluate

    def broken(**kwargs):
        raise RuntimeError("probe")

    monkeypatch.setattr(pipe.place_gate, "evaluate", broken)
    with pytest.raises(RuntimeError, match="probe"):
        rank(pipe, [candidate()])
    assert replay._CURRENT_PATH.get() is None
    monkeypatch.setattr(pipe.place_gate, "evaluate", evaluate)
    assert rank(pipe, [candidate("unknown")]) == []
    assert len(rank(pipe, [candidate()])) == 1
    assert replay._CURRENT_PATH.get() is None
    assert pipe.short_gate.evaluate(query="care", label="Negligence", score=80).demoted


def saved_inputs(replay, variant="local"):
    import json

    return json.loads(
        (replay.ROOT / f"docs/benchmarks/embedding-gate-ablation-{variant}.json").read_text()
    )


def test_all_actual_frozen_controls_and_guard_denominators():
    replay = runner()
    result = replay.run_replay()
    assert result["decision"]["preserves_observed_benefit_and_lexical_protection"]
    for variant, data in result["variants"].items():
        saved = saved_inputs(replay, variant)
        for case, original in zip(data["results"], saved["results"], strict=True):
            for arm in ("baseline", "bypass"):
                assert case[arm]["candidates"] == original[arm]["candidates"]
        for arm in replay.ARMS:
            assert data["metrics"][arm]["positive_queries"] == 6
            assert set(data["metrics"][arm]["negative_candidate_counts"]) == {"N1", "N2"}
    assert len(result["guard_checks"]) == 8
    assert all(
        g["selective_matches_baseline_survival"]
        for g in result["guard_checks"]
        if g["input"]["extraction_path"] == "label_search"
    )
    lost = [g["name"] for g in result["guard_checks"] if g["selective_protection_lost"]]
    assert lost == ["short_fuzzy_law:semantic"]


def test_artifact_and_import_drift_rejected(tmp_path, monkeypatch):
    import pytest

    import folio_resolve

    replay = runner()
    path = tmp_path / "docs/benchmarks/embedding-gate-ablation-disabled.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="Frozen artifact SHA-256"):
        replay.run_replay()
    monkeypatch.setattr(folio_resolve, "__file__", str(tmp_path / "wrong/__init__.py"))
    with pytest.raises(ValueError, match="imported folio_resolve"):
        replay.run_replay()


def test_source_fixture_configuration_and_case_drift_rejected():
    import copy

    import pytest

    replay = runner()
    saved = saved_inputs(replay)
    fixture = replay.baseline.load_fixtures()
    source = replay.source_identity()
    replay.verify_saved(saved, fixture, "local", source)
    with pytest.raises(ValueError, match="library_source_sha256"):
        replay.verify_saved(saved, fixture, "local", "changed")
    changed_fixture = copy.deepcopy(fixture)
    changed_fixture["cases"][0]["query"] = "changed"
    with pytest.raises(ValueError, match="fixture_sha256"):
        replay.verify_saved(saved, changed_fixture, "local", source)
    for key in (
        "benchmark_source_sha256",
        "diagnostic_source_sha256",
        "ablation_source_sha256",
        "baseline_sha256",
        "pipeline",
        "variant",
    ):
        changed = copy.deepcopy(saved)
        changed["provenance"][key] = "changed"
        changed["configuration_sha256"] = replay.baseline.stable_digest(changed["provenance"])
        with pytest.raises(ValueError, match=key):
            replay.verify_saved(changed, fixture, "local", source)
    changed = copy.deepcopy(saved)
    changed["configuration_sha256"] = "changed"
    with pytest.raises(ValueError, match="configuration digest"):
        replay.verify_saved(changed, fixture, "local", source)
    changed = copy.deepcopy(saved)
    changed["results"][0]["query"] = "changed"
    with pytest.raises(ValueError, match="case identity"):
        replay.verify_saved(changed, fixture, "local", source)
    changed = copy.deepcopy(saved)
    changed["results"].pop()
    with pytest.raises(ValueError, match="case count"):
        replay.verify_saved(changed, fixture, "local", source)
    changed = copy.deepcopy(saved)
    changed["results"][0]["bypass"]["retrievals"][0]["before"]["score"] = 0
    with pytest.raises(ValueError, match="Paired inputs"):
        replay.verify_saved(changed, fixture, "local", source)


def test_both_control_output_drifts_prevent_selective_verdict(monkeypatch):
    import copy

    import pytest

    replay = runner()
    saved = saved_inputs(replay)
    monkeypatch.setattr(
        replay, "selective_pipeline", lambda: pytest.fail("selective ran after drift")
    )
    for arm in ("baseline", "bypass"):
        for field in ("candidates", "mutations"):
            changed = copy.deepcopy(saved)
            if field == "candidates":
                changed["results"][0][arm]["candidates"][0]["gate_reason"] = "changed"
            else:
                changed["results"][0][arm]["retrievals"][0]["after"]["gate_reason"] = "changed"
            with pytest.raises(ValueError, match=f"{arm} control {field} differ"):
                replay.replay_controls(changed)
