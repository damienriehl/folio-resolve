from __future__ import annotations

import json

from folio_resolve import (
    InMemoryOntology,
    MatchPipeline,
    build_judge_prompt,
    enforce_verdict,
    parse_judge_json,
    strip_markdown_fences,
)
from folio_resolve.judge import build_contextual_rerank_prompt


def test_us_lj_01_supply_a_provider_neutral_judge(
    readme_ontology: InMemoryOntology,
) -> None:
    """US-LJ-01 supplies a local Judge and runs the pipeline judge stage offline."""
    calls: list[tuple[str, str]] = []

    class FakeJudge:
        def complete(self, system: str, user: str) -> str:
            calls.append((system, user))
            return """```json
{"judged": [{"iri_hash": "R-arb-rules", "adjusted_score": 97, "verdict": "confirmed"}]}
```"""

    system, user = build_judge_prompt(
        "rules of arbitration",
        [{"iri_hash": "R-arb-rules", "label": "Arbitration Rules", "score": 88.0}],
        document_type="Litigation",
    )
    rerank_prompt = build_contextual_rerank_prompt(
        "rules of arbitration",
        [{"folio_iri": "R-arb-rules", "folio_label": "Arbitration Rules"}],
        document_type="Litigation",
    )
    results = MatchPipeline(ontology=readme_ontology, judge=FakeJudge()).match(
        "rules of arbitration",
        run_judge=True,
    )

    assert "rules of arbitration" in user
    assert "Respond with ONLY valid JSON" in system
    assert "This document is: Litigation" in rerank_prompt
    assert len(calls) == 1
    assert results and results[0].iri == "R-arb-rules"


def test_us_lj_02_parse_hardened_model_output() -> None:
    """US-LJ-02 parses fenced JSON, drops bad rows, and enforces score bounds."""
    raw = """```json
{
  "judged": [
    {"iri_hash": "R-bad", "adjusted_score": "high", "verdict": "confirmed"},
    {"iri_hash": "R-over", "adjusted_score": 150, "verdict": "penalized"},
    {"iri_hash": "R-rejected", "adjusted_score": 75, "verdict": "rejected"}
  ]
}
```"""
    ranked = {"R-bad": 80.0, "R-over": 80.0, "R-rejected": 80.0}

    stripped = strip_markdown_fences(raw)
    parsed = parse_judge_json(raw, ranked)
    by_iri = {candidate.iri: candidate for candidate in parsed}

    assert json.loads(stripped)["judged"][0]["iri_hash"] == "R-bad"
    assert "R-bad" not in by_iri
    assert by_iri["R-over"].adjusted_score == 100.0
    assert by_iri["R-rejected"].adjusted_score == 0.0
    assert enforce_verdict(80.0, 75.0, "rejected") == 0.0


def test_us_lj_03_degrade_without_a_judge(readme_ontology: InMemoryOntology) -> None:
    """US-LJ-03 keeps deterministic candidates available for consumer-side marking."""
    pipeline = MatchPipeline(ontology=readme_ontology)

    without_judge_stage = pipeline.match("rules of arbitration")
    first = pipeline.match("rules of arbitration", run_judge=True)
    second = pipeline.match("rules of arbitration", run_judge=True)

    assert first == second == without_judge_stage
    assert first and first[0].iri == "R-arb-rules"

    for candidate in first:
        candidate.extraction_path = candidate.extraction_path or "unjudged"

    assert first[0].extraction_path == "label_search"
    assert all(candidate.extraction_path for candidate in first)
