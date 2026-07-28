"""Tests for the gold builder (U2): normalization, label resolution, and the KTD6 derivation.

Every fixture here is synthetic (see ``tests/fixtures/eval_synthetic_workbook.py``) — no firm
surface strings and no real FOLIO snapshot, per KTD1 and ``tests/conftest.py``'s public-repo
policy. The resolver runs against a tiny in-memory label index, so nothing in this file needs
``folio-python``, ``openpyxl``, or the gitignored ``eval/data/`` tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixtures.eval_synthetic_workbook import (
    FIRM1_ROWS,
    FIRM2_MULTIROW_ROWS,
    FIRM2_SECTOR_ROWS,
    FIRM2_WORKTYPE_ROWS,
    W_ADVISORY,
    W_AGREEMENTS,
    W_ARBITRATION,
    W_ASSEMBLY,
    W_ENFORCEMENT,
    W_FINANCE,
    W_INDUSTRY,
    W_LITIGATION,
    W_MANUFACTURING,
    W_PURCHASE,
    synthetic_index,
)
from folio_eval import gold as gold_mod
from folio_eval import normalize as norm
from folio_eval.gold import GoldItem, build_gold, item_id, parse_firm1, parse_firm2, write_gold
from folio_eval.resolve_labels import LabelIndex, resolve_gold_value, resolve_label

# --------------------------------------------------------------------------------------
# normalize.py
# --------------------------------------------------------------------------------------


def test_normalize_label_collapses_whitespace_and_dashes() -> None:
    assert norm.normalize_label("  Gizmo\u2013Assembly   Law  ") == "Gizmo-Assembly Law"
    assert norm.normalize_label("Widget\u00a0Law") == "Widget Law"
    # NFKC folds compatibility forms.
    assert norm.normalize_label("\uff37idget Law") == "Widget Law"


def test_legacy_iri_normalization_including_trailing_space() -> None:
    """AE3: legacy IRIs (and ``lmss:`` short forms) normalize into the FOLIO namespace."""
    target = "https://folio.openlegalstandard.org/RBX1KA0BJR7y27zZSvaLBVE"
    assert norm.normalize_iri("http://lmss.sali.org/RBX1KA0BJR7y27zZSvaLBVE ") == target
    assert norm.normalize_iri("lmss:RBX1KA0BJR7y27zZSvaLBVE") == target
    assert norm.normalize_iri("  https://folio.openlegalstandard.org/RBX1KA0BJR7y27zZSvaLBVE") == target
    assert norm.normalize_iri("Widget Manufacturing Law") is None


def test_relational_and_non_referential_detection() -> None:
    assert norm.is_relational("sali:isMemberOf")
    assert norm.is_relational("Buyer -> Seller")
    assert not norm.is_relational("lmss:RBX1KA0BJR7y27zZSvaLBVE")
    assert norm.is_non_referential("Other")
    assert norm.is_non_referential("  varies ")
    assert not norm.is_non_referential("Other Tax")


def test_pipe_split_and_suspect_marker() -> None:
    assert norm.split_pipe_values("A Law | B Law") == ["A Law", "B Law"]
    assert norm.strip_suspect_marker("Advisory? ") == ("Advisory", True)
    assert norm.strip_suspect_marker("Advisory") == ("Advisory", False)


# --------------------------------------------------------------------------------------
# resolve_labels.py
# --------------------------------------------------------------------------------------


@pytest.fixture
def index() -> LabelIndex:
    return synthetic_index()


def test_resolution_ladder_branches(index: LabelIndex) -> None:
    assert resolve_label("Widget Manufacturing Law", index).branch == "exact_preferred"
    assert resolve_label("widget law", index).branch == "exact_alternative"
    # Dash variant + trailing space resolves only after normalization, and says so.
    normalized = resolve_label("Gizmo-Assembly Law ", index)
    assert normalized.branch == "normalized_preferred"
    assert normalized.iri == W_ASSEMBLY
    # Plural/singular variant.
    lemma = resolve_label("Bauble Agreement", index)
    assert lemma.branch == "lemma_variant"
    assert lemma.iri == W_AGREEMENTS
    assert resolve_label("Utterly Unmappable Concept", index).branch == "unresolved"


def test_ambiguous_label_is_flagged_and_deterministic(index: LabelIndex) -> None:
    first = resolve_label("Contraption Services", index)
    second = resolve_label("Contraption Services", index)
    assert first.ambiguous and len(first.candidates) == 2
    assert first.iri == second.iri == min(first.candidates)


def test_compound_value_parse_records_branch(index: LabelIndex) -> None:
    """KTD6: ``Bucket: Concept`` -> right-hand side first, whole string second, bucket last."""
    rhs = resolve_gold_value("Doodad Finance: Whatsit Purchase and Sale", index, compound=True)
    assert (rhs.parse_branch, rhs.iri) == ("rhs_last", W_PURCHASE)
    whole = resolve_gold_value("Zorb: Special Regime Law", index, compound=True)
    assert whole.parse_branch == "whole"
    bucket = resolve_gold_value("Trinket Industry: something unmappable", index, compound=True)
    assert (bucket.parse_branch, bucket.iri) == ("bucket", W_INDUSTRY)
    # Without the compound flag the whole string is the only candidate.
    plain = resolve_gold_value("Doodad Finance: Whatsit Purchase and Sale", index, compound=False)
    assert plain.parse_branch == "plain" and plain.iri is None


def test_legacy_iri_value_resolves_through_the_iri_branch(index: LabelIndex) -> None:
    legacy = f"http://lmss.sali.org/{W_INDUSTRY.rsplit('/', 1)[1]} "
    resolved = resolve_gold_value(legacy, index)
    assert (resolved.branch, resolved.iri) == ("legacy_iri", W_INDUSTRY)


# --------------------------------------------------------------------------------------
# gold.py — KTD6 derivation over the synthetic cascade sheet
# --------------------------------------------------------------------------------------


@pytest.fixture
def firm1_build(index: LabelIndex) -> gold_mod.GoldBuild:
    return build_gold([parse_firm1(FIRM1_ROWS, firm="firm1")], index)


def _by_leaf(build: gold_mod.GoldBuild) -> dict[str, GoldItem]:
    return {item.leaf: item for item in build.items}


def test_cascade_union_of_own_l2_and_l1(firm1_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm1_build)["Enforcement matters"]
    assert set(item.gold_iris) == {W_ENFORCEMENT, W_ADVISORY, W_INDUSTRY, W_MANUFACTURING}
    assert item.ancestor_path == ("Widget Practice", "Widget Advice")
    assert {v.origin for v in item.values} == {"own", "level2", "level1"}


def test_row_without_own_cells_inherits_and_is_not_blank(firm1_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm1_build)["General advice"]
    assert set(item.gold_iris) == {W_ADVISORY, W_INDUSTRY, W_MANUFACTURING}
    assert not item.blank


def test_blank_only_when_own_and_inherited_are_all_empty(firm1_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm1_build)["Nothing mapped here"]
    assert item.gold_iris == ()
    assert item.blank
    assert firm1_build.counts["items_blank"] == 1


def test_column_position_is_ignored(firm1_build: gold_mod.GoldBuild) -> None:
    """The L2 row spells its two values in SALI 0 and SALI 3; both cascade identically."""
    item = _by_leaf(firm1_build)["General advice"]
    inherited = {v.iri for v in item.values if v.origin == "level2"}
    assert inherited == {W_ADVISORY, W_INDUSTRY}


def test_pipe_delimited_cell_splits_into_two_gold_iris(firm1_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm1_build)["Blended matters"]
    assert {W_PURCHASE, W_FINANCE} <= set(item.gold_iris)
    assert firm1_build.counts["pipe_split_cells"] == 1


def test_relational_expression_excluded_as_its_own_category(firm1_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm1_build)["Membership matters"]
    assert W_ARBITRATION in item.gold_iris
    assert all("isMemberOf" not in raw for raw in item.gold_labels_raw)
    assert firm1_build.counts["excluded_relational"] == 1


def test_normalization_only_resolution_is_logged(firm1_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm1_build)["Assembly matters"]
    assert W_ASSEMBLY in item.gold_iris
    assert firm1_build.branch_histogram["normalized_preferred"] >= 1
    assert any(entry["raw"] == "Gizmo-Assembly Law " for entry in firm1_build.normalization_log)


def test_notes_flag_words_pre_flag_low_confidence(firm1_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm1_build)["Unsettled matters"]
    assert "notes_flagged" in item.flags
    assert W_LITIGATION in item.gold_iris
    assert any(s["item_id"] == item.item_id for s in firm1_build.suspects)


def test_non_referential_leaf_excluded_and_counted(firm1_build: gold_mod.GoldBuild) -> None:
    assert "Other" not in _by_leaf(firm1_build)
    assert firm1_build.counts["excluded_non_referential_leaf"] == 1


def test_l2_and_l3_on_one_row_cascades_to_all_children(firm1_build: gold_mod.GoldBuild) -> None:
    """KTD6: such a row is the L2's mapping, so its siblings inherit it too."""
    items = _by_leaf(firm1_build)
    assert set(items["First attribute"].gold_iris) == {W_MANUFACTURING, W_AGREEMENTS}
    assert set(items["Second attribute"].gold_iris) == {W_MANUFACTURING, W_AGREEMENTS}
    assert firm1_build.counts["level2_level3_same_row"] == 1
    # The sibling inherits values that were written on a row which also named a leaf: the
    # highest-risk cascade, so it is marked for the audit gate rather than left invisible.
    assert "cascade_from_shared_row" in items["Second attribute"].rules
    assert "cascade_from_shared_row" not in items["First attribute"].rules


