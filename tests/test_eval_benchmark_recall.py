"""Contract tests for the public-data recall benchmark."""

from __future__ import annotations

from benchmark_recall import EXPECTED_DIGEST, QUERIES, _digest, benchmark_payload


def _snapshot(*, label: str = "Contract") -> list[list[dict[str, object]]]:
    return [
        [
            {
                "concept": {
                    "iri": "iri:contract",
                    "label": label,
                    "definition": "An agreement enforceable by law",
                    "alternative_labels": ("Agreement",),
                    "preferred_label": "Contract",
                    "branch": "Area of Law",
                    "parent_iris": ("iri:root",),
                },
                "score": 99.0,
            }
        ]
    ]


def test_benchmark_payload_has_explicit_single_sample_timing_contract() -> None:
    first = _snapshot()

    payload = benchmark_payload(
        first,
        first,
        elapsed_seconds=1.23456789,
        ontology_search_calls=17,
    )

    assert payload == {
        "elapsed_seconds": 1.234568,
        "output_equivalent": int(_digest(first) == EXPECTED_DIGEST),
        "deterministic": 1,
        "query_count": len(QUERIES),
        "ontology_search_calls": 17,
    }


def test_benchmark_digest_covers_non_iri_concept_metadata() -> None:
    assert _digest(_snapshot(label="Contract")) != _digest(_snapshot(label="Changed label"))
