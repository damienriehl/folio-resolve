"""Characterize the real scorer before the isolated boundary experiment."""

import importlib.util
from pathlib import Path

import pytest

from folio_resolve.ontology import Concept, InMemoryOntology
from folio_resolve.scoring import compute_relevance_score, content_words

_SPEC = importlib.util.spec_from_file_location(
    "lexical_boundaries", Path(__file__).parents[1] / "benchmarks/embedding_lexical_boundaries.py"
)
assert _SPEC is not None and _SPEC.loader is not None
lexical = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lexical)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Riga", 99.0),
        ("Riga Stock Exchange", 67.5),
        ("Surigao del Norte", 55.2),
        ("Surigao del Sur", 55.2),
        ("Water Supply and Irrigation Systems", 55.2),
    ],
)
def test_characterization_real_scorer(label, expected):
    assert compute_relevance_score(content_words("Riga"), "Riga", label) == expected


def test_characterization_real_provider():
    concepts = [
        Concept(iri=str(i), label=label)
        for i, label in enumerate(
            [
                "Riga",
                "Riga Stock Exchange",
                "Surigao del Norte",
                "Surigao del Sur",
                "Water Supply and Irrigation Systems",
            ]
        )
    ]
    assert [(c.iri, s) for c, s in InMemoryOntology(concepts).search_by_label("Riga")] == [
        ("0", 99.0),
        ("1", 67.5),
        ("2", 55.2),
        ("3", 55.2),
        ("4", 55.2),
    ]


def test_boundary_contract_removes_embedded_riga_bonus():
    assert (
        lexical.boundary_relevance_score(content_words("Riga"), "Riga", "Surigao del Norte") == 0.0
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("riga", 0),
        ("RIGA", None),
        ("(riga)", 1),
        ("riga-stock", 0),
        ("riga's", 0),
        ("'riga'", 1),
        ("1riga", None),
        ("riga2", None),
        ("_riga", None),
        ("riga_", None),
        ("ériga", None),
        ("riga界", None),
        ("\u0301riga", None),
        ("riga\u0301", None),
        ("rigaⅣ", None),
        ("riga²", None),
        ("surigao riga", 8),
        ("rigariga riga", 9),
        ("riga\tstock", 0),
        ("", None),
    ],
)
def test_literal_unicode_boundaries(text, expected):
    assert lexical.containment_start(text, "riga") == expected


@pytest.mark.parametrize(
    "text,needle,expected",
    [
        ("riga stock exchange", "riga stock", 0),
        ("riga  stock exchange", "riga stock", None),
        ("riga-stock exchange", "riga stock", None),
        ("riga-stock exchange", "riga-stock", 0),
        ("é", "e\u0301", None),
        ("anything", "", None),
        ("riga stockyards", "riga stock", None),
        ("a-a-a", "a-a", 0),
    ],
)
def test_phrase_spacing_and_punctuation_are_literal(text, needle, expected):
    assert lexical.containment_start(text, needle) == expected


@pytest.mark.parametrize(
    "field,query,label,kwargs,baseline,treatment",
    [
        ("label", "Riga", "Surigao", {}, 92.0, 0.0),
        ("reverse_label", "Surigao", "Riga", {}, 88.0, 0.0),
        ("preferred_label", "Riga", "Unknown", {"preferred_label": "Surigao"}, 84.0, 0.0),
        ("definition", "Riga", "Unknown", {"definition": "Surigao"}, 60.0, 0.0),
    ],
)
def test_each_containment_branch_and_trace(field, query, label, kwargs, baseline, treatment):
    trace = []
    assert compute_relevance_score(set(), query, label, **kwargs) == baseline
    assert (
        lexical.boundary_relevance_score(
            set(), query, label, policy="substring", trace=trace, **kwargs
        )
        == baseline
    )
    assert len(trace) == 1 and trace[0]["field"] == field
    trace = []
    assert lexical.boundary_relevance_score(set(), query, label, trace=trace, **kwargs) == treatment
    assert trace == []