def test_identical_input_and_gold_pairs_dedupe(firm1_build: gold_mod.GoldBuild) -> None:
    seconds = [i for i in firm1_build.items if i.leaf == "Second attribute"]
    assert len(seconds) == 1
    assert firm1_build.counts["deduped_items"] == 1
    assert len(seconds[0].source_rows) == 2


def test_item_id_is_a_stable_hash_of_the_ktd3_key() -> None:
    first = item_id("firm1", "Widget Practice", ("Widget Practice", "Widget Advice"), "General advice")
    second = item_id("firm1", "Widget Practice", ("Widget Practice", "Widget Advice"), "General advice")
    third = item_id("firm1", "Widget Practice", ("Widget Practice",), "General advice")
    assert first == second != third


# --------------------------------------------------------------------------------------
# gold.py — firm-2 term-set shapes
# --------------------------------------------------------------------------------------


@pytest.fixture
def firm2_build(index: LabelIndex) -> gold_mod.GoldBuild:
    return build_gold(
        [
            parse_firm2(FIRM2_WORKTYPE_ROWS, firm="firm2"),
            parse_firm2(FIRM2_SECTOR_ROWS, firm="firm2"),
            parse_firm2(FIRM2_MULTIROW_ROWS, firm="firm2"),
        ],
        index,
    )


