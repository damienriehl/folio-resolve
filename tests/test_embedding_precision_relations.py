"""Owner relation categories project to two metrics without inventing judgments."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def runner():
    spec = importlib.util.spec_from_file_location(
        "embedding_precision_relations", ROOT / "benchmarks/embedding_precision_relations.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def approve(mod, sheet):
    sheet["approval"] = {
        "owner": "test owner",
        "date": "2026-09-20",
        "decision": "approve categories",
        "decision_reference": "test oracle",
        "payload_sha256": mod.precision.baseline.stable_digest(
            mod.precision.judgment_payload(sheet)
        ),
    }
    return mod.precision.baseline.stable_digest(sheet["approval"])


def collection():
    return json.loads((ROOT / "docs/benchmarks/embedding-precision-collection.json").read_text())


def test_frozen_collection_owner_categories_offline(monkeypatch):
    mod = runner()
    data = collection()
    sheet = mod.prepare_judgments(data)
    categories = {
        "direct_match": [2, 8, 12, 17, 27, 28, 37, 41, 43, 50],
        "child": [3, 35, 36, 38, 39, 40, 42],
        "parent": [4],
        "relationship": [5, 9, 10, 11, 24, 25, 26, 30, 33, 34, 53],
    }
    for category, numbers in categories.items():
        for number in numbers:
            sheet["judgments"][number - 1]["judgment"] = category
    receipt = approve(mod, sheet)

    def forbidden(*args, **kwargs):
        pytest.fail("Scoring must not load a model or retrieve")

    monkeypatch.setattr(mod.precision.baseline, "build_pipeline", forbidden)
    monkeypatch.setattr(mod.precision, "retrieve_once", forbidden)
    result = mod.run_score(data, sheet, receipt)
    assert result["coverage"] == {
        "pooled_pairs": 55,
        "direct_match": 10,
        "child": 7,
        "parent": 1,
        "relationship": 11,
        "irrelevant": 0,
        "uncertain": 0,
        "missing": 26,
        "annotated": 29,
    }
    assert len(result["unresolved_pairs"]) == 26
    for metric in ("strict", "expanded"):
        combined = result[metric]["positive_groups"]["combined"]
        assert combined["query_count"] == 12
        assert combined["baseline"]["p_at_5"] is None
        assert combined["selective"]["p_at_5"] is None
    assert "not verified ontology edges" in " ".join(result["limitations"])
    assert "benchmarks/embedding_precision_relations.py" in result["scoring_source_sha256"]


@pytest.mark.parametrize("unknown", [None, "uncertain"])
def test_hand_calculated_projections_and_paired_withholding(unknown):
    mod = runner()
    candidates = [{"iri": str(i), "extraction_path": "semantic"} for i in range(5)]
    case = {
        "id": "Q",
        "query": "q",
        "kind": "exact",
        "acceptable_iris": ["0"],
        "baseline": {"candidates": candidates[:4]},
        "selective": {"candidates": candidates},
    }
    labels = dict(
        zip(
            (("Q", str(i)) for i in range(5)),
            ["direct_match", "child", "parent", "relationship", unknown],
            strict=True,
        )
    )
    strict = mod.precision.summarize([case], mod.project_labels(labels, "strict"), ["Q"])
    expanded = mod.precision.summarize([case], mod.project_labels(labels, "expanded"), ["Q"])
    assert strict["queries"][0]["selective"]["p_at_5_bounds"] == [0.2, 0.4]
    assert expanded["queries"][0]["selective"]["p_at_5_bounds"] == [0.8, 1.0]
    assert strict["queries"][0]["baseline"]["p_at_5"] is None
    assert expanded["positive_groups"]["combined"]["baseline"]["p_at_5"] is None
    case["selective"]["candidates"] = candidates[:4]
    for metric, expected in (("strict", 0.2), ("expanded", 0.8)):
        summary = mod.precision.summarize([case], mod.project_labels(labels, metric), ["Q"])
        assert summary["queries"][0]["baseline"]["p_at_5"] == expected


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda s: s["judgments"][0].update(judgment="relevant"), "Invalid category"),
        (lambda s: s["judgments"][0].update(label="altered"), "metadata"),
        (lambda s: s.update(rubric="different"), "rubric"),
        (lambda s: s["judgments"].pop(), "pairs"),
    ],
)
def test_reject_invalid_signed_sheet(mutation, message):
    mod = runner()
    data = collection()
    sheet = mod.prepare_judgments(data)
    mutation(sheet)
    receipt = approve(mod, sheet)
    with pytest.raises(ValueError, match=message):
        mod.run_score(data, sheet, receipt)


def test_approval_checked_before_collection_and_source_drift_rejected():
    mod = runner()
    data = collection()
    sheet = mod.prepare_judgments(data)
    sheet["judgments"][0]["judgment"] = "child"
    receipt = approve(mod, sheet)
    changed = copy.deepcopy(sheet)
    changed["judgments"][0]["judgment"] = "direct_match"
    with pytest.raises(ValueError, match="payload digest"):
        mod.run_score({}, changed, receipt)
    with pytest.raises(ValueError, match="receipt digest"):
        mod.run_score({}, sheet, "0" * 64)
    with pytest.raises(ValueError, match="approval digest required"):
        mod.run_score({}, sheet)
    data["provenance"]["source_sha256"]["benchmarks/embedding_precision.py"] = "changed"
    sheet = mod.prepare_judgments(data)
    receipt = approve(mod, sheet)
    with pytest.raises(ValueError, match="provenance/source"):
        mod.run_score(data, sheet, receipt)


@pytest.mark.parametrize("field", ["judgment", "rationale", "parents"])
def test_missing_row_fields_cannot_be_repaired_by_projection(field):
    mod = runner()
    data = collection()
    sheet = mod.prepare_judgments(data)
    del sheet["judgments"][0][field]
    receipt = approve(mod, sheet)
    with pytest.raises(ValueError, match="metadata"):
        mod.run_score(data, sheet, receipt)


def test_unapproved_blank_sheet_and_explicit_irrelevant_uncertain():
    mod = runner()
    data = collection()
    sheet = mod.prepare_judgments(data)
    blank = mod.run_score(data, sheet)
    assert blank["coverage"]["missing"] == 55
    assert blank["approval_sha256"] is None
    sheet["judgments"][0]["judgment"] = "irrelevant"
    sheet["judgments"][1]["judgment"] = "uncertain"
    result = mod.run_score(data, sheet, approve(mod, sheet))
    assert result["coverage"]["annotated"] == 1
    assert result["coverage"]["uncertain"] == 1
    assert len(result["unresolved_pairs"]) == 54


def test_related_negative_requires_annotation_resolution():
    mod = runner()
    case = {
        "id": "N",
        "query": "nonsense",
        "kind": "negative",
        "acceptable_iris": [],
        "baseline": {"candidates": []},
        "selective": {"candidates": [{"iri": "r", "extraction_path": "semantic"}]},
    }
    labels = {("N", "r"): "relationship"}
    with pytest.raises(ValueError, match="Annotation conflict"):
        mod.precision.summarize([case], mod.project_labels(labels, "expanded"), [])