@pytest.mark.parametrize(
    "field,query,label,kwargs,expected,start",
    [
        ("label", " RIGA ", "Surigao Riga", {}, 92.0, 8),
        ("reverse_label", "Surigao Riga", "Riga", {}, 88.0, 8),
        ("preferred_label", "RIGA", "Unknown", {"preferred_label": "Surigao Riga"}, 84.0, 8),
        ("definition", "RIGA", "Unknown", {"definition": "Surigao Riga"}, 60.0, 8),
    ],
)
def test_scoring_records_later_qualifying_occurrence(field, query, label, kwargs, expected, start):
    trace = []
    assert lexical.boundary_relevance_score(set(), query, label, trace=trace, **kwargs) == expected
    assert trace == [
        {
            "field": field,
            "text": "surigao riga",
            "needle": "riga",
            "start": start,
            "end": start + 4,
            "bonus": expected,
        }
    ]


def test_traces_only_awards_not_failed_ratio_or_exact_matches():
    for query, label in [("Riga", "Riga"), ("Riga and many other words", "Riga")]:
        trace = []
        lexical.boundary_relevance_score(set(), query, label, trace=trace)
        assert trace == []


def test_surviving_overlap_synonyms_and_definition_weight():
    assert lexical.boundary_relevance_score({"riga"}, "Riga", "Rigatoni") == 70.4
    assert (
        lexical.boundary_relevance_score({"riga"}, "Riga", "Surigao", synonyms=[None, 7, "Riga"])
        == 82.0
    )
    assert (
        lexical.boundary_relevance_score(
            {"riga"}, "Riga", "Surigao", definition="Riga", synonyms=["Riga"]
        )
        == 89.2
    )
    # Removing pref containment allows the existing overlap branch to run (86 > 84).
    assert (
        lexical.boundary_relevance_score({"riga"}, "Riga", "Unknown", preferred_label="Riga's")
        == 84.0
    )
    assert (
        lexical.boundary_relevance_score({"riga"}, "Riga", "Unknown", preferred_label="Rigatoni")
        == 68.8
    )


@pytest.mark.parametrize(
    "label",
    [
        "Riga",
        "Riga Stock Exchange",
        "Surigao del Norte",
        "Surigao del Sur",
        "Water Supply and Irrigation Systems",
    ],
)
def test_riga_treatment_scores(label):
    expected = {"Riga": 99.0, "Riga Stock Exchange": 67.5}.get(label, 0.0)
    assert lexical.boundary_relevance_score({"riga"}, "Riga", label) == expected


@pytest.mark.parametrize("penalty", [0.0, 0.3, 1.0, 4.0, -1.0])
@pytest.mark.parametrize(
    "query,label,kwargs",
    [
        ("RIGA", "riga", {}),
        (" Riga ", "Riga Stock Exchange", {}),
        ("rules of arbitration", "Arbitration Rules", {}),
        ("Habitability", "Breach of Warranty of Habitability", {}),
        ("lease", "tenancy", {"synonyms": ["lease", None, 17], "definition": "A lease"}),
        ("Riga", "Unknown", {"preferred_label": "RIGA"}),
        ("Riga", "Unknown", {"definition": None, "preferred_label": 12}),
        (None, None, {}),
        (42, 17, {}),
        (None, "Unrelated", {}),
        ("", "", {}),
        (" ", " ", {}),
    ],
)
def test_unaffected_scores_match_production_exactly(query, label, kwargs, penalty):
    args = (content_words(query), query, label)
    expected = compute_relevance_score(*args, specificity_penalty=penalty, **kwargs)
    assert (
        lexical.boundary_relevance_score(*args, specificity_penalty=penalty, **kwargs) == expected
    )


