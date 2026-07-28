"""Failure clustering, calibration fit, and the threshold x k grid (U4; R8; KTD2, KTD7).

Synthetic only: no workbook, no FOLIO, no network. The direct-search diagnostic is exercised
through a *fake* callable so the truncated-vs-unreachable distinction — the one that decides
iteration 1 — is tested without a live ontology.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from folio_eval.answer_rule import AnswerRuleConfig, commit_from_ranked, rank_candidates
from folio_eval.clusters import (
    DEFAULT_K_GRID,
    DEFAULT_THRESHOLD_GRID,
    FP_CLUSTERS,
    MISS_CLUSTERS,
    REACHABILITY_CLASSES,
    CachedDirectSearch,
    ClusterAnalysis,
    GridPoint,
    MissClassifier,
    MissSignals,
    RawCandidate,
    ReplayPredictor,
    SurfaceLeakError,
    assert_no_surfaces,
    calibration_rationale,
    calibration_samples,
    classify_false_positive,
    classify_miss,
    cluster_run,
    collect_raw_candidates,
    compress_steps,
    fit_calibration,
    frontier_around,
    grid_search,
    query_variants,
    rank_all,
    reachability_of,
    scan_for_surfaces,
    select_point,
    surface_strings,
    token_jaccard,
    tokens,
    write_cluster_detail,
)
from folio_eval.score import Hierarchy, score_items
from folio_eval.splits import GoldItemRecord, load_gold
from test_eval_splits import gold_row, write_gold  # sibling module (pytest rootdir on sys.path)

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


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
    leaf: str = "Fund Formation",
    firm: str = "firm1",
    stratum_id: str = "s1",
) -> GoldItemRecord:
    return GoldItemRecord(
        item_id=item_id,
        firm=firm,
        stratum=stratum_id,
        stratum_id=stratum_id,
        ancestor_path=("Corporate", "Funds"),
        leaf=leaf,
        input_text=f"Corporate > Funds > {leaf}",
        gold_iris=frozenset(gold),
        flags=frozenset(),
        blank=False,
    )


def ranked(*pairs: tuple[str, float]) -> list[object]:
    """The item's full raw ranked list, labelled from :data:`LABELS` as the pipeline would be."""
    return rank_candidates(  # type: ignore[return-value]
        [
            FakeCandidate(iri=iri, label=LABELS.get(iri, (iri,))[0], score=score)
            for iri, score in pairs
        ],
        AnswerRuleConfig(),
    )


# --------------------------------------------------------------------------------------
# The taxonomy: every rule classifies a hand-built miss correctly
# --------------------------------------------------------------------------------------


def test_a_gold_iri_present_in_the_ranked_list_is_ranked_below_cutoff() -> None:
    signals = MissSignals(in_ranked=True, reachable_at_limit=True, exact_label_match=True)
    assert classify_miss(signals) == "ranked_below_cutoff"
    assert reachability_of("ranked_below_cutoff") == "ranked_below_cutoff"


def test_reachability_dominates_every_explanatory_signal() -> None:
    """A truncated miss stays truncated even when synonymy/homonym signals also fire.

    This ordering is the unit's whole point: the cheap fixes must be attributed before any
    unreachability story is credited (the recall port is only justified by what is left over).
    """
    loud = dict(
        exact_label_match=True,
        homonym_collision=True,
        hierarchy_neighbor_seen=True,
        max_token_jaccard=0.0,
    )
    assert classify_miss(MissSignals(in_ranked=True, reachable_at_limit=False, **loud)) == (
        "ranked_below_cutoff"
    )
    assert classify_miss(MissSignals(in_ranked=False, reachable_at_limit=True, **loud)) == (
        "candidate_gap_truncated"
    )


def test_unreachable_with_an_exact_normalized_label_is_a_normalization_miss() -> None:
    signals = MissSignals(in_ranked=False, reachable_at_limit=False, exact_label_match=True)
    assert classify_miss(signals) == "normalization"
    assert reachability_of("normalization") == "candidate_gap_unreachable"


def test_unreachable_with_a_surface_colliding_false_positive_is_a_homonym() -> None:
    signals = MissSignals(
        in_ranked=False, reachable_at_limit=False, homonym_collision=True, max_token_jaccard=0.5
    )
    assert classify_miss(signals) == "homonym_or_wrong_sense"


