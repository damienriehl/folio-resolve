"""Judge — verdict enforcement + domain-prior prompt threading."""

from __future__ import annotations

import json

from folio_resolve import build_judge_prompt, enforce_verdict, parse_judge_json
from folio_resolve.judge import (
    SCORE_CALIBRATION,
    build_contextual_rerank_prompt,
    strip_markdown_fences,
)


def test_enforce_rejected_forces_zero() -> None:
    assert enforce_verdict(80.0, 50.0, "rejected") == 0.0


def test_enforce_confirmed_clamps_within_5() -> None:
    assert enforce_verdict(80.0, 95.0, "confirmed") == 85.0


def test_enforce_boost_capped_at_25() -> None:
    assert enforce_verdict(50.0, 200.0, "boosted") == 75.0


def test_parse_drops_hallucinated_iris() -> None:
    ranked = {"R1": 80.0}
    raw = json.dumps(
        {
            "judged": [
                {"iri_hash": "R1", "adjusted_score": 82, "verdict": "confirmed", "reasoning": "ok"},
                {"iri_hash": "FAKE", "adjusted_score": 90, "verdict": "boosted", "reasoning": "halluc"},
            ]
        }
    )
    out = parse_judge_json(raw, ranked)
    assert len(out) == 1
    assert out[0].iri == "R1"


def test_parse_bad_json_returns_empty() -> None:
    assert parse_judge_json("not json", {"R1": 80.0}) == []


def test_judge_prompt_threads_domain_prior() -> None:
    _system, user = build_judge_prompt(
        "The defenses raised were meritless.",
        [{"iri_hash": "R-defenses", "label": "Litigation Defenses"}],
        document_type="Litigation / Trial Advocacy treatise",
    )
    assert "Litigation / Trial Advocacy treatise" in user
    assert "Document Type" in user


def test_calibration_block_in_prompt() -> None:
    system, _user = build_judge_prompt("x", [])
    assert SCORE_CALIBRATION in system
    assert "90+" in system


def test_contextual_rerank_prompt_injects_domain() -> None:
    prompt = build_contextual_rerank_prompt(
        "Some excerpt", [{"folio_iri": "R1", "folio_label": "X"}], document_type="Litigation"
    )
    assert "This document is: Litigation" in prompt


# --- Transport hardening (v0.2.1) ------------------------------------------------------------
#
# Gaps surfaced by the folio-mapper migration (SCHEDULE.md row 3): mapper — the donor of these
# very verdict rules — could not delete its local parse loop because the library's
# parse_judge_json did not strip markdown fences, did not clamp to 0-100, and RAISED on a
# non-numeric adjusted_score. Each behavior below is mapper's, promoted here.


def test_parse_strips_markdown_fences() -> None:
    raw = '```json\n{"judged": [{"iri_hash": "R1", "adjusted_score": 82, "verdict": "confirmed"}]}\n```'
    out = parse_judge_json(raw, {"R1": 80.0})
    assert len(out) == 1
    assert out[0].adjusted_score == 82.0


def test_parse_strips_bare_fences() -> None:
    raw = '```\n{"judged": [{"iri_hash": "R1", "adjusted_score": 82, "verdict": "confirmed"}]}\n```'
    assert len(parse_judge_json(raw, {"R1": 80.0})) == 1


def test_strip_markdown_fences_is_idempotent_on_clean_json() -> None:
    clean = '{"judged": []}'
    assert strip_markdown_fences(clean) == clean


def test_parse_drops_non_numeric_score_instead_of_raising() -> None:
    raw = json.dumps({"judged": [{"iri_hash": "R1", "adjusted_score": "high", "verdict": "confirmed"}]})
    assert parse_judge_json(raw, {"R1": 80.0}) == []


def test_parse_drops_boolean_score() -> None:
    raw = json.dumps({"judged": [{"iri_hash": "R1", "adjusted_score": True, "verdict": "confirmed"}]})
    assert parse_judge_json(raw, {"R1": 80.0}) == []


def test_parse_clamps_out_of_range_penalty_to_scale() -> None:
    """A "penalized" verdict is the one path enforce_verdict does not bound on its own."""
    over = json.dumps({"judged": [{"iri_hash": "R1", "adjusted_score": 150, "verdict": "penalized"}]})
    under = json.dumps({"judged": [{"iri_hash": "R1", "adjusted_score": -20, "verdict": "penalized"}]})
    assert parse_judge_json(over, {"R1": 80.0})[0].adjusted_score == 100.0
    assert parse_judge_json(under, {"R1": 80.0})[0].adjusted_score == 0.0


def test_parse_survives_non_list_judged() -> None:
    assert parse_judge_json(json.dumps({"judged": {"iri_hash": "R1"}}), {"R1": 80.0}) == []


def test_parse_survives_non_dict_rows() -> None:
    raw = json.dumps({"judged": ["R1", 42, {"iri_hash": "R1", "adjusted_score": 80, "verdict": "confirmed"}]})
    assert len(parse_judge_json(raw, {"R1": 80.0})) == 1


def test_parse_survives_non_object_payload() -> None:
    assert parse_judge_json("[1, 2, 3]", {"R1": 80.0}) == []


def test_parse_survives_none_input() -> None:
    assert parse_judge_json(None, {"R1": 80.0}) == []  # type: ignore[arg-type]


def test_parse_coerces_non_string_reasoning() -> None:
    raw = json.dumps({"judged": [{"iri_hash": "R1", "adjusted_score": 80, "verdict": "confirmed", "reasoning": 7}]})
    assert parse_judge_json(raw, {"R1": 80.0})[0].reasoning == ""


def test_parse_defaults_missing_score_to_the_original() -> None:
    raw = json.dumps({"judged": [{"iri_hash": "R1", "verdict": "confirmed"}]})
    assert parse_judge_json(raw, {"R1": 80.0})[0].adjusted_score == 80.0
