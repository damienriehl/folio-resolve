"""Label-blind generation harness contracts; fixtures contain no evaluation data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from folio_eval.generation import fill_report, input_manifest, load_quotas, render_prompt
from folio_eval.synthesize import SyntheticItem

ROOT = Path(__file__).parents[1]
QUOTAS = ROOT / "eval" / "synthetic" / "generation" / "quotas_v1.json"
TEMPLATE = ROOT / "eval" / "synthetic" / "generation" / "prompt_template.md"


def test_quota_round_trip(tmp_path: Path) -> None:
    quotas = load_quotas(QUOTAS)
    copy = tmp_path / "quotas.json"
    copy.write_text(json.dumps(quotas.to_json()), encoding="utf-8")
    assert load_quotas(copy) == quotas
    assert quotas.scoreable_target == 240
    assert quotas.no_match_target == 30


def test_fill_report_math_on_graded_fixture(tmp_path: Path) -> None:
    path = tmp_path / "quotas.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "doc_types": [
                    {
                        "doc_type": "brief",
                        "jurisdictions": ["US federal"],
                        "scoreable": 2,
                        "no_match": 1,
                    }
                ],
                "branch_targets": {"Objectives": 2, "Service": 1},
            }
        ),
        encoding="utf-8",
    )
    quotas = load_quotas(path)
    items = [
        SyntheticItem(
            "one",
            "brief",
            "US federal",
            "Passage one.",
            gold_iris=frozenset({"urn:test:a", "urn:test:b"}),
            verification="human",
            provenance={"branches_by_iri": {"urn:test:a": "Objectives", "urn:test:b": "Service"}},
        ),
        SyntheticItem(
            "two",
            "brief",
            "US federal",
            "Passage two.",
            gold_iris=frozenset({"urn:test:c"}),
            verification="human",
            provenance={"branches_by_iri": {"urn:test:c": "Objectives"}},
        ),
        SyntheticItem(
            "none",
            "brief",
            "US federal",
            "A routine administrative passage.",
            provenance={"no_match": True},
        ),
    ]
    report = fill_report(quotas, items)
    assert report.doc_types["brief"].scoreable_fraction == 1.0
    assert report.doc_types["brief"].no_match_fraction == 1.0
    assert report.branches == {"Objectives": 1.0, "Service": 1.0}


@pytest.mark.parametrize(
    "template",
    [
        "Use Objectives here: {scenario}",
        "Mention FOLIO: {scenario}",
        "Use this IRI: {scenario}",
        "Visit https://example.invalid: {scenario}",
    ],
)
def test_render_prompt_refuses_leakage_classes(tmp_path: Path, template: str) -> None:
    quota_path = tmp_path / "q.json"
    quota_path.write_text(
        json.dumps(
            {
                "version": 1,
                "doc_types": [
                    {"doc_type": "brief", "jurisdictions": ["test"], "scoreable": 1, "no_match": 0}
                ],
                "branch_targets": {"Objectives": 1},
            }
        ),
        encoding="utf-8",
    )
    assignment = {
        "doc_type": "brief",
        "jurisdiction": "test",
        "scenario": "a fictional dispute",
        "length": "200 words",
        "register": "formal",
        "branch_names": load_quotas(quota_path).branch_targets,
    }
    with pytest.raises(ValueError, match="leakage marker"):
        render_prompt(template, assignment)


def test_input_manifest_is_deterministic(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha.txt"
    beta = tmp_path / "beta.txt"
    alpha.write_text("alpha\n", encoding="utf-8")
    beta.write_text("beta\n", encoding="utf-8")
    assert input_manifest([beta, alpha]) == input_manifest([alpha, beta])
    assert list(input_manifest([beta, alpha])["files"]) == [str(alpha), str(beta)]


def test_shipped_template_passes_for_every_doc_type() -> None:
    quotas = load_quotas(QUOTAS)
    template = TEMPLATE.read_text(encoding="utf-8")
    for quota in quotas.doc_types:
        rendered = render_prompt(
            template,
            {
                "doc_type": quota.doc_type,
                "jurisdiction": quota.jurisdictions[0],
                "scenario": "a wholly fictional matter",
                "length": "350-500 words",
                "register": "professional legal prose",
                "branch_names": quotas.branch_targets,
            },
        )
        assert quota.doc_type in rendered