def test_unreachable_with_a_1hop_neighbour_in_the_ranked_list_is_a_hierarchy_near_miss() -> None:
    signals = MissSignals(
        in_ranked=False,
        reachable_at_limit=False,
        hierarchy_neighbor_seen=True,
        max_token_jaccard=0.4,
    )
    assert classify_miss(signals) == "hierarchy_near_miss"


def test_unreachable_with_zero_token_overlap_is_synonymy() -> None:
    signals = MissSignals(in_ranked=False, reachable_at_limit=False, max_token_jaccard=0.0)
    assert classify_miss(signals) == "synonymy"


def test_unreachable_with_partial_overlap_is_the_residual_candidate_gap() -> None:
    signals = MissSignals(in_ranked=False, reachable_at_limit=False, max_token_jaccard=0.25)
    assert classify_miss(signals) == "candidate_gap_unreachable"


def test_every_cluster_rolls_up_into_the_three_way_axis() -> None:
    assert set(reachability_of(name) for name in MISS_CLUSTERS) == set(REACHABILITY_CLASSES)
    with pytest.raises(ValueError):
        reachability_of("not_a_cluster")


def test_false_positives_split_into_near_miss_and_unrelated() -> None:
    assert classify_false_positive("parent_1hop") == "fp_near_miss"
    assert classify_false_positive("child_1hop") == "fp_near_miss"
    for bucket in ("ancestor_2hop", "descendant_2hop", "sibling", "unrelated"):
        assert classify_false_positive(bucket) == "fp_unrelated"
    assert set(FP_CLUSTERS) == {"fp_near_miss", "fp_unrelated"}


# --------------------------------------------------------------------------------------
# Token overlap
# --------------------------------------------------------------------------------------


def test_tokens_drop_function_words_and_fold_case_and_punctuation() -> None:
    assert tokens("Rules of Arbitration") == frozenset({"rules", "arbitration"})
    assert tokens("Fund  Formation") == tokens("fund formation")


def test_token_jaccard_is_zero_for_disjoint_labels() -> None:
    assert token_jaccard(tokens("Fund Formation"), tokens("Escrow Agreement")) == 0.0
    assert token_jaccard(tokens("Fund Formation"), tokens("Fund Formation")) == 1.0
    assert 0.0 < token_jaccard(tokens("Fund Formation"), tokens("Fund Administration")) < 1.0


# --------------------------------------------------------------------------------------
# Truncated vs unreachable, through a fake direct-search callable
# --------------------------------------------------------------------------------------

HIERARCHY = Hierarchy.from_parent_map(
    {
        "G-parent": (),
        "G-gold": ("G-parent",),
        "G-child": ("G-gold",),
        "X-other": ("G-parent",),
        "X-far": (),
    }
)

LABELS = {
    "G-gold": ("Fund Formation",),
    "G-parent": ("Funds",),
    "G-child": ("Fund Formation Advice",),
    "X-other": ("Fund Formation",),
    "X-far": ("Escrow Agreement",),
}


def classifier(index: dict[str, frozenset[str]], *, calls: list[str] | None = None) -> MissClassifier:
    """A classifier whose 'limit=200' search is a hand-built query -> IRIs table."""

    def search(query: str) -> frozenset[str]:
        if calls is not None:
            calls.append(query)
        return index.get(query, frozenset())

    return MissClassifier(
        hierarchy=HIERARCHY,
        labels_for=lambda iri: LABELS.get(iri, ()),
        direct=CachedDirectSearch(search),
    )


def score_one(record: GoldItemRecord, candidates: Sequence[tuple[str, float]]) -> object:
    run = score_items(
        [record],
        lambda _record: [
            FakeCandidate(iri=iri, label=LABELS.get(iri, (iri,))[0], score=score)
            for iri, score in candidates
        ],
        config=AnswerRuleConfig(),
        hierarchy=HIERARCHY,
        slice_name="tune",
    )
    return run.item_scores[0]


def test_a_gold_iri_the_limit200_search_returns_is_truncated_not_unreachable() -> None:
    """The cluster that decides whether the cheap limit fix, or the recall port, comes first."""
    # Gold is G-child ("Fund Formation Advice") under the leaf "Fund Formation": partial token
    # overlap, no exact label match, no 1-hop neighbour ranked — so nothing but the direct-search
    # answer separates the two outcomes.
    record = item("i-1", ("G-child",), leaf="Fund Formation")
    score = score_one(record, [("X-far", 90.0)])
    reachable = classifier({"Fund Formation": frozenset({"G-child", "X-far"})})
    misses, _ = reachable.classify_item(score, ranked=ranked(("X-far", 90.0)))  # type: ignore[arg-type]
    assert [row.cluster for row in misses] == ["candidate_gap_truncated"]
    assert [row.reachability for row in misses] == ["candidate_gap_truncated"]
    assert misses[0].reachable_queries == ("Fund Formation",)

    unreachable = classifier({"Fund Formation": frozenset({"X-far"})})
    misses, _ = unreachable.classify_item(score, ranked=ranked(("X-far", 90.0)))  # type: ignore[arg-type]
    assert [row.cluster for row in misses] == ["candidate_gap_unreachable"]
    assert [row.reachability for row in misses] == ["candidate_gap_unreachable"]
    assert misses[0].reachable_queries == ()