def test_compound_parse_branches_are_recorded_on_items(firm2_build: gold_mod.GoldBuild) -> None:
    items = _by_leaf(firm2_build)
    assert items["Acquisition support"].gold_iris == (W_PURCHASE,)
    assert items["Acquisition support"].values[0].parse_branch == "rhs_last"
    assert items["Sector work"].values[0].parse_branch == "bucket"
    assert firm2_build.branch_histogram["exact_preferred"] >= 1


def test_question_marked_value_routes_to_suspects_and_stays_in_gold(
    firm2_build: gold_mod.GoldBuild,
) -> None:
    item = _by_leaf(firm2_build)["Unclear work"]
    assert item.gold_iris == (W_LITIGATION,)
    assert "suspect_question_mark" in item.flags
    assert any(s["item_id"] == item.item_id for s in firm2_build.suspects)


def test_varies_is_excluded_as_non_referential(firm2_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm2_build)["Varying work"]
    assert item.gold_iris == ()
    assert item.blank
    assert firm2_build.counts["excluded_non_referential_value"] == 1


def test_additional_sali_mapping_is_notes_not_gold(firm2_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm2_build)["Noted work"]
    assert item.gold_iris == (W_ADVISORY,)
    assert item.notes is not None and "Thingamajig Arbitration" in item.notes