@pytest.mark.parametrize("query", ["Riga", "Surigao", "", None, 17, "rules of arbitration"])
@pytest.mark.parametrize(
    "label", ["Riga", "Surigao del Norte", "Riga Stock Exchange", "Arbitration Rules", None, 17]
)
def test_substring_copy_matches_real_scorer_with_all_channels(query, label):
    kwargs = {
        "definition": "Surigao Riga",
        "preferred_label": "Surigao",
        "synonyms": [None, 17, "Riga"],
    }
    args = (content_words(query), query, label)
    assert lexical.boundary_relevance_score(
        *args, policy="substring", **kwargs
    ) == compute_relevance_score(*args, **kwargs)


@pytest.mark.parametrize("query", [None, 17, "", "   "])
def test_empty_needle_loses_only_definition_containment(query):
    assert compute_relevance_score(set(), query, "Unknown", definition="Text") == 60.0
    assert lexical.boundary_relevance_score(set(), query, "Unknown", definition="Text") == 0.0


def test_vector_and_invalid_content_entries_preserve_arithmetic():
    for policy in ["substring", "boundary"]:
        kwargs = {
            "use_vectors": True,
            "word_similarity": lambda a, b: 0.4,
            "definition": "Other text",
            "synonyms": [False, "Different"],
        }
        args = ({"riga", None, 17}, "Riga", "Unrelated")
        assert lexical.boundary_relevance_score(
            *args, policy=policy, **kwargs
        ) == compute_relevance_score(*args, **kwargs)


def test_preferred_overlap_can_outscore_removed_containment():
    args = ({"riga"}, "Riga", "Unknown")
    assert compute_relevance_score(*args, preferred_label="Riga_Riga") == 84.0
    trace = []
    assert lexical.boundary_relevance_score(*args, preferred_label="Riga_Riga", trace=trace) == 86.0
    assert trace == []


def test_baseline_export_is_the_real_production_function():
    assert lexical.baseline_relevance_score is compute_relevance_score


@pytest.mark.parametrize(
    "query,label,kwargs",
    [
        ("cat", "cat person", {}),
        ("cat person", "cat", {}),
        ("cat", "Unknown", {"preferred_label": "cat person"}),
    ],
)
def test_original_minimum_lengths_do_not_award_containment(query, label, kwargs):
    trace = []
    assert lexical.boundary_relevance_score(set(), query, label, trace=trace, **kwargs) == 0.0
    assert trace == []


def test_provider_reretrieves_before_limit_and_orders_ties():
    concepts = [
        Concept(iri="z", label="Surigao"),
        Concept(iri="b", label="Town Exchange", alternative_labels=("Riga",)),
        Concept(iri="a", label="Town Exchange", alternative_labels=("Riga",)),
    ]
    assert (
        lexical.LexicalOntology(concepts, "substring").search_by_label("Riga", limit=1)[0][0].iri
        == "z"
    )
    treatment = lexical.LexicalOntology(concepts, "boundary")
    assert [c.iri for c, _ in treatment.search_by_label("Riga", limit=2)] == ["a", "b"]


def test_real_pipeline_decomposition_shared_semantics_and_fresh_ranking():
    from folio_resolve import MatchPipeline

    concepts = [
        Concept(iri="embedded", label="Surigao"),
        Concept(iri="riga", label="Riga"),
        Concept(iri="paris", label="Paris"),
    ]
    query = "Riga and Paris"
    case = {
        "id": "small",
        "kind": "exact",
        "query": query,
        "acceptable_iris": ["riga"],
        "expected_labels": {"riga": "Riga"},
    }

    class Semantic:
        calls = 0

        def query(self, text, *, top_k):
            assert text == query and top_k == 5
            self.calls += 1
            return [("riga", "Riga", 0.8)]

    semantic = Semantic()
    pipeline = MatchPipeline(InMemoryOntology(concepts), semantic_index=semantic)
    inputs = lexical.precision.retrieve_once(pipeline, query)
    controls = [{**case, **lexical.precision.rank_pair(pipeline, inputs, ["riga"])}]
    semantic.calls = 0
    result = lexical.collect_cases(concepts, semantic, {"cases": [case]}, controls)[0]
    assert semantic.calls == 1
    left = result["substring"]["baseline"]["candidate_inputs"]
    right = result["boundary"]["baseline"]["candidate_inputs"]
    assert [c for c in left if c["extraction_path"] == "semantic"] == [
        c for c in right if c["extraction_path"] == "semantic"
    ]
    assert any(c["iri"] == "embedded" and c["extraction_path"] == "decomposition" for c in left)
    assert not any(
        c["iri"] == "embedded" and c["extraction_path"] == "decomposition" for c in right
    )
    for policy in ("boundary", "substring"):
        snapshots = result[policy]
        for gate in ("selective", "baseline"):
            pipe = lexical.precision.selective_from(pipeline) if gate == "selective" else pipeline
            assert (
                lexical.precision.replay.rank_snapshot(
                    pipe, snapshots[gate]["candidate_inputs"], ["riga"]
                )
                == snapshots[gate]
            )
    assert result["containment_changes"]