def test_the_diagnostic_probes_the_pipelines_own_generated_search_terms() -> None:
    """``_expand`` searches every ``decompose()`` part, so the probe must too."""
    variants = query_variants("Proposed Findings of Fact and Conclusions of Law")
    assert variants[0] == "Proposed Findings of Fact and Conclusions of Law"
    assert "Proposed Conclusions of Law" in variants

    record = item("i-2", ("G-gold",), leaf="Proposed Findings of Fact and Conclusions of Law")
    score = score_one(record, [])
    via_part = classifier({"Proposed Conclusions of Law": frozenset({"G-gold"})})
    misses, _ = via_part.classify_item(score, ranked=[])
    assert misses[0].cluster == "candidate_gap_truncated"
    assert misses[0].reachable_queries == ("Proposed Conclusions of Law",)


def test_a_gold_iri_already_in_the_ranked_list_is_never_probed() -> None:
    """No ontology query is spent on a miss the pipeline already ranked."""
    record = item("i-3", ("G-gold",), leaf="Fund Formation")
    score = score_one(record, [("X-far", 90.0), ("G-gold", 46.0)])
    calls: list[str] = []
    engine = classifier({}, calls=calls)
    misses, _ = engine.classify_item(
        score, ranked=ranked(("X-far", 90.0), ("G-gold", 46.0))  # type: ignore[arg-type]
    )
    assert [row.cluster for row in misses] == ["ranked_below_cutoff"]
    assert misses[0].gold_rank == 2
    assert calls == []
    assert engine.items_probed == 0


def test_the_direct_search_is_cached_per_unique_query_string() -> None:
    calls: list[str] = []
    cache = CachedDirectSearch(lambda query: (calls.append(query), frozenset({"G-gold"}))[1])
    assert cache("Fund Formation") == frozenset({"G-gold"})
    assert cache("Fund Formation") == frozenset({"G-gold"})
    assert cache("Escrow") == frozenset({"G-gold"})
    assert calls == ["Fund Formation", "Escrow"]
    assert cache.lookups == 3
    assert cache.unique_queries == 2


def test_signals_are_recorded_even_when_another_cluster_wins() -> None:
    """Every boolean is on every row so U5/U7 can re-slice without re-running the pipeline."""
    record = item("i-4", ("G-gold",), leaf="Fund Formation")
    score = score_one(record, [("X-other", 90.0), ("G-child", 80.0)])
    engine = classifier({"Fund Formation": frozenset({"G-gold"})})
    misses, fps = engine.classify_item(
        score, ranked=ranked(("X-other", 90.0), ("G-child", 80.0))  # type: ignore[arg-type]
    )
    row = misses[0]
    assert row.cluster == "candidate_gap_truncated"  # reachability wins
    assert row.signals.exact_label_match is True  # ... and the rest is still on the record
    assert row.signals.homonym_collision is True
    assert row.signals.hierarchy_neighbor_seen is True
    assert row.signals.max_token_jaccard == 1.0
    assert {fp.cluster for fp in fps} == {"fp_near_miss", "fp_unrelated"}
    assert {fp.predicted_iri: fp.near_miss for fp in fps} == {
        "G-child": "child_1hop",
        "X-other": "sibling",
    }


