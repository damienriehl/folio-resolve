"""Audit-gate packet machinery and the gold-decision fold (U5; R2, R3, R11; KTD5, KTD6, KTD9).

Synthetic only: no workbook, no FOLIO, no network. Every ontology fact the packet needs — a
definition snippet, a label proposal for an unresolved string — arrives through an injected
mapping or callable, so the whole gate is exercised offline.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fixtures.eval_synthetic_workbook import (
    FIRM1_PIPE_ROWS,
    FIRM1_V2_ROWS,
    FIRM2_PIPE_ROWS,
    W_ADVISORY,
    W_AGREEMENTS,
    W_ARBITRATION,
    W_ENFORCEMENT,
    W_INDUSTRY,
    W_LITIGATION,
    W_MANUFACTURING,
    W_PURCHASE,
    synthetic_index,
)
from folio_eval.answer_rule import AnswerRuleConfig, RankedCandidate
from folio_eval.audit import (
    DEFAULT_PACKET_DIR_V2,
    GOLD_VARIANTS,
    NEW_GOLD_CAP,
    PAIRING_VIOLATION_DUPLICATE,
    PAIRING_VIOLATION_EMPTY,
    SECTIONS_V2,
    SUSPECT_ROW_CAP,
    DecisionRecord,
    LabelProposal,
    Packet,
    PacketRow,
    SplitFacts,
    _atomized_level_mappings,
    append_decisions,
    build_packet,
    build_packet_v2,
    fold_decisions,
    fold_granular_decisions,
    gold_row_v2_from_json,
    latest_folded_path,
    load_decisions,
    load_gold_rows,
    load_gold_rows_v2,
    locate_source_rows,
    packet_v2_from_json,
    pairing_violations,
    precheck_pairing,
    propose_for_label,
    rejection_key,
    rejection_memory,
    replay_counts,
    sheet_source,
    variant_iris,
    variant_stats,
    variant_table,
    write_decision_notes,
    write_folded_history,
    write_gold_version,
)
from folio_eval.gold import build_gold_v2, parse_firm1_v2, parse_firm2_v2
from folio_eval.packet_render import (
    render_sheet,
    render_sheet_v2,
    write_packet,
    write_packet_v2,
)
from folio_eval.resolve_labels import IndexedConcept, LabelIndex
from folio_eval.splits import DEFAULT_GOLD_DIR, sha256_text

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
            "blank-acronym": ranked(
                ("R-court", "Quorum Regional Tribunal - D. Vellaton", 100.0, 0.37)
            ),
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
            IndexedConcept(iri="R-escrow", preferred_labels=("Escrow Services (non-dispute)",)),
            IndexedConcept(iri="R-freight", preferred_labels=("Freight Escrow Practice",)),
        ]
    )


def test_containment_finds_the_longer_folio_label() -> None:
    index = label_index()
    proposals = propose_for_label(
        "Kingdom of Northmarch", index=index, search=lambda _q, limit=20: []
    )
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
        search=lambda _q, limit=20: [
            LabelProposal("R-freight", "Freight Escrow Practice", 41.0, "search")
        ],
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


def rejected_record(
    item_id: str, iris: tuple[str, ...], *, ontology: str = ONTOLOGY_SHA
) -> DecisionRecord:
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
    assert "<script src=" not in html and '<link rel="stylesheet"' not in html

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


def test_v2_packet_carries_the_pairing_and_consistency_sections(
    v2_gold: tuple[Any, list[Any]],
) -> None:
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
    assert sorted(alternative["Odd attribute"]) == sorted([W_LITIGATION, W_ARBITRATION, W_ADVISORY])

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
    assert all(entry.get("origin") for entry in row.gold)
    assert row.extra.get("system_level_mappings")
    # every pipeline candidate carries its own definition snippet, not just the leader
    assert row.pipeline[0]["definition"]
    html = render_sheet_v2(packet)
    assert 'value="keep"' in html and 'value="remove"' in html
    assert 'value="elevate"' in html and 'value="not_gold"' in html
    assert 'class="note gold-note"' in html and 'class="note pipeline-note"' in html


def test_atomized_level_mappings_assign_exact_input_levels() -> None:
    row = PacketRow(
        decision_id="suspect:test",
        section="suspect",
        reason_class="test",
        input_text=(
            "Banking, Finance & Struct Fin > Insurance Finance (non-structured) > Borrower"
        ),
        item_id="item-1",
        firm="Firm",
        stratum="test",
        stratum_id="test",
        surface_label="Borrower",
        ancestor_path=("Banking, Finance & Struct Fin", "Insurance Finance (non-structured)"),
        slice_name="tune",
        suggested_action="review",
        gold=(
            {
                "iri": "https://folio.openlegalstandard.org/banking",
                "label": "Banking Law",
                "column": "SALI 2",
            },
            {
                "iri": "https://folio.openlegalstandard.org/structured",
                "label": "Structured Finance Law",
                "column": "SALI 4",
            },
            {
                "iri": "https://folio.openlegalstandard.org/industry",
                "label": "Finance and Insurance Services Industry",
                "column": "SALI 2",
            },
            {
                "iri": "https://folio.openlegalstandard.org/insurance",
                "label": "Insurance Law",
                "column": "SALI 0 (cascade down)",
            },
            {
                "iri": "https://folio.openlegalstandard.org/lending",
                "label": "Finance and Lending Law",
                "column": "SALI 0 (cascade down)",
            },
        ),
        pipeline=({"iri": "https://folio.openlegalstandard.org/borrower", "label": "Borrower"},),
    )

    assert _atomized_level_mappings(row) == {
        "L1": [
            "https://folio.openlegalstandard.org/banking",
            "https://folio.openlegalstandard.org/structured",
            "https://folio.openlegalstandard.org/industry",
            "https://folio.openlegalstandard.org/lending",
        ],
        "L2": ["https://folio.openlegalstandard.org/insurance"],
        "L3": ["https://folio.openlegalstandard.org/borrower"],
    }


def test_v2_sheet_is_self_contained_and_renders_the_hierarchy(
    v2_gold: tuple[Any, list[Any]],
) -> None:
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
    suspect = next(entry for entry in packet.section("suspect") if entry.item_id == target.item_id)
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


# --------------------------------------------------------------------------------------
# Pairing fold: an item's gold is the UNION of its deduped instances' contributions.
#
# Gold v2 dedupes identical cell text across source-row instances (KTD3 v2), so one item can be
# fed by more than one pairing-ambiguous source row, each independently adjudicated. The fold must
# re-assign only the ROW being decided -- its old reading's contribution out, its chosen reading's
# contribution in -- never replace the item's whole gold set, or confirming one instance's already-
# applied reading silently erases what the item's *other* instances contributed.
# --------------------------------------------------------------------------------------


def _dedup_gold_row(item_id: str, iris: list[str]) -> Any:
    """A minimal gold v2 row whose current gold is the union of two (synthetic) instances."""
    return gold_row_v2_from_json(
        {
            "item_id": item_id,
            "firm": "firm1",
            "stratum": "Corporate",
            "stratum_id": "corporate",
            "family_id": "family-1",
            "level": 3,
            "levels": [3],
            "leaf": "Shared cell",
            "input_text": "Shared cell",
            "gold_iris": iris,
            "values": [],
            "flags": [],
            "rules": ["deduped"],
            "blank": not iris,
            "notes": None,
            "instances": [],
            "provenance": "curator_workbook",
            "gold_version": 2,
        }
    )


def _pairing_row(
    decision_id: str, item_id: str, *, heuristic: list[str], alternative: list[str]
) -> PacketRow:
    """A pairing packet row for one source-row instance, carrying both readings for its target."""
    return PacketRow(
        decision_id=decision_id,
        section="pairing",
        item_id="",
        firm="firm1",
        stratum="Corporate",
        stratum_id="",
        ancestor_path=(),
        surface_label="Shared cell",
        input_text="",
        slice_name="",
        reason_class="pairing_ambiguous",
        suggested_action="",
        extra={
            "assignments": {
                "heuristic": [{"item_id": item_id, "iris": sorted(heuristic)}],
                "alternative": [{"item_id": item_id, "iris": sorted(alternative)}],
            }
        },
    )


def _pairing_packet(rows: tuple[PacketRow, ...]) -> Packet:
    return Packet(
        rows=rows,
        variants=(),
        replay={},
        split=None,
        counts={},
        overflow={},
        meta={"gold_id": "v2-dedup-test", "gold_version": 2},
    )


def test_pairing_confirm_on_a_deduped_multi_instance_item_is_a_no_op() -> None:
    """Two source-row instances feed one deduped item; confirming both readings is a strict no-op."""
    gold = _dedup_gold_row("item-shared", ["iri-x", "iri-y"])
    row1 = _pairing_row("pairing:r1", "item-shared", heuristic=["iri-x"], alternative=["iri-z"])
    row2 = _pairing_row("pairing:r2", "item-shared", heuristic=["iri-y"], alternative=["iri-w"])
    packet = _pairing_packet((row1, row2))
    result = fold_granular_decisions(
        [gold],
        {
            row1.decision_id: {"pairing": "heuristic"},
            row2.decision_id: {"pairing": "heuristic"},
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    by_id = {row["item_id"]: row for row in result.rows}
    assert by_id["item-shared"]["gold_iris"] == ["iri-x", "iri-y"]
    assert result.counts["changed_items"] == 0


def test_pairing_alternative_on_one_instance_preserves_the_other_instance() -> None:
    """Re-assigning one row's reading touches only that row's own contribution to the item."""
    gold = _dedup_gold_row("item-shared", ["iri-x", "iri-y"])
    row1 = _pairing_row("pairing:r1", "item-shared", heuristic=["iri-x"], alternative=["iri-z"])
    row2 = _pairing_row("pairing:r2", "item-shared", heuristic=["iri-y"], alternative=["iri-w"])
    packet = _pairing_packet((row1, row2))
    result = fold_granular_decisions(
        [gold],
        {
            row1.decision_id: {"pairing": "alternative"},
            row2.decision_id: {"pairing": "heuristic"},
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    by_id = {row["item_id"]: row for row in result.rows}
    # row1's old contribution (iri-x) is gone, its new one (iri-z) is in; row2's untouched
    # contribution (iri-y) survives the fold -- the union, not a replacement.
    assert by_id["item-shared"]["gold_iris"] == sorted(["iri-y", "iri-z"])
    assert result.counts["changed_items"] == 1


def test_pairing_reassignment_emptying_gold_flips_the_item_blank() -> None:
    """KD7: an item whose reassigned gold has nothing left becomes blank/coverage, not deleted."""
    gold = _dedup_gold_row("item-shared", ["iri-x", "iri-y"])
    row1 = _pairing_row("pairing:r1", "item-shared", heuristic=["iri-x"], alternative=[])
    row2 = _pairing_row("pairing:r2", "item-shared", heuristic=["iri-y"], alternative=[])
    packet = _pairing_packet((row1, row2))
    result = fold_granular_decisions(
        [gold],
        {
            row1.decision_id: {"pairing": "alternative"},
            row2.decision_id: {"pairing": "alternative"},
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    by_id = {row["item_id"]: row for row in result.rows}
    assert by_id["item-shared"]["gold_iris"] == []
    assert by_id["item-shared"]["blank"] is True
    assert result.counts["changed_items"] == 1


def test_real_packet_confirming_all_prechecked_heuristic_pairings_is_a_no_op(
    tmp_path: Path,
) -> None:
    """Proof against the real audit gate (read-only on real inputs, writes only to tmp_path).

    Submitting the sheet's 106 pre-checked heuristic readings unchanged must not touch a single
    item's gold. Before the fix, the pairing branch replaced each adjudicated item's whole gold set
    with the single row's assignment, dropping whatever the item's *other* deduped instances
    contributed -- 36 items changed for a sheet Damien never touched.
    """
    packet_path = DEFAULT_PACKET_DIR_V2 / "packet.json"
    if not packet_path.exists():
        pytest.skip("real audit packet not present in this checkout")

    packet = packet_v2_from_json(json.loads(packet_path.read_text(encoding="utf-8")))
    current_version = int(packet.meta.get("current_gold_version", packet.meta["gold_version"]))
    current_gold_id = str(packet.meta.get("current_gold_id", packet.meta["gold_id"]))
    gold_path = DEFAULT_GOLD_DIR / f"gold_v{current_version}.jsonl"
    manifest_path = DEFAULT_GOLD_DIR / f"gold_v{current_version}.manifest.json"
    if not (gold_path.exists() and manifest_path.exists()):
        pytest.skip(f"real live gold v{current_version} not present in this checkout")
    # Characterize the original unsubmitted packet state even after the live artifact starts
    # carrying folded-history panels from later adjudication passes.
    packet = replace(
        packet,
        rows=tuple(
            replace(row, extra={key: value for key, value in row.extra.items() if key != "folded"})
            for row in packet.rows
        ),
    )
    rows = load_gold_rows_v2(gold_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ontology_sha256 = str(manifest["ontology_cache_sha256"])

    pairing = packet.section("pairing")
    heuristic_prechecked = [
        row
        for row in pairing
        if row.extra.get("precheck", {}).get("choice") == "heuristic"  # type: ignore[union-attr]
    ]
    assert len(heuristic_prechecked) == 106  # the measured defect scenario

    # Rows already folded are excluded from this blanket resubmission: their own no-op baseline is
    # the *applied* (current-gold) state, not the static heuristic reading (2026-07-28 fix) -- on
    # the real sheet an untouched folded row whose edit diverged from heuristic pre-checks neither
    # radio at all, so it would never submit "pairing": "heuristic" in the first place. Their no-op
    # guarantee is proven separately (test_a_folded_decision_renders_pre_filled_and_fully_enabled).
    unfolded_heuristic_prechecked = [
        row for row in heuristic_prechecked if not row.extra.get("folded")
    ]
    assert len(unfolded_heuristic_prechecked) == 106

    decisions = {row.decision_id: {"pairing": "heuristic"} for row in unfolded_heuristic_prechecked}
    result = fold_granular_decisions(
        rows,
        decisions,
        packet=packet,
        ontology_sha256=ontology_sha256,
        now="2026-07-28T00:00:00Z",
        parent_gold_id=current_gold_id,
        base_gold_version=current_version,
    )
    assert result.counts["changed_items"] == 0

    written = write_gold_version(result, tmp_path)
    assert written["gold"].exists()
    assert written["manifest"].exists()

    print(f"\n[dry-run] all-heuristic confirm changed_items = {result.counts['changed_items']}")


def test_granular_fold_applies_per_level_mappings_and_preserves_unassigned(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    row = packet.section("consistency")[0]
    retained = str(row.gold[0]["iri"])
    added = "https://folio.openlegalstandard.org/R-added"

    result = fold_granular_decisions(
        rows,
        {
            row.decision_id: {
                "level_mappings": {"L1": [retained], "L2": [added]},
                "mapping_options": {"unassigned": []},
                "level_notes": {"L2": "This belongs to the second input level."},
            }
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-08-10T00:00:00Z",
    )

    output = next(payload for payload in result.rows if payload["item_id"] == row.item_id)
    assert output["gold_iris"] == sorted([retained, added])
    assert result.counts["level_mapping_decisions"] == 1
    assert result.notes[row.decision_id]["level_notes"] == {
        "L2": "This belongs to the second input level."
    }

    rejected = fold_granular_decisions(
        rows,
        {
            row.decision_id: {
                "gold": {retained: "remove"},
                "level_mappings": {"L1": [retained, added]},
                "mapping_options": {"unassigned": []},
            }
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-08-10T00:00:00Z",
    )
    rejected_output = next(
        payload for payload in rejected.rows if payload["item_id"] == row.item_id
    )
    assert rejected_output["gold_iris"] == [added]

    with pytest.raises(ValueError, match="invalid canonical FOLIO IRI"):
        fold_granular_decisions(
            rows,
            {row.decision_id: {"level_mappings": {"L1": ["javascript:bad"]}}},
            packet=packet,
            ontology_sha256=ONTOLOGY_SHA,
        )


def test_granular_fold_records_per_candidate_rejections(v2_gold: tuple[Any, list[Any]]) -> None:
    """A 'not gold' verdict becomes rejection memory, so the same proposal never resurfaces."""
    build, rows = v2_gold
    target = _row_by_text(rows, "Unsettled matters")
    packet = v2_packet(
        build,
        rows,
        predictions={target.item_id: ranked(("R-junk", "Office of Water", 90.0, 0.1))},
    )
    suspect = next(entry for entry in packet.section("suspect") if entry.item_id == target.item_id)
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
        fold_granular_decisions(rows, {"nope": {}}, packet=packet, ontology_sha256=ONTOLOGY_SHA)


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
    suspect = next(entry for entry in packet.section("suspect") if entry.item_id == target.item_id)
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


# --------------------------------------------------------------------------------------
# Section A pre-checks — Damien's principle, applied before he opens the sheet
# --------------------------------------------------------------------------------------


def test_a_reading_that_leaves_an_input_mapping_to_nothing_is_dis_preferred() -> None:
    assert pairing_violations([["A"], []]) == (PAIRING_VIOLATION_EMPTY,)
    assert pairing_violations([["A"], ["B"]]) == ()


def test_a_reading_that_lands_one_concept_on_one_input_twice_is_dis_preferred() -> None:
    assert pairing_violations([["A", "A"], ["B"]]) == (PAIRING_VIOLATION_DUPLICATE,)
    # normalization decides sameness, so a case/whitespace variant still counts as a duplicate
    assert pairing_violations([["Widget Law", "widget  law"]]) == (PAIRING_VIOLATION_DUPLICATE,)


def test_two_different_inputs_may_share_a_concept() -> None:
    """Duplication is judged inside an input cell — Damien's worked example turns on this."""
    assert pairing_violations([["A", "B"], ["A", "B"]]) == ()