def test_complete_controls_required_including_last_case():
    import copy

    frozen = lexical.load_frozen_inputs()[0]
    actual = copy.deepcopy(frozen["results"])
    lexical.precision.verify_controls(actual, frozen["results"])
    actual[-1]["selective"]["candidate_inputs"].append({"tampered": True})
    with pytest.raises(ValueError, match="selective complete control snapshot differs"):
        lexical.precision.verify_controls(actual, frozen["results"])


@pytest.fixture
def small_collection(monkeypatch):
    import json
    from dataclasses import asdict

    from folio_resolve import MatchPipeline

    frozen, sheet = lexical.load_frozen_inputs()
    concepts = [
        Concept(iri="a", label="Riga"),
        Concept(iri="b", label="Surigao"),
        Concept(iri="c", label="Town Exchange", alternative_labels=("Riga",)),
        Concept(iri="d", label="Paris"),
        Concept(iri="e", label="Rome"),
    ]
    case = {
        "id": "small",
        "kind": "exact",
        "query": "Riga",
        "acceptable_iris": ["a"],
        "expected_labels": {"a": "Riga"},
    }
    fixture = {**frozen["fixture"], "cases": [case]}
    response = [[c.iri, c.label, 0.8 - i * 0.1] for i, c in enumerate(concepts)]
    semantic = lexical.SavedSemanticResponse("Riga", response)
    pipe = MatchPipeline(InMemoryOntology(concepts), semantic_index=semantic)
    inputs = lexical.precision.retrieve_once(pipe, "Riga")
    controls = [{**case, **lexical.precision.rank_pair(pipe, inputs, ["a"])}]
    frozen = {
        **frozen,
        "fixture": fixture,
        "results": controls,
        "pool": lexical.precision.candidate_pool(controls, concepts),
    }
    monkeypatch.setattr(lexical, "load_frozen_inputs", lambda: (frozen, sheet))
    results = lexical.collect_cases(concepts, semantic, fixture, controls)
    pool = lexical.pool_from_results(results, concepts)
    provenance = lexical.expected_provenance(frozen, sheet)
    runtime = {"python": "3.11.0", "platform": "test", "packages": {"pytest": "8.0"}}
    metadata = json.loads(json.dumps([asdict(c) for c in concepts]))
    value = {
        "schema_version": lexical.SCHEMA,
        "fixture": fixture,
        "provenance": provenance,
        "configuration_sha256": lexical.baseline.stable_digest(provenance),
        "runtime": runtime,
        "runtime_sha256": lexical.baseline.stable_digest(runtime),
        "results": results,
        "pool": pool,
        "pool_sha256": lexical.baseline.stable_digest(pool),
        "concept_metadata": metadata,
        "concept_metadata_sha256": lexical.baseline.stable_digest(metadata),
        "controls_reproduced": True,
    }
    lexical.validate_collection(value)
    return value