def test_cluster_run_aggregates_counts_by_cluster_axis_and_firm() -> None:
    # b-1's leaf shares no token with its gold concept's label and collides with nothing that was
    # committed, so it lands in synonymy rather than in the homonym bucket ahead of it.
    records = [
        item("a-1", ("G-gold",), leaf="Fund Formation"),
        item("b-1", ("G-gold",), leaf="Trust Deed", firm="firm2", stratum_id="s2"),
    ]
    run = score_items(
        records,
        lambda record: [FakeCandidate(iri="X-far", label="Escrow Agreement", score=90.0)],
        config=AnswerRuleConfig(),
        hierarchy=HIERARCHY,
        slice_name="tune",
    )
    engine = classifier({"Fund Formation": frozenset({"G-gold"})})
    analysis = cluster_run(
        run,
        classifier=engine,
        ranked_by_item={record.item_id: ranked(("X-far", 90.0)) for record in records},  # type: ignore[misc]
        progress_every=0,
    )
    assert analysis.cluster_counts()["candidate_gap_truncated"] == 1
    assert analysis.cluster_counts()["synonymy"] == 1
    assert analysis.reachability_counts() == {
        "ranked_below_cutoff": 0,
        "candidate_gap_truncated": 1,
        "candidate_gap_unreachable": 1,
    }
    assert analysis.clusters_by_firm()["firm1"]["candidate_gap_truncated"] == 1
    assert analysis.clusters_by_firm()["firm2"]["synonymy"] == 1
    payload = analysis.to_json()
    assert set(payload["clusters"]) == set(MISS_CLUSTERS)  # type: ignore[arg-type]
    assert payload["misses"] == 2


def test_each_slice_reports_its_own_direct_search_cost_not_a_running_total() -> None:
    """The query cache is shared across slices on purpose; the per-slice numbers are deltas."""
    engine = classifier({})
    analyses = []
    for slice_name, leaf in (("tune", "Fund Formation"), ("firm2", "Escrow Agreement")):
        record = item(f"{slice_name}-1", ("G-gold",), leaf=leaf)
        run = score_items(
            [record],
            lambda _record: [],
            config=AnswerRuleConfig(),
            hierarchy=HIERARCHY,
            slice_name=slice_name,
        )
        analyses.append(
            cluster_run(run, classifier=engine, ranked_by_item={}, progress_every=0)
        )
    assert [analysis.items_probed for analysis in analyses] == [1, 1]
    assert [analysis.direct["new_unique_queries"] for analysis in analyses] == [1, 1]
    assert analyses[-1].direct["cache_unique_queries_total"] == 2