def test_precheck_picks_the_reading_that_survives_the_principle() -> None:
    empty_alternative = precheck_pairing([["A"], ["B"]], [[], ["A", "B"]])
    assert empty_alternative[0] == "heuristic"
    assert PAIRING_VIOLATION_EMPTY in empty_alternative[2]

    duplicating_heuristic = precheck_pairing([["A", "A"], ["B"]], [["A"], ["B"]])
    assert duplicating_heuristic[0] == "alternative"
    assert PAIRING_VIOLATION_DUPLICATE in duplicating_heuristic[1]


def test_precheck_leaves_both_readings_unchecked_when_both_break_the_principle() -> None:
    """One output block, two inputs: whichever way it falls, an input maps to nothing."""
    choice, heuristic_bad, alternative_bad = precheck_pairing([["A"], []], [[], ["A"]])
    assert choice == ""
    assert PAIRING_VIOLATION_EMPTY in heuristic_bad
    assert PAIRING_VIOLATION_EMPTY in alternative_bad


def test_the_heuristic_wins_a_tie_so_an_untouched_row_stays_a_no_op() -> None:
    assert precheck_pairing([["A"], ["B"]], [["A"], ["B"]])[0] == "heuristic"


def test_the_worked_example_row_pre_checks_the_heuristic() -> None:
    """The shape Damien ruled on: a cascade-down block repeating the per-attribute blocks."""
    heuristic = [
        ["Sprocket Litigation Practice", "Bauble Agreements"],
        ["Sprocket Litigation Practice", "Bauble Agreements"],
    ]
    alternative = [
        [],
        [
            "Sprocket Litigation Practice",
            "Bauble Agreements",
            "Sprocket Litigation Practice",
            "Bauble Agreements",
        ],
    ]
    choice, heuristic_bad, alternative_bad = precheck_pairing(heuristic, alternative)
    assert choice == "heuristic"
    assert heuristic_bad == ()
    assert set(alternative_bad) == {PAIRING_VIOLATION_EMPTY, PAIRING_VIOLATION_DUPLICATE}