def test_unresolved_label_goes_to_the_resolution_batch(firm2_build: gold_mod.GoldBuild) -> None:
    raws = {entry["raw"] for entry in firm2_build.resolution_batch}
    assert "Utterly Unmappable Concept" in raws
    assert firm2_build.counts["unresolved_values"] >= 1


def test_multi_row_term_unions_its_rows(firm2_build: gold_mod.GoldBuild) -> None:
    item = _by_leaf(firm2_build)["Manufacture"]
    assert set(item.gold_iris) == {W_MANUFACTURING, W_ADVISORY}
    assert len(item.source_rows) == 2


# --------------------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------------------


def test_write_gold_emits_versioned_artifacts_deterministically(
    tmp_path: Path, index: LabelIndex
) -> None:
    build = build_gold([parse_firm1(FIRM1_ROWS, firm="firm1")], index)
    out = write_gold(
        build,
        gold_dir=tmp_path / "gold",
        reports_dir=tmp_path / "reports",
        ontology_sha256="0" * 64,
        folio_python_version="9.9.9",
    )
    gold_path = tmp_path / "gold" / "gold_v1.jsonl"
    manifest_path = tmp_path / "gold" / "gold_v1.manifest.json"
    assert gold_path.exists() and manifest_path.exists()
    assert (tmp_path / "gold" / "resolution_batch_v1.jsonl").exists()
    assert (tmp_path / "gold" / "suspects_v1.jsonl").exists()
    assert (tmp_path / "reports" / "worked_examples_v1.md").exists()
    assert out["gold"] == gold_path

    first = gold_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["gold_version"] == 1
    assert manifest["ontology_cache_sha256"] == "0" * 64
    assert manifest["folio_python_version"] == "9.9.9"
    assert manifest["counts"]["items_total"] == len(build.items)
    assert manifest["content_sha256"] == gold_mod.sha256_bytes(first)

    # Rebuilding writes byte-identical content (no timestamps, sorted throughout).
    rebuilt = build_gold([parse_firm1(FIRM1_ROWS, firm="firm1")], index)
    write_gold(
        rebuilt,
        gold_dir=tmp_path / "gold",
        reports_dir=tmp_path / "reports",
        ontology_sha256="0" * 64,
        folio_python_version="9.9.9",
    )
    assert gold_path.read_bytes() == first

    lines = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines()]
    assert {"item_id", "firm", "stratum", "ancestor_path", "leaf", "input_text", "gold_iris"} <= set(
        lines[0]
    )


def test_worked_examples_cover_every_rule_that_fired(tmp_path: Path, index: LabelIndex) -> None:
    build = build_gold(
        [
            parse_firm1(FIRM1_ROWS, firm="firm1"),
            parse_firm2(FIRM2_WORKTYPE_ROWS, firm="firm2"),
            parse_firm2(FIRM2_SECTOR_ROWS, firm="firm2"),
        ],
        index,
    )
    write_gold(
        build,
        gold_dir=tmp_path / "gold",
        reports_dir=tmp_path / "reports",
        ontology_sha256="0" * 64,
        folio_python_version="9.9.9",
    )
    text = (tmp_path / "reports" / "worked_examples_v1.md").read_text(encoding="utf-8")
    for rule in ("cascade_level2", "pipe_split", "compound_rhs", "legacy_iri", "suspect_question_mark"):
        assert rule in text
