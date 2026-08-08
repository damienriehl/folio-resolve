"""Scoring harness (U3; R5, R7; KTD2, KTD3, KTD7) — synthetic fixtures only.

No workbook, no FOLIO, no network: the ontology is ``InMemoryOntology`` and every candidate list
is canned, so the arithmetic under test is the harness's own and nothing else.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from folio_eval.answer_rule import (
    AnswerRuleConfig,
    commit_answers,
    load_config,
    load_or_create_config,
    rank_candidates,
    write_config,
)
from folio_eval.report import (
    bootstrap_ci,
    build_summary,
    changed_item_breakdown,
    changed_item_ci,
    f1_delta_ci,
    pair_items,
    write_item_csv,
)
from folio_eval.score import (
    Hierarchy,
    PipelineAdapter,
    ScoringError,
    build_pipeline,
    near_miss_bucket,
    score_items,
)
from folio_eval.selftest import (
    DeterminismError,
    OntologyPinError,
    assert_ontology_pin,
    run_determinism_selftest,
)
from folio_eval.splits import GoldItemRecord, load_gold
from test_eval_splits import (  # sibling test module (pytest rootdir on sys.path)
    gold_row,
    write_gold,
)

from folio_resolve import Concept, InMemoryOntology


@dataclass
class FakeCandidate:
    """The slice of ``MatchCandidate`` the answer rule reads."""

    iri: str
    label: str
    score: float
    extraction_path: str = "label_search"
    gated: bool = False


def item(
    item_id: str,
    gold: tuple[str, ...],
    *,
    stratum_id: str = "s1",
    firm: str = "firm1",
    leaf: str = "leaf",
) -> GoldItemRecord:
    return GoldItemRecord(
        item_id=item_id,
        firm=firm,
        stratum=stratum_id,
        stratum_id=stratum_id,
        ancestor_path=("L1", "L2"),
        leaf=leaf,
        input_text=f"L1 > L2 > {leaf}",
        gold_iris=frozenset(gold),
        flags=frozenset(),
        blank=False,
    )


# --------------------------------------------------------------------------------------
# Pinned math: hand-computed P/R/F1 on a toy gold + canned candidate lists
# --------------------------------------------------------------------------------------

# threshold=0.5 with no calibration steps => P(correct) = score/100, so the bar is score >= 50.
# top_k=5 caps the committed set.
GOLDEN_ITEMS: dict[str, tuple[str, tuple[str, ...], list[tuple[str, float]]]] = {
    # item_id: (stratum_id, gold IRIs, [(candidate IRI, score), ...])
    "A": ("s1", ("G1", "G2"), [("G1", 90.0), ("X1", 80.0), ("G2", 60.0), ("X2", 40.0)]),
    "B": ("s1", ("G3",), [("X3", 70.0)]),
    "C": ("s2", ("G4",), []),
    "D": (
        "s2",
        ("G5", "G6"),
        [("G5", 95.0), ("G6", 92.0), ("X4", 91.0), ("X5", 90.0), ("X6", 89.0), ("X7", 88.0)],
    ),
    "E": ("s2", ("G7",), [("G7", 99.0)]),
    # F pins recall@k to the RAW ranked list: G8 is ranked 2nd but sits below the probability
    # bar, so it is absent from the committed set and present in recall@3.
    "F": ("s1", ("G8",), [("X8", 90.0), ("G8", 30.0)]),
}

# Hand-computed from the table above (see the unit's plan notes):
#   A: committed {G1,X1,G2}      -> tp 2, fp 1, fn 0
#   B: committed {X3}            -> tp 0, fp 1, fn 1
#   C: committed {}              -> tp 0, fp 0, fn 1   (empty prediction: one FN per gold IRI)
#   D: committed {G5,G6,X4,X5,X6} (top-k cap drops X7) -> tp 2, fp 3, fn 0
#   E: committed {G7}            -> tp 1, fp 0, fn 0
#   F: committed {X8} (G8 below the bar) -> tp 0, fp 1, fn 1
#   totals: tp 5, fp 6, fn 3, gold 8
GOLDEN_TOTALS = {"tp": 5, "fp": 6, "fn": 3, "gold": 8}
GOLDEN_PRECISION = 5 / 11
GOLDEN_RECALL = 5 / 8
GOLDEN_F1 = 2 * 5 / (2 * 5 + 6 + 3)  # 10/19
GOLDEN_RECALL_AT_K = {1: 3 / 8, 3: 6 / 8, 5: 6 / 8, 10: 6 / 8}
GOLDEN_BY_STRATUM_F1 = {"s1": 4 / 9, "s2": 6 / 10}


def golden_items() -> list[GoldItemRecord]:
    return [
        item(item_id, gold, stratum_id=stratum_id, leaf=f"leaf {item_id}")
        for item_id, (stratum_id, gold, _) in GOLDEN_ITEMS.items()
    ]


def golden_predictor(record: GoldItemRecord) -> Sequence[FakeCandidate]:
    _, _, candidates = GOLDEN_ITEMS[record.item_id]
    return [FakeCandidate(iri=iri, label=iri, score=score) for iri, score in candidates]


def test_golden_micro_metrics_have_not_drifted() -> None:
    run = score_items(golden_items(), golden_predictor, config=AnswerRuleConfig(), slice_name="tune")
    assert run.overall.tp == GOLDEN_TOTALS["tp"]
    assert run.overall.fp == GOLDEN_TOTALS["fp"]
    assert run.overall.fn == GOLDEN_TOTALS["fn"]
    assert run.overall.gold == GOLDEN_TOTALS["gold"]
    assert run.overall.precision == pytest.approx(GOLDEN_PRECISION)
    assert run.overall.recall == pytest.approx(GOLDEN_RECALL)
    assert run.overall.f1 == pytest.approx(GOLDEN_F1)
    assert run.overall.exact_items == 1  # only E matches its gold set exactly
    assert run.overall.empty_prediction_items == 1  # only C


def test_golden_recall_at_k_comes_from_the_raw_ranked_list() -> None:
    run = score_items(golden_items(), golden_predictor, config=AnswerRuleConfig())
    for k, expected in GOLDEN_RECALL_AT_K.items():
        assert run.recall_at_k[k] == pytest.approx(expected), k
    # X7 is dropped by the top-k cap but still counts toward recall@10.
    d_score = next(score for score in run.item_scores if score.item_id == "D")
    assert len(d_score.ranked_iris) == 6
    assert len(d_score.committed_iris) == 5


def test_golden_per_stratum_metrics() -> None:
    run = score_items(golden_items(), golden_predictor, config=AnswerRuleConfig())
    for stratum_id, expected in GOLDEN_BY_STRATUM_F1.items():
        assert run.by_stratum[stratum_id].f1 == pytest.approx(expected), stratum_id
    assert set(run.by_firm) == {"firm1"}


def test_empty_prediction_yields_one_fn_per_gold_iri() -> None:
    records = [item("empty", ("G1", "G2", "G3"))]
    run = score_items(records, lambda _record: [], config=AnswerRuleConfig())
    assert run.overall.fn == 3
    assert run.overall.tp == 0
    assert run.overall.fp == 0
    assert run.overall.recall == 0.0


def test_blank_gold_row_is_refused_by_the_scorer() -> None:
    blank = GoldItemRecord(
        item_id="blank",
        firm="firm1",
        stratum="s1",
        stratum_id="s1",
        ancestor_path=(),
        leaf="leaf",
        input_text="leaf",
        gold_iris=frozenset(),
        flags=frozenset(),
        blank=True,
    )
    with pytest.raises(ScoringError, match="blank gold row"):
        score_items([blank], golden_predictor, config=AnswerRuleConfig())


def test_blank_rows_are_absent_from_the_scored_denominator(tmp_path: Path) -> None:
    rows = [
        gold_row("scored-1", leaf="Scored one"),
        gold_row("blank-1", leaf="Blank one", blank=True),
    ]
    gold = load_gold(write_gold(tmp_path, rows))
    assert len(gold.items) == 2
    assert [record.item_id for record in gold.scored()] == ["scored-1"]


# --------------------------------------------------------------------------------------
# AE5 — near misses score zero and land in the 1-hop bucket
# --------------------------------------------------------------------------------------


def hierarchy_fixture() -> Hierarchy:
    return Hierarchy.from_parent_map(
        {
            "R-root": (),
            "R-parent": ("R-root",),
            "R-child": ("R-parent",),
            "R-sibling": ("R-parent",),
            "R-grandchild": ("R-child",),
            "R-unrelated": ("R-root",),
        }
    )


def test_ae5_parent_prediction_scores_zero_and_lands_in_the_1hop_bucket() -> None:
    records = [item("ae5", ("R-child",))]
    run = score_items(
        records,
        lambda _record: [FakeCandidate(iri="R-parent", label="Parent", score=90.0)],
        config=AnswerRuleConfig(),
        hierarchy=hierarchy_fixture(),
    )
    assert run.overall.tp == 0
    assert run.overall.f1 == 0.0
    assert run.overall.fp == 1
    assert run.overall.fn == 1
    assert run.near_miss["parent_1hop"] == 1
    assert run.near_miss["unrelated"] == 0


def test_near_miss_buckets_are_distinct() -> None:
    hierarchy = hierarchy_fixture()
    gold = {"R-child"}
    assert near_miss_bucket("R-parent", gold, hierarchy) == "parent_1hop"
    assert near_miss_bucket("R-grandchild", gold, hierarchy) == "child_1hop"
    assert near_miss_bucket("R-root", gold, hierarchy) == "ancestor_2hop"
    assert near_miss_bucket("R-sibling", gold, hierarchy) == "sibling"
    assert near_miss_bucket("R-unrelated", gold, hierarchy) == "unrelated"


def test_hierarchy_builds_from_concepts() -> None:
    hierarchy = Hierarchy.from_concepts(
        [Concept(iri="A", label="A"), Concept(iri="B", label="B", parent_iris=("A",))]
    )
    assert hierarchy.parents_of("B") == frozenset({"A"})
    assert hierarchy.children_of("A") == frozenset({"B"})


# --------------------------------------------------------------------------------------
# Answer rule (KTD2)
# --------------------------------------------------------------------------------------


def test_answer_rule_is_gold_count_blind() -> None:
    candidates = [
        FakeCandidate(iri="G1", label="G1", score=90.0),
        FakeCandidate(iri="X1", label="X1", score=80.0),
        FakeCandidate(iri="X2", label="X2", score=55.0),
    ]
    config = AnswerRuleConfig()
    committed = [candidate.iri for candidate in commit_answers(candidates, config)]
    # Same candidates, wildly different gold sets -> identical committed set.
    for gold in [("G1",), ("G1", "X1", "X2", "G9"), tuple(f"G{i}" for i in range(20))]:
        run = score_items(
            [item("gold-blind", gold)],
            lambda _record: candidates,
            config=config,
        )
        assert list(run.item_scores[0].committed_iris) == committed


def test_answer_rule_applies_threshold_then_top_k() -> None:
    candidates = [FakeCandidate(iri=f"C{i}", label=f"C{i}", score=float(100 - i)) for i in range(10)]
    committed = commit_answers(candidates, AnswerRuleConfig(threshold=0.95, top_k=10))
    assert [c.iri for c in committed] == ["C0", "C1", "C2", "C3", "C4", "C5"]
    capped = commit_answers(candidates, AnswerRuleConfig(threshold=0.0, top_k=3))
    assert [c.iri for c in capped] == ["C0", "C1", "C2"]


def test_ties_break_by_score_desc_then_iri_asc() -> None:
    candidates = [
        FakeCandidate(iri="Zeta", label="Z", score=80.0),
        FakeCandidate(iri="Alpha", label="A", score=80.0),
        FakeCandidate(iri="Mid", label="M", score=90.0),
    ]
    ranked = rank_candidates(candidates, AnswerRuleConfig())
    assert [c.iri for c in ranked] == ["Mid", "Alpha", "Zeta"]


def test_duplicate_iris_keep_the_best_score() -> None:
    ranked = rank_candidates(
        [
            FakeCandidate(iri="G1", label="G1", score=50.0),
            FakeCandidate(iri="G1", label="G1", score=88.0),
        ],
        AnswerRuleConfig(),
    )
    assert len(ranked) == 1
    assert ranked[0].score == 88.0


def test_config_round_trips_and_hashes(tmp_path: Path) -> None:
    path = tmp_path / "harness_config_v1.json"
    config = AnswerRuleConfig(threshold=0.62, top_k=3, calibrated=True, calibration_steps=((45.0, 0.1),))
    digest = write_config(config, path)
    reloaded = load_config(path)
    assert reloaded == config
    assert reloaded.content_sha256() == digest
    assert AnswerRuleConfig(threshold=0.63, top_k=3).content_sha256() != digest


def test_default_config_is_marked_uncalibrated(tmp_path: Path) -> None:
    config = load_or_create_config(tmp_path / "harness_config_v1.json")
    assert config.calibrated is False
    assert config.threshold == 0.5
    assert config.top_k == 5
    assert "placeholder" in config.rationale
    assert (tmp_path / "harness_config_v1.json").exists()


def test_config_rejects_impossible_values() -> None:
    with pytest.raises(ValueError, match="threshold"):
        AnswerRuleConfig(threshold=1.5)
    with pytest.raises(ValueError, match="top_k"):
        AnswerRuleConfig(top_k=0)


# --------------------------------------------------------------------------------------
# Adapter (KTD3) over the synthetic ontology
# --------------------------------------------------------------------------------------


def test_adapter_passes_leaf_as_surface_term_and_ancestors_as_heading_terms() -> None:
    ontology = InMemoryOntology(
        [
            Concept(iri="R-arb", label="Arbitration Rules"),
            Concept(iri="R-defenses", label="Litigation Defenses"),
        ]
    )
    adapter = PipelineAdapter(build_pipeline(ontology, with_entity_ruler=False))
    record = item("adapter", ("R-arb",), leaf="rules of arbitration")
    results = adapter(record)
    assert any(candidate.iri == "R-arb" for candidate in results)


def test_build_pipeline_can_enable_multi_strategy_recall() -> None:
    ontology = InMemoryOntology(
        [
            Concept(
                iri="R-expectation",
                label="Expectation Damages",
                definition="A remedy for a broken agreement",
            ),
        ]
    )
    adapter = PipelineAdapter(
        build_pipeline(ontology, with_entity_ruler=False, with_multi_strategy_recall=True)
    )

    results = adapter(item("recall", ("R-expectation",), leaf="agreement remedy"))

    assert [candidate.iri for candidate in results] == ["R-expectation"]


def test_scoring_end_to_end_over_the_synthetic_ontology() -> None:
    concepts = [
        Concept(iri="R-arb", label="Arbitration Rules"),
        Concept(iri="R-findings", label="Proposed Findings of Fact"),
        Concept(iri="R-conclusions", label="Proposed Conclusions of Law"),
    ]
    ontology = InMemoryOntology(concepts)
    adapter = PipelineAdapter(build_pipeline(ontology, with_entity_ruler=True))
    records = [item("syn-1", ("R-arb",), leaf="rules of arbitration")]
    run = score_items(
        records, adapter, config=AnswerRuleConfig(threshold=0.8, top_k=1),
        hierarchy=Hierarchy.from_concepts(concepts),
    )
    assert run.overall.tp == 1
    assert run.overall.fn == 0


# --------------------------------------------------------------------------------------
# Reports (KTD1) and bootstrap CIs (AE4 inputs)
# --------------------------------------------------------------------------------------


def test_summary_is_committed_eligible(tmp_path: Path) -> None:
    gold = load_gold(write_gold(tmp_path, [gold_row("scored-1", leaf="A Secret Firm Label")]))
    run = score_items(golden_items(), golden_predictor, config=AnswerRuleConfig(), slice_name="tune")
    summary = build_summary(run, gold=gold, config=AnswerRuleConfig(), label="unit")
    text = json.dumps(summary)
    assert "A Secret Firm Label" not in text
    assert "leaf" not in text
    assert summary["gold_id"] == gold.gold_id
    assert summary["ontology_cache_sha256"] == gold.ontology_cache_sha256
    assert summary["harness_config_sha256"] == AnswerRuleConfig().content_sha256()
    assert set(summary["by_stratum"]) == {"s1", "s2"}  # type: ignore[arg-type]


def test_item_csv_carries_row_level_detail(tmp_path: Path) -> None:
    run = score_items(golden_items(), golden_predictor, config=AnswerRuleConfig(), slice_name="tune")
    path = write_item_csv(run.item_scores, tmp_path / "items.csv")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["item_id"] for row in rows] == ["A", "B", "C", "D", "E", "F"]
    assert rows[0]["gold_iris"] == "G1 G2"
    assert rows[3]["committed_count"] == "5"
    assert rows[2]["committed_iris"] == ""


def test_bootstrap_ci_is_reproducible_and_brackets_the_point() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0] * 20

    def mean(units: Sequence[float]) -> float:
        return sum(units) / len(units)

    first = bootstrap_ci(values, mean, n_resamples=200, seed=7)
    second = bootstrap_ci(values, mean, n_resamples=200, seed=7)
    assert first == second
    assert first.low <= first.point <= first.high


def test_f1_delta_and_changed_item_cis(tmp_path: Path) -> None:
    before = score_items(golden_items(), golden_predictor, config=AnswerRuleConfig()).item_scores

    def better(record: GoldItemRecord) -> Sequence[FakeCandidate]:
        return [FakeCandidate(iri=iri, label=iri, score=99.0) for iri in sorted(record.gold_iris)]

    after = score_items(golden_items(), better, config=AnswerRuleConfig()).item_scores
    delta = f1_delta_ci(before, after, n_resamples=200, seed=11)
    assert delta.point > 0
    assert delta.excludes_zero
    changed = changed_item_ci(before, after, n_resamples=200, seed=11)
    assert changed.point == 5.0  # A, B, C, D, F flip to exact; E already was
    breakdown = changed_item_breakdown(pair_items(before, after))
    assert breakdown == {"items": 6, "improved": 5, "regressed": 0, "unchanged": 1, "net": 5}


# --------------------------------------------------------------------------------------
# KTD7 — determinism self-test and the ontology pin
# --------------------------------------------------------------------------------------


def test_determinism_selftest_passes_on_a_stable_payload() -> None:
    result = run_determinism_selftest("folio_eval.selftest:stable_payload")
    assert result.deterministic
    assert result.first_seed != result.second_seed


def test_determinism_selftest_catches_hash_order_dependence() -> None:
    with pytest.raises(DeterminismError, match="determinism self-test FAILED"):
        run_determinism_selftest("folio_eval.selftest:unstable_payload")


def test_determinism_selftest_passes_on_a_synthetic_scoring_pass() -> None:
    result = run_determinism_selftest("folio_eval.selftest:synthetic_scoring_payload")
    assert result.deterministic


def test_ontology_pin_aborts_when_the_cache_file_is_absent(tmp_path: Path) -> None:
    with pytest.raises(OntologyPinError, match="not found"):
        assert_ontology_pin("deadbeef", cache_path=tmp_path / "missing.owl")


def test_ontology_pin_aborts_on_a_hash_change(tmp_path: Path) -> None:
    fake = tmp_path / "cache.owl"
    fake.write_bytes(b"<owl/>")
    with pytest.raises(OntologyPinError, match="ontology pin mismatch"):
        assert_ontology_pin("f" * 64, cache_path=fake)
    pin = assert_ontology_pin("", cache_path=fake)
    assert pin.path == fake
    assert len(pin.sha256) == 64