def test_the_packet_pre_checks_the_pairing_row_and_the_sheet_renders_it(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    pairing = packet.section("pairing")[0]
    assert pairing.extra["precheck"]["choice"] == "heuristic"
    assert pairing.extra["precheck"]["needs_your_eye"] is False
    assert packet.counts["pairing_precheck_heuristic"] == 1
    assert packet.counts["pairing_needs_your_eye"] == 0
    html = render_sheet_v2(packet)
    assert 'value="heuristic" checked' in html
    assert 'value="alternative" checked' not in html
    assert "dis-preferred" in html


def test_a_row_whose_readings_both_break_the_rule_is_badged_and_left_unchecked(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    _build, rows = v2_gold
    packet = build_packet_v2(
        gold_rows=rows,
        pairing_rows=[
            {
                "firm": "firm1",
                "row": 4,
                "stratum": "Widget Practice",
                "inputs": [
                    {"level": 2, "text": "Shared Category"},
                    {"level": 3, "text": "First attribute"},
                ],
                "blocks": [{"column": "SALI 0 (cascade down)", "values": ["Widget Law"]}],
                "heuristic": [["Widget Law"], []],
                "alternative": [[], ["Widget Law"]],
            }
        ],
        ontology_sha256=ONTOLOGY_SHA,
        gold_id="v2-test",
        generated_at="2026-07-28T00:00:00Z",
    )
    pairing = packet.section("pairing")[0]
    assert pairing.extra["precheck"]["choice"] == ""
    assert pairing.extra["precheck"]["needs_your_eye"] is True
    assert packet.counts["pairing_needs_your_eye"] == 1
    html = render_sheet_v2(packet)
    assert "needs your eye" in html
    assert 'data-kind="pairing" name="pair|' in html
    assert 'value="heuristic" checked' not in html
    assert 'value="alternative" checked' not in html
    # nothing pre-checked means an untouched row emits no decision at all: gold cannot move
    result = fold_granular_decisions(rows, {}, packet=packet, ontology_sha256=ONTOLOGY_SHA)
    assert result.counts["changed_items"] == 0


# --------------------------------------------------------------------------------------
# The three labelled panels, and the original-spreadsheet grid under them
# --------------------------------------------------------------------------------------


def test_every_row_of_every_section_carries_the_three_labelled_panels(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    """Damien must never have to guess which system produced what he is looking at."""
    build, rows = v2_gold
    suspect_row = _row_by_text(rows, "Unsettled matters")
    blank_row = _row_by_text(rows, "General advice")
    packet = v2_packet(
        build,
        rows,
        eligible_strata={suspect_row.stratum_id, blank_row.stratum_id},
        predictions={
            suspect_row.item_id: ranked(("R-pipe", "Enforcement Practice", 100.0, 0.9)),
            blank_row.item_id: ranked(("R-new", "General Advisory", 100.0, 0.9)),
        },
        sheet_sources=[sheet_source("firm1", "firm1-sheet", FIRM1_V2_ROWS)],
    )
    html = render_sheet_v2(packet)
    total = html.count('<article class="row')
    assert total == len(packet.rows) > 0
    # Panel 1 sources from the current gold version, not the pre-fold packet snapshot (Damien,
    # 2026-07-28) -- every row carries it, and the smaller workbook-curation line underneath it.
    assert html.count("Gold — current (v") == total
    # Pairing rows render one workbook-curation line per input cell, so this is a floor, not a
    # 1:1 count with the article total.
    assert html.count("Workbook curation:") >= total
    assert html.count("Current pipeline — folio-resolve today") == total
    assert html.count("Proposed — this sheet") == total
    assert html.count('class="panel source"') == total
    # every decision unit, pairing and consistency included, has somewhere to write a note
    assert html.count('class="note row-note rownote"') == total
    # section A says out loud that the pipeline is reference only
    assert "the pipeline is not involved in the question" in html


def test_the_pipeline_panel_separates_the_committed_answer_from_the_ranked_tail(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = v2_gold
    target = _row_by_text(rows, "Unsettled matters")
    packet = v2_packet(
        build,
        rows,
        answer_config=AnswerRuleConfig(threshold=0.0, top_k=2, calibrated=True),
        predictions={
            target.item_id: ranked(
                ("R-pipe", "Enforcement Practice", 100.0, 0.9),
                ("R-junk", "Office of Water", 95.0, 0.4),
                ("R-tail", "Something Else", 90.0, 0.1),
            )
        },
    )
    suspect = next(entry for entry in packet.section("suspect") if entry.item_id == target.item_id)
    reference = suspect.extra["pipeline_ref"]
    assert [entry["committed"] for entry in reference["candidates"]] == [True, True, False]
    assert reference["ranked_total"] == 3
    assert reference["top_k"] == 2
    assert packet.meta["answer_rule"] == {"top_k": 2, "threshold": 0.0, "calibrated": True}
    html = render_sheet_v2(packet)
    assert "committed answer" in html and "ranked tail" in html


def test_a_suspect_row_quotes_the_workbook_rows_it_came_from(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = v2_gold
    packet = v2_packet(
        build, rows, sheet_sources=[sheet_source("firm1", "firm1-sheet", FIRM1_V2_ROWS)]
    )
    suspect = next(
        entry for entry in packet.section("suspect") if entry.input_text == "Unsettled matters"
    )
    grid = suspect.extra["source_grid"]
    assert grid["unlocated"] == []
    quoted = grid["grids"][0]
    # 1-based row numbers, header on row 1 — the numbering the curator sees in the workbook
    assert [record["row"] for record in quoted["rows"]] == [13]
    assert "Unsettled matters" in quoted["rows"][0]["cells"]
    assert "SALI NOTES" in quoted["headers"]
    # a column no row of this sheet uses is not mirrored
    assert "SALI 6" not in quoted["headers"]
    assert 'table class="sheetgrid"' in render_sheet_v2(packet)


def test_a_row_no_sheet_can_confirm_is_reported_rather_than_guessed_at() -> None:
    source = sheet_source("firm1", "firm1-sheet", FIRM1_V2_ROWS)
    grid = locate_source_rows(
        [source], firm="firm1", row_numbers=[13, 900], needles=["Unsettled matters"]
    )
    assert grid["unlocated"] == [900]
    assert [record["row"] for record in grid["grids"][0]["rows"]] == [13]
    # and a row number whose text does not match this sheet is not silently quoted
    mismatched = locate_source_rows(
        [source], firm="firm1", row_numbers=[13], needles=["Something Else Entirely"]
    )
    assert mismatched["unlocated"] == [13]
    assert mismatched["grids"] == []


def test_the_source_row_grid_escapes_workbook_text() -> None:
    """Workbook cells are arbitrary text; the grid must never inject markup into the sheet."""
    hostile = sheet_source(
        "firm1",
        "hostile-sheet",
        [
            ["Level 3 - Attributes", "SALI 1"],
            ["<script>alert('x')</script>", "Widget & Sons"],
        ],
    )
    grid = locate_source_rows([hostile], firm="firm1", row_numbers=[2], needles=[])
    packet = Packet(
        rows=(
            PacketRow(
                decision_id="suspect:hostile",
                section="suspect",
                item_id="hostile",
                firm="firm1",
                stratum="Widget Practice",
                stratum_id="sid",
                ancestor_path=(),
                surface_label="hostile",
                input_text="hostile",
                slice_name="",
                reason_class="gold_suspect",
                suggested_action="grade it",
                extra={"source_grid": grid},
            ),
        ),
        variants=(),
        replay={},
        split=None,
        counts={},
        overflow={},
        meta={"sections": list(SECTIONS_V2)},
    )
    html = render_sheet_v2(packet)
    assert "<script>alert(&#x27;x&#x27;)</script>" not in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert "Widget &amp; Sons" in html


# --------------------------------------------------------------------------------------
# Notes: every decision unit can carry one, and the fold keeps it out of the committed log
# --------------------------------------------------------------------------------------


def test_a_pairing_note_and_a_consistency_note_survive_the_fold(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    pairing = packet.section("pairing")[0]
    consistency = packet.section("consistency")[0]
    result = fold_granular_decisions(
        rows,
        {
            pairing.decision_id: {
                "pairing": "heuristic",
                "note": "the heading keeps the cascade-down block",
            },
            consistency.decision_id: {
                "note": "same cell, two places, one answer",
                "gold_note": "both concepts stand",
            },
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    assert result.notes[pairing.decision_id] == {"note": "the heading keeps the cascade-down block"}
    assert result.notes[consistency.decision_id] == {
        "note": "same cell, two places, one answer",
        "gold_note": "both concepts stand",
    }
    assert result.counts["notes_recorded"] == 2
    # a note is commentary, never a gold edit
    assert result.counts["changed_items"] == 0
    # and it never reaches the committed, leak-scanned decision log
    for record in result.records:
        assert not set(record.to_json()) & {"note", "gold_note", "pipeline_note"}


def test_a_note_that_is_not_a_string_is_rejected(v2_gold: tuple[Any, list[Any]]) -> None:
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    pairing = packet.section("pairing")[0]
    with pytest.raises(ValueError, match="note must be a string"):
        fold_granular_decisions(
            rows,
            {pairing.decision_id: {"note": ["not", "a", "string"]}},
            packet=packet,
            ontology_sha256=ONTOLOGY_SHA,
        )


def test_decision_notes_land_beside_the_gold_they_explain(
    v2_gold: tuple[Any, list[Any]], tmp_path: Path
) -> None:
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    pairing = packet.section("pairing")[0]
    empty = fold_granular_decisions(rows, {}, packet=packet, ontology_sha256=ONTOLOGY_SHA)
    assert write_decision_notes(empty, tmp_path) is None

    result = fold_granular_decisions(
        rows,
        {pairing.decision_id: {"note": "keep the applied reading"}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    path = write_decision_notes(result, tmp_path)
    assert path is not None and path.name == "decision_notes_v3.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        pairing.decision_id: {"note": "keep the applied reading"}
    }


# --------------------------------------------------------------------------------------
# The pipe cell, end to end — derivation -> gold IRIs -> packet row -> rendered block
#
# Damien, 2026-07-28: "Please recognize the pipe character | as representing multiple tags."
# The derivation always did. What did not was the *sheet*: the packet looked a pairing row's
# input cells up by text alone, so a firm-1 row bound to firm 2's like-named item and the Gold
# panel showed that item's (smaller) mapping. These tests pin every hop of that path.
# --------------------------------------------------------------------------------------


@pytest.fixture
def pipe_gold() -> tuple[Any, list[Any]]:
    """Firm 1's pipe row and firm 2's like-named cell, built into one gold set."""
    build = build_gold_v2(
        [
            parse_firm1_v2(FIRM1_PIPE_ROWS, firm="firm1"),
            parse_firm2_v2(FIRM2_PIPE_ROWS, firm="firm2"),
        ],
        synthetic_index(),
    )
    return build, [gold_row_v2_from_json(item.to_json()) for item in build.items]


def _pipe_packet(build: Any, rows: list[Any], **kwargs: Any) -> Any:
    return build_packet_v2(
        gold_rows=rows,
        pairing_rows=build.pairing_rows,
        inconsistent_groups=build.inconsistent_groups,
        suspects=build.suspects,
        value_iris={value.raw: value.iri for row in rows for value in row.values},
        definitions=DEFINITIONS,
        ontology_sha256=ONTOLOGY_SHA,
        gold_id="v2-test",
        gold_version=2,
        parent_gold_id="v1-test",
        generated_at="2026-07-28T00:00:00Z",
        **kwargs,
    )


def test_a_pipe_cell_is_one_output_block_holding_two_tags(pipe_gold: tuple[Any, list[Any]]) -> None:
    """Pairing happens at cell granularity; the cell's two tags stay two tags inside it."""
    build, _ = pipe_gold
    record = build.pairing_rows[0]
    cascade = record["blocks"][0]
    assert cascade["values"] == ["Widget Manufacturing Law", "Bauble Agreements"]
    assert cascade["from_pipe"] is True
    assert [block["from_pipe"] for block in record["blocks"][1:]] == [False, False]
    # three output cells against two input cells is exactly what makes the row ambiguous
    assert len(record["blocks"]) == 3 and len(record["inputs"]) == 2


def test_each_pipe_split_tag_resolves_to_its_own_gold_iri(pipe_gold: tuple[Any, list[Any]]) -> None:
    _, rows = pipe_gold
    firm1 = next(row for row in rows if row.firm == "firm1" and row.input_text == "Blended Finance")
    assert sorted(firm1.gold_iris) == sorted([W_MANUFACTURING, W_AGREEMENTS])
    assert "pipe_split" in firm1.rules
    # the firm-2 cell of the same name is a different item with a smaller mapping
    firm2 = next(row for row in rows if row.firm == "firm2")
    assert firm2.gold_iris == (W_AGREEMENTS,)
    assert firm1.item_id != firm2.item_id


def test_the_pairing_row_binds_to_its_own_firms_item_not_the_other_firms(
    pipe_gold: tuple[Any, list[Any]],
) -> None:
    """The defect Damien saw: firm 2's like-named cell was shadowing firm 1's gold."""
    build, rows = pipe_gold
    packet = _pipe_packet(build, rows)
    firm1 = next(row for row in rows if row.firm == "firm1" and row.input_text == "Blended Finance")
    firm2 = next(row for row in rows if row.firm == "firm2")

    pairing = packet.section("pairing")[0]
    assert pairing.firm == "firm1"
    bound = {entry["item_id"] for entry in pairing.extra["input_context"]}
    assert bound == {firm1.item_id}
    assert firm2.item_id not in bound
    assert packet.counts["pairing_inputs_unmatched"] == 0

    # and therefore the Gold panel shows BOTH tags the pipe cell named
    shown = {
        str(entry["iri"]) for context in pairing.extra["input_context"] for entry in context["gold"]
    }
    assert shown == {W_MANUFACTURING, W_AGREEMENTS}


def test_the_sheet_renders_a_pipe_cell_as_separate_tags_not_a_comma_joined_string(
    pipe_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = pipe_gold
    packet = _pipe_packet(build, rows)
    assert packet.counts["pairing_pipe_blocks"] == 1
    html = render_sheet_v2(packet)

    assert "pipe cell &rarr; 2 tags" in html or "pipe cell → 2 tags" in html
    # each tag is its own chip carrying its own IRI
    assert html.count('class="concept"') >= 4
    for iri in (W_MANUFACTURING, W_AGREEMENTS):
        assert iri.rsplit("/", 1)[-1][:12] in html
    # the two concepts are never run together into one comma-joined pseudo-concept
    assert "Widget Manufacturing Law, Bauble Agreements" not in html


# --------------------------------------------------------------------------------------
# Pairing gold EDITS — his notes rewrite what an input cell means, not just which cell it hangs on
# --------------------------------------------------------------------------------------


def test_a_pairing_edit_rewrites_only_that_rows_contribution(
    pipe_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = pipe_gold
    packet = _pipe_packet(build, rows)
    pairing = packet.section("pairing")[0]
    firm1 = next(row for row in rows if row.firm == "firm1" and row.input_text == "Blended Finance")

    result = fold_granular_decisions(
        rows,
        {
            pairing.decision_id: {
                "pairing": "heuristic",
                "edited_iris": {firm1.item_id: [W_ADVISORY]},
                "note": "the cell means the service, not the two areas",
            }
        },
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    folded = {str(row["item_id"]): row for row in result.rows}
    assert folded[firm1.item_id]["gold_iris"] == [W_ADVISORY]
    assert folded[firm1.item_id]["provenance"] == "damien_corrected"
    assert result.counts["pairing_gold_edited"] == 1
    assert result.records[0].action == "edit"
    # the other firm's like-named item is untouched
    firm2 = next(row for row in rows if row.firm == "firm2")
    assert folded[firm2.item_id]["gold_iris"] == [W_AGREEMENTS]
    assert folded[firm2.item_id]["provenance"] == "curator_workbook"


def test_a_pairing_edit_naming_an_item_the_row_does_not_touch_is_rejected(
    pipe_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = pipe_gold
    packet = _pipe_packet(build, rows)
    pairing = packet.section("pairing")[0]
    firm2 = next(row for row in rows if row.firm == "firm2")
    with pytest.raises(ValueError, match="not one of this row's input cells"):
        fold_granular_decisions(
            rows,
            {pairing.decision_id: {"pairing": "heuristic", "edited_iris": {firm2.item_id: []}}},
            packet=packet,
            ontology_sha256=ONTOLOGY_SHA,
        )


def test_a_pairing_edit_must_be_an_object_of_item_to_iris(
    pipe_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = pipe_gold
    packet = _pipe_packet(build, rows)
    pairing = packet.section("pairing")[0]
    with pytest.raises(ValueError, match="edited_iris must be an object"):
        fold_granular_decisions(
            rows,
            {pairing.decision_id: {"pairing": "heuristic", "edited_iris": ["nope"]}},
            packet=packet,
            ontology_sha256=ONTOLOGY_SHA,
        )


# --------------------------------------------------------------------------------------
# Section F — machine-proposed gold improvements, and folded rows that stop being asked
# --------------------------------------------------------------------------------------


def test_section_f_asks_accept_or_reject_per_proposed_atom(
    pipe_gold: tuple[Any, list[Any]],
) -> None:
    build, rows = pipe_gold
    firm1 = next(row for row in rows if row.firm == "firm1" and row.input_text == "Blended Finance")
    packet = _pipe_packet(
        build,
        rows,
        improvements=[
            {
                "item_id": firm1.item_id,
                "proposals": [
                    {
                        "iri": W_INDUSTRY,
                        "label": "Trinket Industry",
                        "branch": "Industry and Market",
                        "method": "anchor",
                        "query": "blended",
                        "score": 100.0,
                    }
                ],
            }
        ],
    )
    section = packet.section("improvement")
    assert len(section) == 1
    row = section[0]
    assert row.decision_id == f"improvement:{firm1.item_id}"
    assert row.extra["machine_proposed"] is True
    assert [entry["iri"] for entry in row.pipeline] == [W_INDUSTRY]
    assert packet.counts["improvement_items"] == 1
    assert packet.counts["improvement_proposals"] == 1

    html = render_sheet_v2(packet)
    assert 'data-section="improvement"' in html
    assert "Accept as gold" in html and "machine-proposed" in html
    # a machine row may never delete curated gold: it offers no keep/remove pair of its own
    assert "F &middot; Proposed gold improvements" in html or "F · Proposed gold" in html

    # and accepting one lands it in gold through the verdicts the fold already understands
    result = fold_granular_decisions(
        rows,
        {row.decision_id: {"pipeline": {W_INDUSTRY: "elevate"}}},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
    )
    folded = {str(entry["item_id"]): entry for entry in result.rows}
    assert W_INDUSTRY in folded[firm1.item_id]["gold_iris"]


def test_section_f_is_capped(pipe_gold: tuple[Any, list[Any]]) -> None:
    build, rows = pipe_gold
    firm1 = next(row for row in rows if row.firm == "firm1" and row.input_text == "Blended Finance")
    proposals = [{"iri": W_INDUSTRY, "label": "Trinket Industry", "branch": "Industry and Market"}]
    packet = _pipe_packet(
        build,
        rows,
        improvements=[{"item_id": firm1.item_id, "proposals": proposals}] * 5,
        improvement_cap=2,
    )
    assert len(packet.section("improvement")) == 2


def test_a_folded_decision_renders_pre_filled_and_fully_enabled(
    pipe_gold: tuple[Any, list[Any]],
) -> None:
    """Damien, 2026-07-28: "let me add notes and change items even where you think things are
    settled" -- a folded row shows what was decided, pre-fills it, and never disables anything.
    """
    build, rows = pipe_gold
    packet = _pipe_packet(build, rows)
    pairing = packet.section("pairing")[0]
    applied = _pipe_packet(
        build,
        rows,
        folded_decisions={
            pairing.decision_id: {
                "summary": "heuristic + gold edits",
                "note": "a child cell never implies its parent",
                "gold_version": 3,
                "gold_id": "v3-abc",
            }
        },
    )
    assert applied.counts["folded_rows_locked"] == 1
    row = applied.section("pairing")[0]
    assert row.extra["folded"]["gold_version"] == 3
    assert row.extra["baseline"]["pairing"] == "heuristic"  # no edits on this fixture's row

    html = render_sheet_v2(applied)
    # never disabled -- the whole point of the fix
    assert "<input disabled" not in html
    assert "<textarea disabled" not in html
    assert "data-locked" not in html
    # badged and pre-filled, not silently blank
    assert "applied v3" in html
    assert "a child cell never implies its parent" in html
    # the JS diffs a re-submission against the row's baseline, not a hard skip
    assert "data-baseline=" in html
    assert "baselineOf" in _collect_source(html) and "diffMap" in _collect_source(html)


def _collect_source(html: str) -> str:
    start = html.index("function collect()")
    return html[start : start + 900]


def test_gold_panel_sources_from_latest_gold_version_not_packet_snapshot(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    """Damien caught this (2026-07-28): a reference/grading panel must show what is *live* in
    gold right now, never the pre-fold snapshot ``build_packet_v2`` happened to be called with.
    """
    build, rows = v2_gold
    target = _row_by_text(rows, "Enforcement matters")
    assert {W_ENFORCEMENT, W_PURCHASE} == {str(iri) for iri in target.gold_iris}

    # Simulate a decision that has already moved this item's live gold to a smaller set --
    # exactly what gold v3 looks like after a fold, without needing to run one for this check.
    current_payload = dict(target.payload)
    current_payload["gold_iris"] = [W_ENFORCEMENT]
    current_payload["gold_version"] = 3
    current_rows = [
        gold_row_v2_from_json(current_payload) if row.item_id == target.item_id else row
        for row in rows
    ]

    packet = v2_packet(
        build,
        rows,
        current_gold_rows=current_rows,
        current_gold_version=3,
        current_gold_id="v3-test",
    )
    assert packet.meta["current_gold_version"] == 3
    assert packet.meta["current_gold_id"] == "v3-test"
    row = packet.section("consistency")[0]
    assert row.item_id == target.item_id
    # the askable gold block grades what is live now...
    assert {str(entry["iri"]) for entry in row.gold} == {W_ENFORCEMENT}
    # ...the reference panel agrees...
    assert {str(entry["iri"]) for entry in row.extra["gold_ref"]} == {W_ENFORCEMENT}
    # ...and the original workbook curation is still visible, smaller and separate.
    assert {str(entry["iri"]) for entry in row.extra["workbook_gold"]} == {
        W_ENFORCEMENT,
        W_PURCHASE,
    }

    html = render_sheet_v2(packet)
    assert "Gold — current (v3, includes your corrections)" in html
    assert "Workbook curation:" in html


def test_folded_row_prefill_equals_the_applied_state(v2_gold: tuple[Any, list[Any]]) -> None:
    """A previously-folded row's radios and note box pre-fill to exactly what is live, not blank
    and not the stale carried-forward ruling (Damien, 2026-07-28)."""
    build, rows = v2_gold
    target = _row_by_text(rows, "Enforcement matters")
    predictions = {target.item_id: ranked(("R-pipe", "Enforcement Practice", 100.0, 0.9))}
    packet = v2_packet(build, rows, predictions=predictions)
    consistency = packet.section("consistency")[0]

    applied = v2_packet(
        build,
        rows,
        predictions=predictions,
        folded_decisions={
            consistency.decision_id: {
                "summary": "gold: 2 kept, 0 removed",
                "gold_note": "both concepts stand",
                "gold_version": 3,
                "gold_id": "v3-test",
            }
        },
    )
    row = applied.section("consistency")[0]
    baseline = row.extra["baseline"]
    assert baseline["gold"] == {W_ENFORCEMENT: "keep", W_PURCHASE: "keep"}
    assert baseline["pipeline"] == {"R-pipe": "not_gold"}
    assert baseline["gold_note"] == "both concepts stand"

    html = render_sheet_v2(applied)
    assert 'value="keep" checked' in html
    assert 'value="not_gold" checked' in html
    # the note is pre-filled INTO the textarea (round-trippable), not just echoed as read-only text
    assert '<textarea class="note gold-note" rows="2" name="gold-note|' in html
    assert (
        f'name="gold-note|{consistency.decision_id}" aria-label="note on this cell&rsquo;s gold" '
        'placeholder="note on this cell&rsquo;s gold (optional)">both concepts stand</textarea>'
        in html
    )


def test_amendment_fold_appends_a_new_decision_without_rewriting_the_first(
    v2_gold: tuple[Any, list[Any]], tmp_path: Path
) -> None:
    """A second fold of an already-folded row is an amendment: it lands as an additional decision
    record (never a rewrite of the first) and its delta is computed against what the first fold
    actually applied, not the pre-fold curated baseline (Damien, 2026-07-28)."""
    build, rows = v2_gold
    suspect_row = _row_by_text(rows, "Unsettled matters")
    predictions = {
        suspect_row.item_id: ranked(
            ("R-pipe", "Enforcement Practice", 100.0, 0.9),
            ("R-junk", "Office of Water", 90.0, 0.1),
        )
    }
    packet_v2 = v2_packet(build, rows, predictions=predictions)
    suspect_v2 = next(r for r in packet_v2.section("suspect") if r.item_id == suspect_row.item_id)

    first = fold_granular_decisions(
        rows,
        {
            suspect_v2.decision_id: {
                "gold": {W_LITIGATION: "remove"},
                "pipeline": {"R-pipe": "elevate"},
            }
        },
        packet=packet_v2,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    log_path = tmp_path / "gold_decisions.jsonl"
    append_decisions(log_path, first.records, surfaces=())
    by_id_v3 = {row["item_id"]: row for row in first.rows}
    assert by_id_v3[suspect_row.item_id]["gold_iris"] == ["R-pipe"]
    assert first.manifest["gold_version"] == 3

    # Regenerate the packet fresh at v3 -- the real workflow's "packet-v2 then fold-v2" loop.
    v3_rows = [gold_row_v2_from_json(payload) for payload in first.rows]
    packet_v3 = build_packet_v2(
        gold_rows=v3_rows,
        current_gold_rows=v3_rows,
        pairing_rows=build.pairing_rows,
        inconsistent_groups=build.inconsistent_groups,
        suspects=build.suspects,
        predictions=predictions,
        definitions=DEFINITIONS,
        value_iris={value.raw: value.iri for row in v3_rows for value in row.values},
        ontology_sha256=ONTOLOGY_SHA,
        gold_id="v3-test",
        gold_version=3,
        parent_gold_id="v2-test",
        generated_at="2026-07-28T01:00:00Z",
    )
    suspect_v3 = next(r for r in packet_v3.section("suspect") if r.item_id == suspect_row.item_id)
    # decision-id stability across regenerations means this is the SAME id the first fold used.
    assert suspect_v3.decision_id == suspect_v2.decision_id
    assert {str(entry["iri"]) for entry in suspect_v3.gold} == {"R-pipe"}

    second = fold_granular_decisions(
        v3_rows,
        {suspect_v3.decision_id: {"pipeline": {"R-junk": "elevate"}}},
        packet=packet_v3,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T02:00:00Z",
        parent_gold_id="v3-test",
    )
    by_id_v4 = {row["item_id"]: row for row in second.rows}
    # an addition on top of the first fold's result -- never a reset back toward curated gold
    assert sorted(by_id_v4[suspect_row.item_id]["gold_iris"]) == sorted(["R-junk", "R-pipe"])
    assert second.manifest["gold_version"] == 4
    assert second.manifest["parent_gold_id"] == "v3-test"

    append_decisions(log_path, second.records, surfaces=())
    logged = load_decisions(log_path)
    same_id = [record for record in logged if record.decision_id == suspect_v2.decision_id]
    # append, never rewrite: both folds' records for this decision id are on the log
    assert len(same_id) == 2
    assert same_id[0].resulting_iris == ("R-pipe",)
    assert sorted(same_id[1].resulting_iris) == ["R-junk", "R-pipe"]
    assert same_id[1].gold_version == 3  # the base version THIS fold started from


def test_a_stale_packet_can_fold_against_the_live_gold_version(
    v2_gold: tuple[Any, list[Any]],
) -> None:
    """The CLI may display a stable packet while live gold has advanced since it was built."""
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    live_rows = [gold_row_v2_from_json({**row.payload, "gold_version": 3}) for row in rows]

    result = fold_granular_decisions(
        live_rows,
        {},
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        parent_gold_id="v3-live",
        base_gold_version=3,
    )

    assert result.manifest["gold_version"] == 4
    assert result.manifest["parent_gold_id"] == "v3-live"
    assert all(row["gold_version"] == 4 for row in result.rows)


def test_folded_history_carries_prior_reviews_and_records_notes(
    v2_gold: tuple[Any, list[Any]], tmp_path: Path
) -> None:
    build, rows = v2_gold
    packet = v2_packet(build, rows)
    decision_id = packet.section("pairing")[0].decision_id
    prior_path = tmp_path / "folded_v2.json"
    prior_path.write_text(
        json.dumps({"older": {"summary": "keep", "gold_version": 2, "gold_id": "v2"}}),
        encoding="utf-8",
    )
    decisions = {decision_id: {"pairing": "heuristic", "note": "carry this forward"}}
    result = fold_granular_decisions(
        rows,
        decisions,
        packet=packet,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )

    path = write_folded_history(result, decisions, tmp_path, prior_path=prior_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "folded_v3.json"
    assert payload["older"]["gold_version"] == 2
    assert payload[decision_id] == {
        "summary": "heuristic",
        "note": "carry this forward",
        "gold_version": 3,
        "gold_id": result.manifest["gold_id"],
    }
    assert latest_folded_path(tmp_path, at_most_version=2) == prior_path
    assert latest_folded_path(tmp_path) == path

    amended = write_folded_history(
        result,
        {decision_id: {"pairing": "alternative"}},
        tmp_path,
        prior_path=path,
    )
    amended_payload = json.loads(amended.read_text(encoding="utf-8"))
    assert amended_payload[decision_id]["summary"] == "alternative"
    assert amended_payload[decision_id]["note"] == "carry this forward"

    (tmp_path / "folded_v4.json.tmp").write_text("{}", encoding="utf-8")
    (tmp_path / "folded_vx.json").write_text("{}", encoding="utf-8")
    future = tmp_path / "folded_v5.json"
    future.write_text("{}", encoding="utf-8")
    assert latest_folded_path(tmp_path, at_most_version=4) == path
    assert latest_folded_path(tmp_path) == future
    assert latest_folded_path(tmp_path / "missing") is None


def test_pairing_amendment_diffs_against_the_applied_edit_not_the_original_heuristic(
    pipe_gold: tuple[Any, list[Any]],
) -> None:
    """The real-packet regression this fix had to avoid (2026-07-28): once a pairing row is
    folded, an untouched re-submission (note-only) must stay a no-op, and a genuine second edit
    must land *on top of* the first edit -- never diff against the pre-fold heuristic reading,
    which would silently revert or double-apply Damien's own correction."""
    build, rows = pipe_gold
    firm1 = next(row for row in rows if row.firm == "firm1" and row.input_text == "Blended Finance")
    packet_v2 = _pipe_packet(build, rows)
    pairing_v2 = packet_v2.section("pairing")[0]

    first = fold_granular_decisions(
        rows,
        {
            pairing_v2.decision_id: {
                "pairing": "heuristic",
                "edited_iris": {firm1.item_id: [W_ADVISORY]},
            }
        },
        packet=packet_v2,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T00:00:00Z",
    )
    by_id_v3 = {row["item_id"]: row for row in first.rows}
    assert by_id_v3[firm1.item_id]["gold_iris"] == [W_ADVISORY]

    # Regenerate at v3 with --folded set, exactly as the real "packet-v2 then fold-v2" loop does.
    v3_rows = [gold_row_v2_from_json(payload) for payload in first.rows]
    packet_v3 = build_packet_v2(
        gold_rows=v3_rows,
        current_gold_rows=v3_rows,
        pairing_rows=build.pairing_rows,
        inconsistent_groups=build.inconsistent_groups,
        suspects=build.suspects,
        value_iris={value.raw: value.iri for row in v3_rows for value in row.values},
        definitions=DEFINITIONS,
        ontology_sha256=ONTOLOGY_SHA,
        gold_id="v3-test",
        gold_version=3,
        parent_gold_id="v2-test",
        generated_at="2026-07-28T01:00:00Z",
        folded_decisions={
            pairing_v2.decision_id: {
                "summary": "heuristic + gold edit",
                "note": "the cell means the service, not the two areas",
                "gold_version": 3,
                "gold_id": "v3-test",
            }
        },
    )
    pairing_v3 = packet_v3.section("pairing")[0]
    assert pairing_v3.decision_id == pairing_v2.decision_id
    assert pairing_v3.extra["folded"]["gold_version"] == 3
    applied = {e["item_id"]: e["iris"] for e in pairing_v3.extra["assignments"]["applied"]}
    assert applied[firm1.item_id] == [W_ADVISORY]

    # An untouched re-submission (note only) must not move gold at all.
    untouched = fold_granular_decisions(
        v3_rows,
        {pairing_v3.decision_id: {"note": "still the right call"}},
        packet=packet_v3,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T02:00:00Z",
        parent_gold_id="v3-test",
    )
    by_id_untouched = {row["item_id"]: row for row in untouched.rows}
    assert by_id_untouched[firm1.item_id]["gold_iris"] == [W_ADVISORY]
    assert untouched.counts["changed_items"] == 0

    # A genuine amendment adds to the applied edit -- it does not revert to the original
    # heuristic's {W_MANUFACTURING, W_AGREEMENTS} and does not need to repeat W_ADVISORY.
    amended = fold_granular_decisions(
        v3_rows,
        {pairing_v3.decision_id: {"edited_iris": {firm1.item_id: [W_ADVISORY, W_INDUSTRY]}}},
        packet=packet_v3,
        ontology_sha256=ONTOLOGY_SHA,
        now="2026-07-28T03:00:00Z",
        parent_gold_id="v3-test",
    )
    by_id_amended = {row["item_id"]: row for row in amended.rows}
    assert sorted(by_id_amended[firm1.item_id]["gold_iris"]) == sorted([W_ADVISORY, W_INDUSTRY])
    assert amended.manifest["gold_version"] == 4