def test_offline_new_schema_validates_without_model(small_collection):
    lexical.validate_collection(small_collection)


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "model_files_sha256",
        "corpus_sha256",
        "source_sha256",
        "library_source_sha256",
        "fixture_sha256",
        "pipeline",
        "owner_receipt_sha256",
    ],
)
def test_offline_provenance_tampering_blocked_even_with_new_digest(small_collection, key):
    small_collection["provenance"][key] = "changed"
    small_collection["configuration_sha256"] = lexical.baseline.stable_digest(
        small_collection["provenance"]
    )
    with pytest.raises(ValueError, match=f"provenance {key} differs"):
        lexical.validate_collection(small_collection)


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("semantic", "semantic response|Semantic response"),
        ("rank", "ranked snapshot"),
        ("trace", "trace coverage"),
        ("schema", "schema"),
        ("pool", "pool"),
        ("fixture", "fixture"),
        ("controls", "controls not reproduced"),
    ],
)
def test_offline_evidence_corruption_blocked(small_collection, mutation, message):
    row = small_collection["results"][0]
    if mutation == "semantic":
        row["semantic_response"][0][2] = 0.1
    elif mutation == "rank":
        row["boundary"]["selective"]["candidates"][0]["score"] += 1
    elif mutation == "trace":
        row["containment_changes"].clear()
    elif mutation == "schema":
        small_collection["schema_version"] = 1
    elif mutation == "pool":
        small_collection["pool"][0]["label"] = "changed"
        small_collection["pool_sha256"] = lexical.baseline.stable_digest(small_collection["pool"])
    elif mutation == "fixture":
        small_collection["fixture"] = {**small_collection["fixture"], "cases": []}
    else:
        small_collection["controls_reproduced"] = False
    with pytest.raises(ValueError, match=message):
        lexical.validate_collection(small_collection)


def test_treatment_never_collected_when_control_fails(monkeypatch):
    concepts = [Concept(iri="a", label="Riga")]
    fixture = {
        "cases": [
            {
                "id": "small",
                "kind": "exact",
                "query": "Riga",
                "acceptable_iris": ["a"],
                "expected_labels": {"a": "Riga"},
            }
        ]
    }
    original = lexical.LexicalOntology.search_by_label

    def guard(self, query, *, limit=20):
        assert self.policy == "substring"
        return original(self, query, limit=limit)

    monkeypatch.setattr(lexical.LexicalOntology, "search_by_label", guard)
    with pytest.raises(ValueError, match="control count differs"):
        lexical.collect_cases(
            concepts, lexical.SavedSemanticResponse("Riga", [["a", "Riga", 0.8]]), fixture, []
        )