def test_cluster_detail_rows_round_trip_as_jsonl(tmp_path: Path) -> None:
    record = item("i-5", ("G-child",), leaf="Fund Formation")
    score = score_one(record, [("X-far", 90.0)])
    engine = classifier({})
    misses, fps = engine.classify_item(score, ranked=ranked(("X-far", 90.0)))  # type: ignore[arg-type]
    analysis = ClusterAnalysis(slice_name="tune", miss_rows=misses, fp_rows=fps)
    path = write_cluster_detail([analysis], tmp_path / "clusters_v1.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["kind"] for row in rows] == ["fn", "fp"]
    assert rows[0]["gold_iri"] == "G-child"
    assert rows[0]["cluster"] == "candidate_gap_unreachable"
    assert rows[1]["predicted_iri"] == "X-far"


# --------------------------------------------------------------------------------------
# Calibration fit and the threshold x k grid (KTD2)
# --------------------------------------------------------------------------------------

# Six items whose ranked lists give the fit a clean signal: high scores are usually right, low
# scores are usually wrong, score 70.0 is tied across a correct and a wrong candidate, and f-5's
# gold IRI never appears at all (the candidate-gap case the fit must tolerate).
FIT_ITEMS: dict[str, tuple[tuple[str, ...], list[tuple[str, float]]]] = {
    "f-1": (("G1",), [("G1", 95.0), ("X1", 55.0)]),
    "f-2": (("G2",), [("G2", 92.0), ("X2", 60.0), ("X3", 50.0)]),
    "f-3": (("G3",), [("G3", 88.0), ("X4", 70.0)]),
    "f-4": (("G4",), [("X5", 80.0), ("G4", 70.0)]),
    "f-5": (("G5",), [("X6", 80.0), ("X7", 47.0)]),
    "f-6": (("G6",), [("G6", 99.0), ("X8", 46.0), ("X9", 45.0)]),
}


def fit_cache() -> dict[str, tuple[RawCandidate, ...]]:
    return {
        item_id: tuple(
            RawCandidate(iri=iri, label=iri, score=score) for iri, score in candidates
        )
        for item_id, (_, candidates) in FIT_ITEMS.items()
    }


def fit_gold() -> dict[str, frozenset[str]]:
    return {item_id: frozenset(gold) for item_id, (gold, _) in FIT_ITEMS.items()}


def test_calibration_samples_are_ordered_deterministically_and_labelled_by_gold() -> None:
    samples = calibration_samples(rank_all(fit_cache(), AnswerRuleConfig()), fit_gold())
    assert len(samples) == sum(len(candidates) for _, candidates in FIT_ITEMS.values())
    assert sum(1 for sample in samples if sample.verdict == "correct") == 5  # f-5's gold is absent
    scores = [sample.score for sample in samples]
    assert scores == sorted(scores)
    # Within a tied score the positives come first, so pool-adjacent-violators collapses the
    # block to its true mean instead of leaving a trailing 1.0 step that ``probability`` would
    # read as certainty.
    tied = [sample.verdict for sample in samples if sample.score == 70.0]
    assert tied == ["correct", "wrong"]


def test_a_tied_score_block_calibrates_to_its_mean_not_its_last_sample() -> None:
    # t-1 (wrong) precedes t-2 (correct) in item-id order, so the *natural* order of the tied
    # block at score 50.0 is [0, 1] — which does not violate monotonicity and would survive as two
    # separate steps, leaving ``probability(50.0) == 1.0``. Only the (score, -target) re-ordering
    # pools the block to its true mean.
    cache = {
        "t-1": (RawCandidate(iri="X1", label="X1", score=50.0),),
        "t-2": (RawCandidate(iri="G1", label="G1", score=50.0),),
        "t-3": (RawCandidate(iri="G2", label="G2", score=90.0),),
    }
    gold = {"t-1": frozenset({"G0"}), "t-2": frozenset({"G1"}), "t-3": frozenset({"G2"})}
    steps = fit_calibration(calibration_samples(rank_all(cache, AnswerRuleConfig()), gold))
    assert AnswerRuleConfig(calibration_steps=steps).calibration().probability(50.0) == 0.5


def test_calibration_fit_is_reproducible_from_the_same_inputs() -> None:
    cache, gold = fit_cache(), fit_gold()
    first = fit_calibration(calibration_samples(rank_all(cache, AnswerRuleConfig()), gold))
    shuffled = dict(reversed(list(cache.items())))
    second = fit_calibration(calibration_samples(rank_all(shuffled, AnswerRuleConfig()), gold))
    assert first == second
    assert first == tuple(sorted(first))  # monotone nondecreasing in both coordinates


def test_compress_steps_drops_repeats_without_changing_any_probability() -> None:
    raw = [(10.0, 0.0), (20.0, 0.0), (30.0, 0.5), (40.0, 0.5), (50.0, 1.0)]
    compressed = compress_steps(raw)
    assert compressed == ((10.0, 0.0), (30.0, 0.5), (50.0, 1.0))
    from folio_resolve.calibration import ScoreCalibration

    dense, sparse = ScoreCalibration(list(raw)), ScoreCalibration(list(compressed))
    for score in (0.0, 15.0, 25.0, 35.0, 45.0, 55.0, 100.0):
        assert dense.probability(score) == sparse.probability(score)


def test_grid_search_selects_the_micro_f1_maximum_and_is_reproducible() -> None:
    cache, gold = fit_cache(), fit_gold()
    steps = fit_calibration(calibration_samples(rank_all(cache, AnswerRuleConfig()), gold))
    ranked_by_item = rank_all(cache, AnswerRuleConfig(calibrated=True, calibration_steps=steps))
    grid = grid_search(ranked_by_item, gold, calibration_steps=steps)
    assert len(grid) == len(DEFAULT_THRESHOLD_GRID) * len(DEFAULT_K_GRID)

    chosen = select_point(grid)
    assert chosen.f1 == max(point.f1 for point in grid)
    # Re-running from a differently-ordered mapping reproduces the identical cell.
    again = select_point(
        grid_search(dict(reversed(list(ranked_by_item.items()))), gold, calibration_steps=steps)
    )
    assert (again.threshold, again.top_k, again.tp, again.fp, again.fn) == (
        chosen.threshold,
        chosen.top_k,
        chosen.tp,
        chosen.fp,
        chosen.fn,
    )


def test_ties_break_to_the_smaller_k_then_the_higher_threshold() -> None:
    grid = [
        GridPoint(threshold=0.5, top_k=3, tp=4, fp=1, fn=1),
        GridPoint(threshold=0.6, top_k=2, tp=4, fp=1, fn=1),
        GridPoint(threshold=0.7, top_k=2, tp=4, fp=1, fn=1),
        GridPoint(threshold=0.4, top_k=1, tp=3, fp=0, fn=2),
    ]
    chosen = select_point(grid)
    assert (chosen.threshold, chosen.top_k) == (0.7, 2)
    with pytest.raises(ValueError):
        select_point([])


def test_frontier_reports_the_chosen_cell_and_its_neighbours() -> None:
    cache, gold = fit_cache(), fit_gold()
    steps = fit_calibration(calibration_samples(rank_all(cache, AnswerRuleConfig()), gold))
    grid = grid_search(rank_all(cache, AnswerRuleConfig()), gold, calibration_steps=steps)
    chosen = select_point(grid)
    frontier = frontier_around(grid, chosen)
    assert any(
        (point.threshold, point.top_k) == (chosen.threshold, chosen.top_k) for point in frontier
    )
    for point in frontier:
        assert abs(point.top_k - chosen.top_k) <= 1
        assert abs(
            DEFAULT_THRESHOLD_GRID.index(point.threshold)
            - DEFAULT_THRESHOLD_GRID.index(chosen.threshold)
        ) <= 1


def test_the_fitted_rule_never_reads_a_gold_count() -> None:
    """KTD2's invariant survives calibration: the grid picks constants, not per-item targets."""
    cache, gold = fit_cache(), fit_gold()
    steps = fit_calibration(calibration_samples(rank_all(cache, AnswerRuleConfig()), gold))
    chosen = select_point(grid_search(rank_all(cache, AnswerRuleConfig()), gold, calibration_steps=steps))
    config = AnswerRuleConfig(
        threshold=chosen.threshold, top_k=chosen.top_k, calibrated=True, calibration_steps=steps
    )
    candidates = rank_candidates(
        [FakeCandidate(iri=f"C{index}", label=f"C{index}", score=95.0 - index) for index in range(8)],
        config,
    )
    committed = [candidate.iri for candidate in commit_from_ranked(candidates, config)]
    for gold_size in (0, 1, 3, 8):
        # Feeding the same candidates under any gold set at all commits the same answer.
        assert [
            candidate.iri for candidate in commit_from_ranked(candidates, config)
        ] == committed
        assert len(committed) <= config.top_k
        assert gold_size >= 0  # the gold set is simply never an input


def test_calibration_rationale_carries_counts_and_hashes_only() -> None:
    line = calibration_rationale(
        chosen=GridPoint(threshold=0.6, top_k=2, tp=4, fp=1, fn=1),
        samples=100,
        positives=12,
        items=50,
        steps=7,
        gold_id="v1-abcdef123456",
        ontology_sha256="f" * 64,
    )
    assert "threshold=0.6" in line and "top_k=2" in line
    assert "TUNE slice only" in line
    assert "R9" in line


# --------------------------------------------------------------------------------------
# Replay + leak scan
# --------------------------------------------------------------------------------------


def test_one_pipeline_pass_replays_into_an_identical_scored_run() -> None:
    records = [item(f"r-{index}", ("G-gold",), leaf="Fund Formation") for index in range(3)]
    calls: list[str] = []

    def predict(record: GoldItemRecord) -> Sequence[FakeCandidate]:
        calls.append(record.item_id)
        return [FakeCandidate(iri="G-gold", label="Fund Formation", score=90.0)]

    cache = collect_raw_candidates(records, predict, progress_every=0)
    assert sorted(calls) == [record.item_id for record in records]
    live = score_items(records, predict, config=AnswerRuleConfig(), slice_name="tune")
    replayed = score_items(
        records, ReplayPredictor(cache), config=AnswerRuleConfig(), slice_name="tune"
    )
    assert [score.to_json() for score in replayed.item_scores] == [
        score.to_json() for score in live.item_scores
    ]
    assert len(calls) == 2 * len(records)  # the replay added no pipeline calls


def test_leak_scan_catches_a_surface_string_in_a_committed_artefact(tmp_path: Path) -> None:
    rows = [gold_row("g-1", leaf="Fund Formation", stratum="Corporate", level2="Funds")]
    gold = load_gold(write_gold(tmp_path, rows))
    surfaces = surface_strings(gold)
    assert "Fund Formation" in surfaces
    assert scan_for_surfaces('{"clusters": {"synonymy": 3}}', surfaces) == []
    assert assert_no_surfaces('{"misses": 3}', surfaces, what="report") == len(surfaces)
    with pytest.raises(SurfaceLeakError):
        assert_no_surfaces('{"leaf": "Fund Formation"}', surfaces, what="report")


def test_leak_scan_skips_fragments_too_short_to_be_evidence(tmp_path: Path) -> None:
    gold = load_gold(write_gold(tmp_path, [gold_row("g-1", leaf="Tax", stratum="Tax")]))
    assert "Tax" not in surface_strings(gold)
