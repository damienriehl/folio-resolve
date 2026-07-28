"""Audit-gate packet machinery and the gold-decision fold (U5; R2, R3, R11; KTD5, KTD6, KTD9).

Synthetic only: no workbook, no FOLIO, no network. Every ontology fact the packet needs — a
definition snippet, a label proposal for an unresolved string — arrives through an injected
mapping or callable, so the whole gate is exercised offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fixtures.eval_synthetic_workbook import (
    FIRM1_V2_ROWS,
    W_ADVISORY,
    W_ARBITRATION,
    W_ENFORCEMENT,
    W_LITIGATION,
    W_PURCHASE,
    synthetic_index,
)
from folio_eval.answer_rule import RankedCandidate
from folio_eval.audit import (
    GOLD_VARIANTS,
    NEW_GOLD_CAP,
    SECTIONS_V2,
    SUSPECT_ROW_CAP,
    DecisionRecord,
    LabelProposal,
    SplitFacts,
    append_decisions,
    build_packet,
    build_packet_v2,
    fold_decisions,
    fold_granular_decisions,
    gold_row_v2_from_json,
    load_decisions,
    load_gold_rows,
    packet_v2_from_json,
    propose_for_label,
    rejection_key,
    rejection_memory,
    replay_counts,
    variant_iris,
    variant_stats,
    variant_table,
)
from folio_eval.gold import build_gold_v2, parse_firm1_v2
from folio_eval.packet_render import (
    render_sheet,
    render_sheet_v2,
    write_packet,
    write_packet_v2,
)
from folio_eval.resolve_labels import IndexedConcept, LabelIndex
from folio_eval.splits import sha256_text

ONTOLOGY_SHA = "b" * 64
OTHER_ONTOLOGY_SHA = "c" * 64

# --------------------------------------------------------------------------------------
# Synthetic gold
# --------------------------------------------------------------------------------------


def value(iri: str, raw: str, origin: str, *, column: str = "SALI 1") -> dict[str, Any]:
    return {
        "raw": raw,
        "iri": iri,
        "origin": origin,
        "column": column,
        "branch": "exact_preferred",
        "parse_branch": "plain",
        "ambiguous": False,
        "suspect": False,
    }


def gold_payload(
    item_id: str,
    *,
    firm: str = "firm1",
    stratum: str = "Corporate",
    leaf: str = "Fund Formation",
    level2: str = "Funds",
    values: list[dict[str, Any]] | None = None,
    rules: list[str] | None = None,
    flags: list[str] | None = None,
    notes: str | None = None,
    blank: bool | None = None,
) -> dict[str, Any]:
    values = [] if values is None else values
    iris = sorted({entry["iri"] for entry in values})
    ancestor_path = [stratum, level2]
    return {
        "item_id": item_id,
        "firm": firm,
        "stratum": stratum,
        "stratum_id": f"sid-{stratum}",
        "ancestor_path": ancestor_path,
        "leaf": leaf,
        "input_text": " > ".join([*ancestor_path, leaf]),
        "gold_iris": iris,
        "gold_labels_raw": [entry["raw"] for entry in values],
        "values": values,
        "flags": flags or [],
        "rules": rules or [],
        "blank": (not iris) if blank is None else blank,
        "notes": notes,
        "source_rows": [4],
        "provenance": "curator_workbook",
        "gold_version": 1,
    }


def write_gold_file(tmp_path: Path, payloads: list[dict[str, Any]]) -> Path:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in payloads)
    path = tmp_path / "gold_v1.jsonl"
    path.write_text(text, encoding="utf-8")
    (tmp_path / "gold_v1.manifest.json").write_text(
        json.dumps(
            {
                "gold_id": "v1-test",
                "gold_version": 1,
                "content_sha256": sha256_text(text),
                "ontology_cache_sha256": ONTOLOGY_SHA,
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def rows(tmp_path: Path) -> list[Any]:
    payloads = [
        # own + inherited mix, cascaded from a shared L2/L3 row
        gold_payload(
            "item-own-and-inherited",
            values=[
                value("R-own", "Fund Formation", "own"),
                value("R-l2", "Advisory Service", "level2"),
                value("R-l1", "Corporate Law", "level1"),
            ],
            rules=["cascade_level2", "cascade_level1", "cascade_from_shared_row"],
        ),
        # inherited only: own_only empties it
        gold_payload(
            "item-inherited-only",
            leaf="Carry Waterfall",
            values=[value("R-l2", "Advisory Service", "level2")],
            rules=["cascade_level2"],
        ),
        # own only: every variant keeps it whole
        gold_payload(
            "item-own-only",
            leaf="Side Letters",
            values=[value("R-own2", "Side Letter", "own")],
            rules=[],
        ),
        # blank: new-gold candidate pool
        gold_payload("item-blank", leaf="Continuation Fund", values=[], rules=["blank_row"]),
    ]
    write_gold_file(tmp_path, payloads)
    return load_gold_rows(tmp_path / "gold_v1.jsonl")


def ranked(*specs: tuple[str, str, float, float]) -> list[RankedCandidate]:
    return [
        RankedCandidate(
            iri=iri,
            label=label,
            score=score,
            probability=probability,
            rank=index,
            extraction_path="label_search",
        )
        for index, (iri, label, score, probability) in enumerate(specs, start=1)
    ]


DEFINITIONS = {
    "R-own": "A service in which counsel forms a pooled investment vehicle for a sponsor client "
    "and negotiates its constituent documents across many jurisdictions and regulatory regimes "
    "including a long tail of side arrangements.",
    "R-pipe": "The practice of organizing private investment funds and their governing documents.",
    "R-new": "A vehicle formed to hold assets rolled out of an expiring fund.",
    "R-l2": "Advice given outside a dispute.",
}


# --------------------------------------------------------------------------------------
# Gold-derivation variants (the cascade / denominator decision)
# --------------------------------------------------------------------------------------


def test_variant_names_are_pinned() -> None:
    assert GOLD_VARIANTS == ("v1_as_is", "own_only", "no_shared_row_cascade")


def test_own_only_drops_inherited_iris(rows: list[Any]) -> None:
    by_id = {row.item_id: row for row in rows}
    assert variant_iris(by_id["item-own-and-inherited"], "own_only") == ("R-own",)
    assert variant_iris(by_id["item-inherited-only"], "own_only") == ()
    assert variant_iris(by_id["item-own-only"], "own_only") == ("R-own2",)


def test_no_shared_row_cascade_drops_only_shared_row_level2(rows: list[Any]) -> None:
    by_id = {row.item_id: row for row in rows}
    # item-own-and-inherited carries the cascade_from_shared_row rule: its level2 IRI goes.
    assert variant_iris(by_id["item-own-and-inherited"], "no_shared_row_cascade") == (
        "R-l1",
        "R-own",
    )
    # item-inherited-only cascaded from an ordinary L2 row: untouched.
    assert variant_iris(by_id["item-inherited-only"], "no_shared_row_cascade") == ("R-l2",)


def test_variant_stats_count_items_iris_and_mean_set_size(rows: list[Any]) -> None:
    as_is = variant_stats(rows, "v1_as_is")
    own = variant_stats(rows, "own_only")
    assert (as_is.items_scored, as_is.gold_iris) == (3, 5)
    assert as_is.mean_set_size == pytest.approx(5 / 3)
    # own_only empties item-inherited-only, which therefore becomes blank.
    assert (own.items_scored, own.items_blank, own.gold_iris) == (2, 2, 2)
    assert own.mean_set_size == pytest.approx(1.0)


def test_variant_table_covers_every_variant(rows: list[Any]) -> None:
    table = variant_table(rows)
    assert [entry.variant for entry in table] == list(GOLD_VARIANTS)


def test_replay_counts_rescore_the_same_predictions_under_a_new_gold(rows: list[Any]) -> None:
    predictions = {
        "item-own-and-inherited": ("R-own", "R-wrong"),
        "item-inherited-only": ("R-l2",),
        "item-own-only": ("R-own2",),
    }
    as_is = replay_counts(predictions, {row.item_id: variant_iris(row, "v1_as_is") for row in rows})
    own = replay_counts(predictions, {row.item_id: variant_iris(row, "own_only") for row in rows})
    # v1: 5 gold, 3 tp (R-own, R-l2, R-own2), 1 fp, 2 fn.
    assert (as_is.tp, as_is.fp, as_is.fn, as_is.items) == (3, 1, 2, 3)
    # own_only: item-inherited-only leaves the denominator entirely; its prediction leaves too.
    assert (own.tp, own.fp, own.fn, own.items) == (2, 1, 0, 2)
    assert own.recall == pytest.approx(1.0)


# --------------------------------------------------------------------------------------
# Packet assembly — AE1
# --------------------------------------------------------------------------------------


def base_packet(rows: list[Any], **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "gold_rows": rows,
        "suspects": [
            {
                "item_id": "item-own-and-inherited",
                "firm": "firm1",
                "stratum": "Corporate",
                "ancestor_path": ["Corporate", "Funds"],
                "leaf": "Fund Formation",
                "gold_iris": ["R-l1", "R-l2", "R-own"],
                "gold_labels_raw": ["Fund Formation", "Advisory Service", "Corporate Law"],
                "reasons": ["sali_notes_flagged"],
                "notes": "DAMIEN: is this really Advisory Service? discuss",
                "source_rows": [4],
            }
        ],
        "resolution_batch": [],
        "predictions": {
            "item-own-and-inherited": ranked(("R-pipe", "Fund Formation Practice", 100.0, 0.37)),
            "item-blank": ranked(("R-new", "Continuation Fund Practice", 95.0, 0.18)),
        },
        "definitions": DEFINITIONS,
        "ontology_sha256": ONTOLOGY_SHA,
        "gold_version": 1,
        "gold_id": "v1-test",
    }
    kwargs.update(overrides)
    return build_packet(**kwargs)


def test_ae1_suspect_row_carries_both_candidates_and_evidence(rows: list[Any]) -> None:
    packet = base_packet(rows)
    suspects = [row for row in packet.rows if row.section == "suspect"]
    assert len(suspects) == 1
    row = suspects[0]
    assert row.decision_id
    assert row.item_id == "item-own-and-inherited"
    assert row.firm == "firm1" and row.stratum_id == "sid-Corporate"
    assert row.ancestor_path == ("Corporate", "Funds")
    assert row.surface_label == "Fund Formation"
    # gold side: labels + IRIs + per-IRI origin
    assert {entry["iri"] for entry in row.gold} == {"R-own", "R-l2", "R-l1"}
    assert {entry["origin"] for entry in row.gold} == {"own", "level2", "level1"}
    # pipeline side: labels + scores
    assert row.pipeline[0]["iri"] == "R-pipe"
    assert row.pipeline[0]["label"] == "Fund Formation Practice"
    assert row.pipeline[0]["score"] == 100.0
    # definition snippets for gold + top pipeline candidate, capped at ~25 words
    assert row.pipeline[0]["definition"].startswith("The practice of organizing")
    gold_own = next(entry for entry in row.gold if entry["iri"] == "R-own")
    assert gold_own["definition"].endswith("…")
    assert len(gold_own["definition"].split()) <= 26
    # curator notes, reason class, suggested action
    assert "discuss" in (row.notes_text or "")
    assert row.reason_class == "sali_notes_flagged"
    assert row.suggested_action
    assert row.proposed_iris == ("R-pipe",)


def test_packet_does_not_touch_gold(rows: list[Any]) -> None:
    before = [tuple(row.gold_iris) for row in rows]
    base_packet(rows)
    assert [tuple(row.gold_iris) for row in rows] == before


def test_frozen_slice_items_never_appear_as_score_driven_suspects(rows: list[Any]) -> None:
    """KTD4: score-driven suspects are barred from the frozen slice; builder flags are not."""
    cluster_rows = [
        {
            "item_id": "item-own-only",  # frozen
            "kind": "fn",
            "cluster": "synonymy",
            "gold_iri": "R-own2",
            "gold_labels": ["Side Letter"],
            "top_candidate_iri": "R-pipe",
            "top_candidate_label": "Fund Formation Practice",
            "top_candidate_score": 90.0,
            "signals": {"max_token_jaccard": 0.0},
            "slice": "frozen",
        },
        {
            "item_id": "item-own-and-inherited",  # tune, own-origin gold IRI
            "kind": "fn",
            "cluster": "synonymy",
            "gold_iri": "R-own",
            "gold_labels": ["Fund Formation"],
            "top_candidate_iri": "R-pipe",
            "top_candidate_label": "Fund Formation Practice",
            "top_candidate_score": 90.0,
            "signals": {"max_token_jaccard": 0.0},
            "slice": "tune",
        },
        {
            "item_id": "item-inherited-only",  # inherited-origin gold: not a gold-suspect signal
            "kind": "fn",
            "cluster": "synonymy",
            "gold_iri": "R-l2",
            "gold_labels": ["Advisory Service"],
            "top_candidate_iri": "R-pipe",
            "top_candidate_label": "Fund Formation Practice",
            "top_candidate_score": 90.0,
            "signals": {"max_token_jaccard": 0.0},
            "slice": "tune",
        },
    ]
    packet = base_packet(
        rows,
        cluster_rows=cluster_rows,
        frozen_ids={"item-own-only"},
        predictions={
            "item-own-and-inherited": ranked(("R-pipe", "Fund Formation Practice", 100.0, 0.37)),
            "item-inherited-only": ranked(("R-pipe", "Fund Formation Practice", 90.0, 0.17)),
        },
    )
    score_driven = [row for row in packet.rows if row.reason_class == "own_origin_synonymy"]
    assert {row.item_id for row in score_driven} == {"item-own-and-inherited"}
    assert packet.counts["frozen_suspects_barred"] == 1
    assert packet.counts["score_driven_suspects"] == 1


def test_suspect_rows_cap_at_fifty_and_report_the_remainder(rows: list[Any]) -> None:
    many = [
        {
            "item_id": "item-own-and-inherited",
            "firm": "firm1",
            "stratum": "Corporate",
            "ancestor_path": ["Corporate", "Funds"],
            "leaf": "Fund Formation",
            "gold_iris": ["R-own"],
            "gold_labels_raw": ["Fund Formation"],
            "reasons": ["sali_notes_flagged"],
            "notes": f"note {index}",
            "source_rows": [index],
        }
        for index in range(70)
    ]
    packet = base_packet(rows, suspects=many)
    detailed = [row for row in packet.rows if row.section == "suspect"]
    assert len(detailed) == SUSPECT_ROW_CAP
    assert packet.counts["suspects_total"] == 70
    assert packet.overflow["sali_notes_flagged"] == 70 - SUSPECT_ROW_CAP


# --------------------------------------------------------------------------------------
# New-gold candidates (KTD5 / AE2)
# --------------------------------------------------------------------------------------


def test_new_gold_candidates_rank_by_calibrated_score_and_cap_at_25(rows: list[Any]) -> None:
    blanks = [
        gold_payload(f"blank-{index:03d}", leaf=f"Blank {index}", values=[], rules=["blank_row"])
        for index in range(40)
    ]
    payloads = [row.payload for row in rows] + blanks
    tmp = Path(rows[0].payload["__dir__"]) if "__dir__" in rows[0].payload else None
    assert tmp is None  # payload carries no scratch keys
    predictions = {
        f"blank-{index:03d}": ranked((f"R-cand-{index}", f"Candidate {index}", 90.0, index / 100))
        for index in range(40)
    }
    packet = build_packet(
        gold_rows=load_rows_from_payloads(payloads),
        suspects=[],
        resolution_batch=[],
        predictions=predictions,
        definitions={},
        ontology_sha256=ONTOLOGY_SHA,
        gold_version=1,
        gold_id="v1-test",
        eligible_strata={"sid-Corporate"},
    )
    candidates = [row for row in packet.rows if row.section == "new_gold"]
    assert len(candidates) == NEW_GOLD_CAP
    # highest calibrated probability first
    assert candidates[0].item_id == "blank-039"
    assert candidates[0].proposed_iris == ("R-cand-39",)


def test_new_gold_ties_break_on_an_exact_label_match(rows: list[Any]) -> None:
    """The fitted calibration saturates, so every top-score proposal shares one probability."""
    blanks = [
        gold_payload("blank-acronym", leaf="QQR", values=[], rules=["blank_row"]),
        gold_payload("blank-exact", leaf="Escheat", values=[], rules=["blank_row"]),
    ]
    packet = build_packet(
        gold_rows=load_rows_from_payloads([row.payload for row in rows] + blanks),
        predictions={
            # identical calibrated probability and raw score; only the label match separates them
            "blank-acronym": ranked(("R-court", "Quorum Regional Tribunal - D. Vellaton", 100.0, 0.37)),
            "blank-exact": ranked(("R-esch", "Escheat", 100.0, 0.37)),
        },
        ontology_sha256=ONTOLOGY_SHA,
        gold_version=1,
        gold_id="v1-test",
        eligible_strata={"sid-Corporate"},
    )
    candidates = [row for row in packet.rows if row.section == "new_gold"]
    assert [row.item_id for row in candidates] == ["blank-exact", "blank-acronym"]
    assert candidates[0].extra["exact_label_match"] is True


def test_new_gold_candidates_skip_strata_without_gold(rows: list[Any]) -> None:
    blank = gold_payload(
        "blank-no-gold", stratum="NoGoldTermSet", leaf="Vellaton", values=[], rules=["blank_row"]
    )
    payloads = [row.payload for row in rows] + [blank]
    packet = build_packet(
        gold_rows=load_rows_from_payloads(payloads),
        suspects=[],
        resolution_batch=[],
        predictions={"blank-no-gold": ranked(("R-wales", "Wales", 100.0, 0.9))},
        definitions={},
        ontology_sha256=ONTOLOGY_SHA,
        gold_version=1,
        gold_id="v1-test",
    )
    assert not [row for row in packet.rows if row.item_id == "blank-no-gold"]


# --------------------------------------------------------------------------------------
# Resolution-batch enrichment (R2)
# --------------------------------------------------------------------------------------


def label_index() -> LabelIndex:
    return LabelIndex.from_concepts(
        [
            IndexedConcept(
                iri="R-realm",
                preferred_labels=("Kingdom of Northmarch and the Outer Isles",),
                alternative_labels=("Northmarch",),
            ),
            IndexedConcept(
                iri="R-escrow", preferred_labels=("Escrow Services (non-dispute)",)
            ),
            IndexedConcept(iri="R-freight", preferred_labels=("Freight Escrow Practice",)),
        ]
    )


def test_containment_finds_the_longer_folio_label() -> None:
    index = label_index()
    proposals = propose_for_label("Kingdom of Northmarch", index=index, search=lambda _q, limit=20: [])
    assert proposals[0].iri == "R-realm"
    assert proposals[0].method == "containment"


def test_containment_handles_the_parenthetical_suffix_case() -> None:
    index = label_index()
    proposals = propose_for_label("Escrow Service", index=index, search=lambda _q, limit=20: [])
    assert [entry.iri for entry in proposals] == ["R-escrow"]


def test_direct_search_supplies_candidates_containment_misses() -> None:
    index = label_index()
    proposals = propose_for_label(
        "Freight Escrow Cover",
        index=index,
        search=lambda _q, limit=20: [LabelProposal("R-freight", "Freight Escrow Practice", 41.0, "search")],
    )
    assert [entry.method for entry in proposals] == ["search"]


def test_fuzzy_fallback_junk_is_filtered_out() -> None:
    """folio-python returns unrelated concepts at score 90 for strings FOLIO has no label for."""
    index = label_index()
    proposals = propose_for_label(
        "Need clarity",
        index=index,
        search=lambda _q, limit=20: [
            LabelProposal("R-zeno", "Zenobia", 90.0, "search"),
            LabelProposal("R-rav", "Ravenspur Tribunal", 90.0, "search"),
        ],
    )
    assert proposals == ()


def test_search_proposals_rank_by_token_overlap_not_provider_score() -> None:
    index = label_index()
    proposals = propose_for_label(
        "Freight Escrow Practice",
        index=index,
        search=lambda _q, limit=20: [
            LabelProposal("R-loose", "Freight Law", 90.0, "search"),
            LabelProposal("R-tight", "Freight Escrow Practice", 85.5, "search"),
        ],
    )
    assert [entry.iri for entry in proposals] == ["R-tight", "R-loose"]


def test_rows_with_no_plausible_candidate_are_coverage_gaps(rows: list[Any]) -> None:
    batch = [
        {
            "item_id": "item-own-and-inherited",
            "firm": "firm1",
            "stratum": "Corporate",
            "ancestor_path": ["Corporate", "Funds"],
            "leaf": "Fund Formation",
            "column": "SALI 3",
            "normalized": "Escrow Service",
            "raw": "Escrow Service",
            "origin": "own",
            "parse_branch": "plain",
            "reason": "no_label_match",
        },
        {
            "item_id": "item-own-only",
            "firm": "firm1",
            "stratum": "Corporate",
            "ancestor_path": ["Corporate", "Funds"],
            "leaf": "Side Letters",
            "column": "SALI 2",
            "normalized": "Unclear — ask",
            "raw": "Unclear — ask",
            "origin": "own",
            "parse_branch": "plain",
            "reason": "no_label_match",
        },
    ]
    packet = base_packet(
        rows,
        resolution_batch=batch,
        label_proposals={
            "Escrow Service": [LabelProposal("R-escrow", "Escrow Services", 90.0, "containment")],
            "Unclear — ask": [],
        },
    )
    resolution = {row.surface_label: row for row in packet.rows if row.section == "resolution"}
    assert resolution["Escrow Service"].reason_class == "no_label_match"
    assert resolution["Escrow Service"].proposed_iris == ("R-escrow",)
    assert resolution["Unclear — ask"].reason_class == "folio_coverage_gap"
    assert packet.counts["resolution_coverage_gaps"] == 1
    assert packet.counts["resolution_with_candidates"] == 1


# --------------------------------------------------------------------------------------
# Rejection memory (KTD9)
# --------------------------------------------------------------------------------------


def rejected_record(item_id: str, iris: tuple[str, ...], *, ontology: str = ONTOLOGY_SHA) -> DecisionRecord:
    return DecisionRecord(
        decision_id=f"suspect:{item_id}:x",
        item_id=item_id,
        section="suspect",
        action="reject",
        reason_class="sali_notes_flagged",
        gold_version=1,
        ontology_sha256=ontology,
        proposed_iris=iris,
        resulting_iris=(),
        recorded_at="2026-07-27T00:00:00Z",
    )


def test_rejection_memory_suppresses_an_identical_reproposal(rows: list[Any]) -> None:
    memory = rejection_memory(
        [rejected_record("item-own-and-inherited", ("R-pipe",))], ontology_sha256=ONTOLOGY_SHA
    )
    assert rejection_key("item-own-and-inherited", ("R-pipe",)) in memory
    packet = base_packet(rows, rejected=memory)
    assert not [row for row in packet.rows if row.section == "suspect"]
    assert packet.counts["suppressed_by_rejection_memory"] == 1


def test_rejection_memory_releases_when_the_ontology_moves(rows: list[Any]) -> None:
    memory = rejection_memory(
        [rejected_record("item-own-and-inherited", ("R-pipe",), ontology=OTHER_ONTOLOGY_SHA)],
        ontology_sha256=ONTOLOGY_SHA,
    )
    assert memory == frozenset()
    packet = base_packet(rows, rejected=memory)
    assert len([row for row in packet.rows if row.section == "suspect"]) == 1


def test_a_different_proposal_is_not_suppressed(rows: list[Any]) -> None:
    memory = rejection_memory(
        [rejected_record("item-own-and-inherited", ("R-somethingelse",))],
        ontology_sha256=ONTOLOGY_SHA,
    )
    packet = base_packet(rows, rejected=memory)
    assert len([row for row in packet.rows if row.section == "suspect"]) == 1
    assert packet.counts["suppressed_by_rejection_memory"] == 0


def test_decision_log_round_trips_and_carries_no_surface_strings(tmp_path: Path) -> None:
    path = tmp_path / "gold_decisions.jsonl"
    record = rejected_record("item-own-and-inherited", ("R-pipe",))
    append_decisions(path, [record], surfaces=("Fund Formation", "Corporate"))
    text = path.read_text(encoding="utf-8")
    assert "Fund Formation" not in text
    assert load_decisions(path) == (record,)


def test_decision_log_refuses_to_write_a_surface_string(tmp_path: Path) -> None:
    from folio_eval.clusters import SurfaceLeakError

    path = tmp_path / "gold_decisions.jsonl"
    leaky = DecisionRecord(
        decision_id="suspect:item-1:x",
        item_id="item-1",
        section="suspect",
        action="reject",
        reason_class="Fund Formation was wrong",
        gold_version=1,
        ontology_sha256=ONTOLOGY_SHA,
        proposed_iris=("R-pipe",),
        resulting_iris=(),
        recorded_at="2026-07-27T00:00:00Z",
    )
    with pytest.raises(SurfaceLeakError):
        append_decisions(path, [leaky], surfaces=("Fund Formation",))
    assert not path.exists()


# --------------------------------------------------------------------------------------
# The fold: gold v2 (R3 / AE1 / KTD5)
# --------------------------------------------------------------------------------------


def load_rows_from_payloads(payloads: list[dict[str, Any]]) -> list[Any]:
    from folio_eval.audit import gold_row_from_json

    return [gold_row_from_json(payload) for payload in payloads]


def test_no_decisions_means_gold_is_carried_forward_unchanged(rows: list[Any]) -> None:
    packet = base_packet(rows)
    result = fold_decisions(
        rows, {}, packet=packet, ontology_sha256=ONTOLOGY_SHA, now="2026-07-28T00:00:00Z"
    )
    assert result.manifest["gold_version"] == 2
    assert result.counts["carried_forward"] == len(rows)
    assert result.counts["accepted"] == 0
    before = {row.item_id: sorted(row.gold_iris) for row in rows}
    after = {payload["item_id"]: sorted(payload["gold_iris"]) for payload in result.rows}
    assert before == after
    assert {payload["provenance"] for payload in result.rows} == {"curator_workbook"}


def test_accepting_a_suspect_rewrites_gold_and_bumps_the_version(rows: list[Any]) -> None:
    packet = base_packet(rows)
    suspect = next(row for row in packet.rows if row.section == "suspect")
    result = fold_decisions(
        rows,
        {suspect.decision_id: {"action": "accept", "note": "yes — the practice concept"}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    changed = next(
        payload for payload in result.rows if payload["item_id"] == "item-own-and-inherited"
    )
    assert changed["gold_iris"] == ["R-pipe"]
    assert changed["provenance"] == "damien_corrected"
    assert changed["gold_version"] == 2
    assert result.counts["accepted"] == 1
    assert result.manifest["provenance_counts"]["damien_corrected"] == 1
    assert result.manifest["gold_id"].startswith("v2-")


def test_accepted_new_gold_is_tagged_pipeline_suggested_with_a_sensitivity_flag(
    rows: list[Any],
) -> None:
    packet = base_packet(rows, eligible_strata={"sid-Corporate"})
    candidate = next(row for row in packet.rows if row.section == "new_gold")
    assert candidate.item_id == "item-blank"
    result = fold_decisions(
        rows,
        {candidate.decision_id: {"action": "accept"}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    filled = next(payload for payload in result.rows if payload["item_id"] == "item-blank")
    assert filled["gold_iris"] == ["R-new"]
    assert filled["blank"] is False
    assert filled["provenance"] == "pipeline_suggested"
    assert "pipeline_suggested" in filled["flags"]
    assert result.manifest["provenance_counts"]["pipeline_suggested"] == 1
    assert result.manifest["sensitivity_excluded_items"] == 1


def test_edit_writes_the_edited_iris_and_tags_damien_corrected(rows: list[Any]) -> None:
    packet = base_packet(rows)
    suspect = next(row for row in packet.rows if row.section == "suspect")
    result = fold_decisions(
        rows,
        {
            suspect.decision_id: {
                "action": "edit",
                "edited_iris": ["R-own", "R-pipe"],
                "note": "keep the own-origin mapping too",
            }
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    changed = next(
        payload for payload in result.rows if payload["item_id"] == "item-own-and-inherited"
    )
    assert changed["gold_iris"] == ["R-own", "R-pipe"]
    assert changed["provenance"] == "damien_corrected"
    assert result.counts["edited"] == 1
    record = next(entry for entry in result.records if entry.action == "edit")
    assert record.resulting_iris == ("R-own", "R-pipe")


def test_accepting_a_resolution_adds_the_iri_to_every_item_carrying_that_label(
    rows: list[Any],
) -> None:
    """A resolved cell is one of an item's several gold values — it unions in, never replaces."""
    batch = [
        {
            "item_id": item_id,
            "firm": "firm1",
            "stratum": "Corporate",
            "ancestor_path": ["Corporate", "Funds"],
            "leaf": "Side Letters",
            "column": "SALI 2",
            "normalized": "Escrow Service",
            "raw": "Escrow Service",
            "origin": "own",
            "parse_branch": "plain",
            "reason": "no_label_match",
        }
        for item_id in ("item-own-and-inherited", "item-own-only")
    ]
    packet = base_packet(
        rows,
        resolution_batch=batch,
        label_proposals={
            "Escrow Service": [LabelProposal("R-escrow", "Escrow Services", 90.0, "containment")]
        },
    )
    resolution = next(row for row in packet.rows if row.section == "resolution")
    assert resolution.extra["occurrences"] == 2
    result = fold_decisions(
        rows,
        {resolution.decision_id: {"action": "accept"}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    by_id = {payload["item_id"]: payload for payload in result.rows}
    assert by_id["item-own-and-inherited"]["gold_iris"] == ["R-escrow", "R-l1", "R-l2", "R-own"]
    assert by_id["item-own-only"]["gold_iris"] == ["R-escrow", "R-own2"]
    assert by_id["item-own-only"]["provenance"] == "damien_corrected"
    # untouched items still carry forward
    assert by_id["item-inherited-only"]["provenance"] == "curator_workbook"


def test_reject_leaves_gold_alone_and_records_the_rejection(rows: list[Any]) -> None:
    packet = base_packet(rows)
    suspect = next(row for row in packet.rows if row.section == "suspect")
    result = fold_decisions(
        rows,
        {suspect.decision_id: {"action": "reject", "note": "gold is right"}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    unchanged = next(
        payload for payload in result.rows if payload["item_id"] == "item-own-and-inherited"
    )
    assert sorted(unchanged["gold_iris"]) == ["R-l1", "R-l2", "R-own"]
    assert unchanged["provenance"] == "curator_workbook"
    assert result.counts["rejected"] == 1
    record = next(entry for entry in result.records if entry.action == "reject")
    assert record.proposed_iris == ("R-pipe",)
    # and the rejection is remembered for the next triage
    memory = rejection_memory(result.records, ontology_sha256=ONTOLOGY_SHA)
    assert rejection_key("item-own-and-inherited", ("R-pipe",)) in memory


def test_fold_rejects_an_unknown_action(rows: list[Any]) -> None:
    packet = base_packet(rows)
    suspect = next(row for row in packet.rows if row.section == "suspect")
    with pytest.raises(ValueError, match="unknown action"):
        fold_decisions(
            rows,
            {suspect.decision_id: {"action": "maybe"}},
            packet=packet,
            ontology_sha256=ONTOLOGY_SHA,
        )


def test_fold_rejects_an_unknown_decision_id(rows: list[Any]) -> None:
    packet = base_packet(rows)
    with pytest.raises(KeyError, match="no packet row"):
        fold_decisions(
            rows,
            {"suspect:nope:0000": {"action": "accept"}},
            packet=packet,
            ontology_sha256=ONTOLOGY_SHA,
        )


def test_manifest_content_hash_matches_the_written_rows(rows: list[Any], tmp_path: Path) -> None:
    from folio_eval.audit import write_gold_version

    packet = base_packet(rows)
    result = fold_decisions(
        rows, {}, packet=packet, ontology_sha256=ONTOLOGY_SHA, now="2026-07-28T00:00:00Z"
    )
    written = write_gold_version(result, tmp_path)
    text = written["gold"].read_text(encoding="utf-8")
    manifest = json.loads(written["manifest"].read_text(encoding="utf-8"))
    assert manifest["content_sha256"] == sha256_text(text)
    assert manifest["parent_gold_id"] == "v1-test"


# --------------------------------------------------------------------------------------
# Split facts and the rendered sheet
# --------------------------------------------------------------------------------------


def test_sheet_renders_every_section_and_is_self_contained(rows: list[Any], tmp_path: Path) -> None:
    packet = base_packet(
        rows,
        eligible_strata={"sid-Corporate"},
        split=SplitFacts(
            seed=20260727,
            tune=1217,
            frozen=166,
            firm2=111,
            excluded_surface_duplicates=111,
            realized_frozen_fraction=0.111,
            small_strata=5,
            manifest_sha256="8e37f97c",
        ),
        resolution_batch=[
            {
                "item_id": "item-own-only",
                "firm": "firm1",
                "stratum": "Corporate",
                "ancestor_path": ["Corporate", "Funds"],
                "leaf": "Side Letters",
                "column": "SALI 2",
                "normalized": "Regulatory Service",
                "raw": "Regulatory Service",
                "origin": "own",
                "parse_branch": "plain",
                "reason": "no_label_match",
            }
        ],
        label_proposals={
            "Escrow Service": [LabelProposal("R-escrow", "Escrow Services", 90.0, "containment")]
        },
    )
    html = render_sheet(packet)
    for marker in ("cascade", "split", "suspect", "resolution", "new_gold"):
        assert f'data-section="{marker}"' in html
    assert "<textarea" in html
    assert 'type="radio"' in html
    assert "prefers-color-scheme" in html
    # self-contained: no external asset may be referenced
    assert "http://" not in html and "https://folio" not in html
    assert "<script src=" not in html and "<link rel=\"stylesheet\"" not in html

    paths = write_packet(packet, tmp_path)
    assert paths["packet"].exists() and paths["sheet"].exists()
    payload = json.loads(paths["packet"].read_text(encoding="utf-8"))
    assert payload["counts"]["suspects_total"] == 1
    assert {entry["section"] for entry in payload["rows"]} >= {"cascade", "split", "suspect"}


def test_decision_ids_are_stable_across_regeneration(rows: list[Any]) -> None:
    first = base_packet(rows)
    second = base_packet(rows)
    assert [row.decision_id for row in first.rows] == [row.decision_id for row in second.rows]


# --------------------------------------------------------------------------------------
# Gold v2 — the per-cell packet and the granular fold
# --------------------------------------------------------------------------------------


@pytest.fixture
def v2_gold() -> tuple[Any, list[Any]]:
    """The synthetic per-cell build, plus its rows as the packet reads them back."""
    build = build_gold_v2([parse_firm1_v2(FIRM1_V2_ROWS, firm="firm1")], synthetic_index())
    return build, [gold_row_v2_from_json(item.to_json()) for item in build.items]


def _row_by_text(rows: list[Any], text: str) -> Any:
    return next(row for row in rows if row.input_text == text)


def v2_packet(
    build: Any,
    rows: list[Any],
    *,
    predictions: dict[str, Any] | None = None,
    prefill_rulings: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    return build_packet_v2(
        gold_rows=rows,
        pairing_rows=build.pairing_rows,
        inconsistent_groups=build.inconsistent_groups,
        suspects=build.suspects,
        predictions=predictions or {},
        definitions=DEFINITIONS,
        value_iris={value.raw: value.iri for row in rows for value in row.values},
        prefill_rulings=prefill_rulings,
        ontology_sha256=ONTOLOGY_SHA,
        gold_id="v2-test",
        gold_version=2,
        parent_gold_id="v1-test",
        generated_at="2026-07-28T00:00:00Z",
        **kwargs,
    )


def test_v2_packet_carries_the_pairing_and_consistency_sections(v2_gold: tuple[Any, list[Any]]) -> None:
    """Sections A and B exist because the per-cell derivation cannot decide them alone."""
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    assert packet.meta["sections"] == list(SECTIONS_V2)

    pairing = packet.section("pairing")
    assert len(pairing) == 1
    assignments = pairing[0].extra["assignments"]
    heuristic = {entry["input"]: entry["iris"] for entry in assignments["heuristic"]}
    alternative = {entry["input"]: entry["iris"] for entry in assignments["alternative"]}
    assert heuristic["Uneven Category"] == [W_LITIGATION]
    assert sorted(heuristic["Odd attribute"]) == sorted([W_ARBITRATION, W_ADVISORY])
    assert alternative["Uneven Category"] == []
    assert sorted(alternative["Odd attribute"]) == sorted(
        [W_LITIGATION, W_ARBITRATION, W_ADVISORY]
    )

    consistency = packet.section("consistency")
    assert len(consistency) == 1
    assert consistency[0].surface_label == "Enforcement matters"
    assert {str(entry["iri"]) for entry in consistency[0].gold} == {W_ENFORCEMENT, W_PURCHASE}
    assert len(consistency[0].extra["instances"]) == 2


def test_v2_packet_grades_every_concept_individually(v2_gold: tuple[Any, list[Any]]) -> None:
    """Damien's format: one radio pair per gold concept and per pipeline candidate."""
    build, rows = v2_gold
    target = _row_by_text(rows, "Enforcement matters")
    packet = v2_packet(
        build,
        rows,
        predictions={
            target.item_id: ranked(
                ("R-pipe", "Enforcement Practice", 100.0, 0.9),
                ("R-junk", "Office of Water", 90.0, 0.1),
            )
        },
    )
    row = packet.section("consistency")[0]
    assert len(row.gold) == 2
    assert [entry["iri"] for entry in row.pipeline] == ["R-pipe", "R-junk"]
    # every pipeline candidate carries its own definition snippet, not just the leader
    assert row.pipeline[0]["definition"]
    html = render_sheet_v2(packet)
    assert 'value="keep"' in html and 'value="remove"' in html
    assert 'value="elevate"' in html and 'value="not_gold"' in html
    assert 'class="note gold-note"' in html and 'class="note pipeline-note"' in html


def test_v2_sheet_is_self_contained_and_renders_the_hierarchy(v2_gold: tuple[Any, list[Any]]) -> None:
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    html = render_sheet_v2(packet)
    for marker in SECTIONS_V2:
        assert f'data-section="{marker}"' in html
    assert "lvl-2" in html and "lvl-3" in html
    assert "prefers-color-scheme" in html
    # Nothing is fetched: no external script, stylesheet, font, image, or CSS import. (Gold IRIs
    # appear as inert ``data-iri`` attributes, which the browser never resolves.)
    for forbidden in ("<script src=", "<link ", "@import", "url(http", "src=", "href="):
        assert forbidden not in html
    assert "Copy decisions" in html and '<textarea id="out" readonly' in html


def test_v2_prefilled_ruling_is_carried_forward(v2_gold: tuple[Any, list[Any]]) -> None:
    """A ruling Damien already made shows up pre-checked instead of being asked again."""
    build, rows = v2_gold
    target = _row_by_text(rows, "Unsettled matters")
    packet = v2_packet(
        build,
        rows,
        predictions={target.item_id: ranked(("R-junk", "Office of Water", 90.0, 0.1))},
        prefill_rulings={"unsettled matters": "already ruled: gold stands"},
    )
    assert packet.section("consistency")[0].extra["prefill"] == {}
    suspect = next(
        entry for entry in packet.section("suspect") if entry.item_id == target.item_id
    )
    assert suspect.extra["prefill"]["gold"] == {W_LITIGATION: "keep"}
    assert suspect.extra["prefill"]["pipeline"] == {"R-junk": "not_gold"}
    assert packet.counts["prefilled_rulings"] == 1
    html = render_sheet_v2(packet)
    assert 'value="keep" checked' in html and 'value="not_gold" checked' in html


def test_granular_fold_keeps_removes_and_elevates(v2_gold: tuple[Any, list[Any]]) -> None:
    """The three per-concept verdicts, each landing in gold v3 with its provenance."""
    build, rows = v2_gold
    suspect_row = _row_by_text(rows, "Unsettled matters")
    blank_row = _row_by_text(rows, "General advice")
    packet = v2_packet(
        build,
        rows,
        eligible_strata={suspect_row.stratum_id, blank_row.stratum_id},
        predictions={
            suspect_row.item_id: ranked(
                ("R-pipe", "Enforcement Practice", 100.0, 0.9),
                ("R-junk", "Office of Water", 90.0, 0.1),
            ),
            blank_row.item_id: ranked(("R-new", "General Advisory", 100.0, 0.9)),
        },
    )
    suspect = next(
        entry for entry in packet.section("suspect") if entry.item_id == suspect_row.item_id
    )
    new_gold = next(
        entry for entry in packet.section("new_gold") if entry.item_id == blank_row.item_id
    )
    result = fold_granular_decisions(
        rows,
        {
            suspect.decision_id: {
                "gold": {W_LITIGATION: "remove"},
                "pipeline": {"R-pipe": "elevate", "R-junk": "not_gold"},
                "gold_note": "the litigation mapping belongs to a sibling cell",
            },
            new_gold.decision_id: {"pipeline": {"R-new": "elevate"}},
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    by_id = {row["item_id"]: row for row in result.rows}
    assert by_id[suspect_row.item_id]["gold_iris"] == ["R-pipe"]
    assert by_id[suspect_row.item_id]["provenance"] == "damien_corrected"
    assert by_id[blank_row.item_id]["gold_iris"] == ["R-new"]
    assert by_id[blank_row.item_id]["blank"] is False
    assert by_id[blank_row.item_id]["provenance"] == "pipeline_suggested"
    assert "pipeline_suggested" in by_id[blank_row.item_id]["flags"]
    assert result.manifest["gold_version"] == 3
    assert result.manifest["parent_gold_id"] == "v2-test"
    assert result.counts["gold_removed"] == 1
    assert result.counts["pipeline_elevated"] == 2
    assert result.counts["changed_items"] == 2
    assert result.manifest["sensitivity_excluded_items"] == 1


def test_granular_fold_leaves_unmentioned_gold_alone(v2_gold: tuple[Any, list[Any]]) -> None:
    """Silence never deletes curated gold — an omitted IRI is kept."""
    build, rows = v2_gold
    target = _row_by_text(rows, "Enforcement matters")
    packet = v2_packet(build, rows)
    consistency = packet.section("consistency")[0]
    assert consistency.item_id == target.item_id
    result = fold_granular_decisions(
        rows,
        {consistency.decision_id: {"gold": {W_ENFORCEMENT: "keep"}}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    by_id = {row["item_id"]: row for row in result.rows}
    assert by_id[target.item_id]["gold_iris"] == sorted([W_ENFORCEMENT, W_PURCHASE])
    assert result.counts["changed_items"] == 0


def test_granular_fold_applies_the_pairing_alternative(v2_gold: tuple[Any, list[Any]]) -> None:
    """Picking the alternative reading moves the shared row's outputs to the deepest input."""
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    pairing = packet.section("pairing")[0]
    result = fold_granular_decisions(
        rows,
        {pairing.decision_id: {"pairing": "alternative"}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    by_id = {row["item_id"]: row for row in result.rows}
    heading = _row_by_text(rows, "Uneven Category")
    leaf = _row_by_text(rows, "Odd attribute")
    assert by_id[heading.item_id]["gold_iris"] == []
    assert by_id[heading.item_id]["blank"] is True
    assert by_id[leaf.item_id]["gold_iris"] == sorted([W_LITIGATION, W_ARBITRATION, W_ADVISORY])
    assert result.counts["pairing_alternative"] == 1


def test_granular_fold_records_per_candidate_rejections(v2_gold: tuple[Any, list[Any]]) -> None:
    """A 'not gold' verdict becomes rejection memory, so the same proposal never resurfaces."""
    build, rows = v2_gold
    target = _row_by_text(rows, "Unsettled matters")
    packet = v2_packet(
        build,
        rows,
        predictions={target.item_id: ranked(("R-junk", "Office of Water", 90.0, 0.1))},
    )
    suspect = next(
        entry for entry in packet.section("suspect") if entry.item_id == target.item_id
    )
    result = fold_granular_decisions(
        rows,
        {suspect.decision_id: {"pipeline": {"R-junk": "not_gold"}}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    memory = rejection_memory(result.records, ontology_sha256=ONTOLOGY_SHA)
    assert rejection_key(target.item_id, ["R-junk"]) in memory
    reraised = v2_packet(
        build,
        rows,
        predictions={target.item_id: ranked(("R-junk", "Office of Water", 90.0, 0.1))},
        rejected=memory,
    )
    assert all(entry.item_id != target.item_id for entry in reraised.section("suspect"))
    assert reraised.counts["suppressed_by_rejection_memory"] == 1


def test_granular_fold_rejects_unknown_verdicts(v2_gold: tuple[Any, list[Any]]) -> None:
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    suspect = packet.section("suspect")[0]
    with pytest.raises(ValueError, match="unknown gold verdict"):
        fold_granular_decisions(
            rows,
            {suspect.decision_id: {"gold": {W_LITIGATION: "delete"}}},
            packet=packet,
            ontology_sha256=ONTOLOGY_SHA,
        )
    with pytest.raises(KeyError):
        fold_granular_decisions(
            rows, {"nope": {}}, packet=packet, ontology_sha256=ONTOLOGY_SHA
        )


def test_v2_packet_round_trips_through_json(v2_gold: tuple[Any, list[Any]], tmp_path: Path) -> None:
    """The fold grades the packet it was rendered from, so gold/pipeline must survive the file."""
    build, rows = v2_gold
    target = _row_by_text(rows, "Enforcement matters")
    packet = v2_packet(
        build,
        rows,
        predictions={target.item_id: ranked(("R-pipe", "Enforcement Practice", 100.0, 0.9))},
    )
    paths = write_packet_v2(packet, tmp_path)
    reloaded = packet_v2_from_json(json.loads(paths["packet"].read_text(encoding="utf-8")))
    original = {row.decision_id: row for row in packet.rows}
    for row in reloaded.rows:
        assert [entry["iri"] for entry in row.gold] == [
            entry["iri"] for entry in original[row.decision_id].gold
        ]
        assert [entry["iri"] for entry in row.pipeline] == [
            entry["iri"] for entry in original[row.decision_id].pipeline
        ]
    assert reloaded.section("pairing")[0].extra["assignments"]


def test_a_pipeline_candidate_that_is_already_gold_says_so(v2_gold: tuple[Any, list[Any]]) -> None:
    """Grading it 'not gold' in the pipeline block must not read as removing curated gold."""
    build, rows = v2_gold
    target = _row_by_text(rows, "Unsettled matters")
    packet = v2_packet(
        build,
        rows,
        predictions={
            target.item_id: ranked(
                (W_LITIGATION, "Sprocket Litigation Practice", 100.0, 0.9),
                ("R-junk", "Office of Water", 90.0, 0.1),
            )
        },
        prefill_rulings={"unsettled matters": "already ruled"},
    )
    suspect = next(
        entry for entry in packet.section("suspect") if entry.item_id == target.item_id
    )
    assert suspect.pipeline[0]["already_gold"] is True
    assert suspect.pipeline[1]["already_gold"] is False
    # the carried-forward ruling rejects the junk tail only, never the concept gold already names
    assert suspect.extra["prefill"]["pipeline"] == {"R-junk": "not_gold"}
    assert "already gold" in render_sheet_v2(packet)


def test_confirming_the_pairing_heuristic_changes_nothing(v2_gold: tuple[Any, list[Any]]) -> None:
    """The sheet ships with the applied reading pre-checked, so an untouched sheet is a no-op."""
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    pairing = packet.section("pairing")[0]
    result = fold_granular_decisions(
        rows,
        {pairing.decision_id: {"pairing": "heuristic"}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    assert result.counts["changed_items"] == 0
    assert result.counts["pairing_alternative"] == 0
    before = {row.item_id: sorted(row.gold_iris) for row in rows}
    assert {row["item_id"]: sorted(row["gold_iris"]) for row in result.rows} == before