def test_scoring_miniature_fixed_gate_oracle():
    def cell(*iris):
        return {
            "candidates": [
                {"iri": iri, "score": 90 - i, "extraction_path": "label_search"}
                for i, iri in enumerate(iris)
            ]
        }

    rows = [
        {
            "id": "old",
            "query": "Riga",
            "kind": "geographic",
            "acceptable_iris": ["direct"],
            "substring": {
                "baseline": cell("direct", "child", "unknown"),
                "selective": cell("direct"),
            },
            "boundary": {"baseline": cell("child", "fresh"), "selective": cell("direct", "child")},
        },
        {
            "id": "new",
            "query": "other",
            "kind": "paraphrase",
            "acceptable_iris": ["direct"],
            "substring": {"baseline": cell("direct"), "selective": cell("direct")},
            "boundary": {"baseline": cell(), "selective": cell()},
        },
        {
            "id": "negative",
            "query": "noise",
            "kind": "negative",
            "acceptable_iris": [],
            "substring": {"baseline": cell("unknown"), "selective": cell()},
            "boundary": {"baseline": cell(), "selective": cell()},
        },
    ]
    labels = {("old", "direct"): "direct_match", ("old", "child"): "child"}
    summary = lexical.summarize_gate(rows, labels, ["old"], "baseline")
    strict = summary["strict"]
    old = strict["queries"][0]
    assert old["substring"]["p_at_5_bounds"] == [0.2, 0.4]
    assert old["boundary"]["p_at_5_bounds"] == [0, 0.2]
    assert old["substring"]["p_at_5"] is None
    assert old["boundary"]["target_hit_at_5"] is False
    assert strict["queries"][1]["substring"]["missing"] == 1  # Query-specific identity.
    combined = strict["positive_groups"]["combined"]
    assert combined["query_count"] == 2
    assert combined["substring"]["p_at_5_bounds"] == pytest.approx([0.1, 0.3])
    assert combined["substring"]["target_hits_at_5"] == 2
    assert strict["positive_groups"]["original"]["query_ids"] == ["old"]
    assert strict["positive_groups"]["new"]["query_ids"] == ["new"]
    assert summary["expanded"]["queries"][0]["substring"]["p_at_5_bounds"] == [0.4, 0.6]
    assert summary["negative_admissions"][0]["substring"]["returned"] == 1
    changes = summary["candidate_changes"][0]
    assert changes["removed"]["direct_match"] == ["direct"]
    assert changes["removed"]["unknown"] == ["unknown"]
    assert changes["added"]["unknown"] == ["fresh"]
    selective = lexical.summarize_gate(rows, labels, ["old"], "selective")
    assert selective["strict"]["queries"][0]["substring"]["p_at_5"] == 0.2
    assert selective["expanded"]["queries"][0]["boundary"]["p_at_5"] == 0.4


def test_transfer_real_frozen_categories_and_new_unknown():
    import copy

    frozen, sheet = lexical.load_frozen_inputs()
    pool = copy.deepcopy(frozen["pool"])
    pool.append({**pool[0], "iri": "new-iri"})
    labels = lexical.transfer_judgments(pool, frozen, sheet)
    assert sum(v is not None for v in labels.values()) == 29
    assert sum(v is None for v in labels.values()) == 27
    assert labels[pool[-1]["query_id"], "new-iri"] is None
    for row in sheet["judgments"]:
        assert labels[row["query_id"], row["iri"]] == row["judgment"]
    pool[0]["query"] += " changed"
    with pytest.raises(ValueError, match="metadata differs"):
        lexical.transfer_judgments(pool, frozen, sheet)


def test_scoring_blocks_invalid_collection_and_receipt():
    import copy

    with pytest.raises(ValueError, match="schema differs"):
        lexical.run_score({})
    frozen, sheet = lexical.load_frozen_inputs()
    sheet = copy.deepcopy(sheet)
    sheet["approval"]["owner"] = "changed"
    with pytest.raises(ValueError, match="approval receipt digest differs"):
        lexical.transfer_judgments(frozen["pool"], frozen, sheet)


def test_real_frozen_judgment_chain_identity_arithmetic():
    # Real historical evidence, NOT a claim of a new real-model lexical collection.
    frozen, sheet = lexical.load_frozen_inputs()
    expected = lexical.relations.run_score(frozen, sheet, lexical.OWNER_RECEIPT)
    labels = lexical.transfer_judgments(frozen["pool"], frozen, sheet)
    original_ids = [case["id"] for case in lexical.baseline.load_fixtures()["cases"]]
    rows = [
        {
            **row,
            "substring": {gate: row[gate] for gate in lexical.GATES},
            "boundary": {gate: row[gate] for gate in lexical.GATES},
        }
        for row in frozen["results"]
    ]
    for gate in lexical.GATES:
        summary = lexical.summarize_gate(rows, labels, original_ids, gate)
        for metric in ("strict", "expanded"):
            for group, source in expected[metric]["positive_groups"].items():
                actual = summary[metric]["positive_groups"][group]
                assert actual["query_count"] == source["query_count"]
                assert actual["substring"] == actual["boundary"] == source[gate]
        assert len(summary["negative_admissions"]) == 4
        assert all(
            not iris
            for row in summary["candidate_changes"]
            for direction in ("added", "removed")
            for iris in row[direction].values()
        )
