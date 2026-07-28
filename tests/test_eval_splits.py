"""Split integrity (U3, KTD4) — synthetic gold only; no workbook, no FOLIO, no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from folio_eval.splits import (
    FROZEN_SLICE,
    SIGNAL_SLICE,
    TUNE_SLICE,
    GoldIntegrityError,
    SplitIntegrityError,
    assert_split_invariants,
    build_split_manifest,
    build_splits,
    load_gold,
    load_split_manifest,
    sha256_text,
    write_split_manifest,
)

ONTOLOGY_SHA = "a" * 64


def gold_row(
    item_id: str,
    *,
    firm: str = "firm1",
    stratum: str = "S1",
    leaf: str = "Leaf",
    level2: str = "L2",
    gold_iris: tuple[str, ...] = ("R-1",),
    flags: tuple[str, ...] = (),
    blank: bool = False,
) -> dict[str, Any]:
    ancestor_path = [stratum, level2]
    return {
        "item_id": item_id,
        "firm": firm,
        "stratum": stratum,
        "stratum_id": f"sid-{stratum}",
        "ancestor_path": ancestor_path,
        "leaf": leaf,
        "input_text": " > ".join([*ancestor_path, leaf]),
        "gold_iris": list(gold_iris) if not blank else [],
        "flags": list(flags),
        "blank": blank,
    }


def write_gold(
    tmp_path: Path,
    rows: list[dict[str, Any]],
    *,
    ontology_sha256: str = ONTOLOGY_SHA,
    gold_id: str = "v1-test",
    content_sha256: str | None = None,
) -> Path:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    gold_path = tmp_path / "gold_v1.jsonl"
    gold_path.write_text(text, encoding="utf-8")
    manifest = {
        "gold_id": gold_id,
        "gold_version": 1,
        "content_sha256": content_sha256 or sha256_text(text),
        "ontology_cache_sha256": ontology_sha256,
    }
    (tmp_path / "gold_v1.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return gold_path


def sample_rows() -> list[dict[str, Any]]:
    """25 strata are the real shape; this is the same shape in miniature."""
    rows: list[dict[str, Any]] = []
    # S-big: 20 scored items in 10 Level-2 groups of 2 -> target 4 frozen.
    for group in range(10):
        for member in range(2):
            rows.append(
                gold_row(
                    f"big-{group:02d}-{member}",
                    stratum="S-big",
                    level2=f"G{group:02d}",
                    leaf=f"Big Leaf {group:02d}{member}",
                )
            )
    # S-small: 5 scored items -> below the minimum, contributes 0 frozen items.
    for index in range(5):
        rows.append(
            gold_row(f"small-{index}", stratum="S-small", level2=f"G{index}", leaf=f"Small {index}")
        )
    # S-flagged: 10 items, every group carrying one flagged item -> no eligible group.
    for group in range(5):
        rows.append(
            gold_row(
                f"flag-{group}-0",
                stratum="S-flagged",
                level2=f"G{group}",
                leaf=f"Flagged {group}0",
                flags=("notes_flagged",),
            )
        )
        rows.append(
            gold_row(f"flag-{group}-1", stratum="S-flagged", level2=f"G{group}", leaf=f"Flagged {group}1")
        )
    # S-dupall: 16 items in 8 groups, drawing leaves from a pool of 4 -> every group's surfaces
    # also live in tune, so the duplicate rule releases whatever the draw picked.
    for group in range(8):
        for member in range(2):
            rows.append(
                gold_row(
                    f"dup-{group}-{member}",
                    stratum="S-dupall",
                    level2=f"G{group}",
                    leaf=f"Shared Leaf {(group * 2 + member) % 4}",
                )
            )
    # Blank coverage rows and the Firm-2 signal slice.
    rows.append(gold_row("blank-1", stratum="S-big", level2="G00", leaf="Blank one", blank=True))
    for index in range(6):
        rows.append(
            gold_row(
                f"f2-{index}",
                firm="firm2",
                stratum="WorkType",
                level2=f"T{index}",
                leaf=f"Work Type {index}",
            )
        )
    return rows


@pytest.fixture
def gold_path(tmp_path: Path) -> Path:
    return write_gold(tmp_path, sample_rows())


# -- gold loading --------------------------------------------------------


def test_load_gold_verifies_content_hash(tmp_path: Path) -> None:
    path = write_gold(tmp_path, sample_rows(), content_sha256="b" * 64)
    with pytest.raises(GoldIntegrityError, match="check=content_sha256"):
        load_gold(path)


def test_load_gold_aborts_on_ontology_hash_mismatch(gold_path: Path) -> None:
    load_gold(gold_path, ontology_sha256=ONTOLOGY_SHA)  # matching pin is fine
    with pytest.raises(GoldIntegrityError, match="check=ontology_cache_sha256"):
        load_gold(gold_path, ontology_sha256="c" * 64)


def test_blank_rows_are_never_scored(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    assert any(item.blank for item in gold.items)
    assert all(not item.blank for item in gold.scored())


# -- split construction --------------------------------------------------


def test_no_item_appears_in_two_slices(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    seen: set[str] = set()
    for name in (TUNE_SLICE, FROZEN_SLICE, SIGNAL_SLICE):
        for item_id in plan.slices[name]:
            assert item_id not in seen, item_id
            seen.add(item_id)
    # Blank rows are in no slice at all.
    assert "blank-1" not in seen


def test_flagged_items_never_enter_frozen(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    by_id = gold.by_id()
    assert all(by_id[item_id].frozen_eligible for item_id in plan.frozen_ids)
    flagged_stratum = next(entry for entry in plan.strata if entry.stratum_id == "sid-S-flagged")
    assert flagged_stratum.frozen == 0
    assert flagged_stratum.eligible_groups == 0


def test_small_strata_contribute_no_frozen_items(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    small = next(entry for entry in plan.strata if entry.stratum_id == "sid-S-small")
    assert small.frozen == 0
    assert "min_stratum_items" in small.reason
    assert "sid-S-small" in plan.small_strata


def test_surface_duplicates_are_excluded_from_frozen_and_counted(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    by_id = gold.by_id()
    frozen_surfaces = {by_id[item_id].surface_key for item_id in plan.frozen_ids}
    tune_surfaces = {by_id[item_id].surface_key for item_id in plan.tune_ids}
    assert not (frozen_surfaces & tune_surfaces)
    assert plan.excluded_surface_duplicates, "the engineered duplicate stratum produced no exclusions"
    assert plan.excluded_surface_duplicate_groups
    # Excluded duplicates leave scoring entirely — they are never quietly moved into tune, which
    # would put their Level-2 group's cascaded gold on both sides of the split.
    everywhere = set(plan.slices[TUNE_SLICE]) | set(plan.frozen_ids) | set(plan.slices[SIGNAL_SLICE])
    assert not (set(plan.excluded_surface_duplicates) & everywhere)
    firm1_scored = [record for record in gold.scored() if record.firm == "firm1"]
    assert len(plan.tune_ids) + len(plan.frozen_ids) + len(plan.excluded_surface_duplicates) == len(
        firm1_scored
    )
    dup_stratum = next(entry for entry in plan.strata if entry.stratum_id == "sid-S-dupall")
    assert dup_stratum.excluded > 0
    assert dup_stratum.frozen == 0


def test_frozen_slice_is_about_twenty_percent_of_eligible_strata(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    big = next(entry for entry in plan.strata if entry.stratum_id == "sid-S-big")
    assert big.scored == 20
    assert big.target == 4
    assert big.frozen == 4


def test_firm2_is_evaluated_whole(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    assert len(plan.slices[SIGNAL_SLICE]) == 6
    assert all(item_id.startswith("f2-") for item_id in plan.slices[SIGNAL_SLICE])


def test_split_is_deterministic_under_a_fixed_seed(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    first = build_splits(gold.scored(), seed=4242)
    second = build_splits(gold.scored(), seed=4242)
    assert first.slices == second.slices
    other = build_splits(gold.scored(), seed=99)
    assert other.slices[FROZEN_SLICE] != first.slices[FROZEN_SLICE]


def test_level2_groups_never_straddle_the_boundary(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    by_id = gold.by_id()
    frozen_groups = {by_id[item_id].group_id for item_id in plan.frozen_ids}
    tune_groups = {by_id[item_id].group_id for item_id in plan.tune_ids}
    assert not (frozen_groups & tune_groups)


# -- manifest ------------------------------------------------------------


def test_manifest_round_trips_and_asserts_invariants(gold_path: Path, tmp_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    path = tmp_path / "split_manifest_v1.json"
    write_split_manifest(plan, gold, path)
    manifest = load_split_manifest(path, gold)
    assert manifest.gold_id == gold.gold_id
    assert manifest.slices[FROZEN_SLICE] == tuple(plan.frozen_ids)
    assert len(manifest.slice_items(TUNE_SLICE, gold)) == len(plan.tune_ids)


def test_manifest_rejects_dual_membership(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    manifest = build_split_manifest(plan, gold)
    slices: Any = manifest["slices"]
    stolen = slices[FROZEN_SLICE]["item_ids"][0]
    slices[TUNE_SLICE]["item_ids"].append(stolen)
    slices[TUNE_SLICE]["count"] += 1
    manifest["content_sha256"] = _rehash(manifest)
    with pytest.raises(SplitIntegrityError, match="appears in two slices"):
        assert_split_invariants(manifest, gold)


def test_manifest_rejects_a_flagged_frozen_item(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    manifest = build_split_manifest(plan, gold)
    slices: Any = manifest["slices"]
    slices[TUNE_SLICE]["item_ids"].remove("flag-0-0")
    slices[TUNE_SLICE]["count"] -= 1
    slices[FROZEN_SLICE]["item_ids"].append("flag-0-0")
    slices[FROZEN_SLICE]["count"] += 1
    manifest["content_sha256"] = _rehash(manifest)
    with pytest.raises(SplitIntegrityError, match="flagged items"):
        assert_split_invariants(manifest, gold)


def test_manifest_rejects_a_tampered_hash(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    manifest = build_split_manifest(plan, gold)
    manifest["seed"] = 1
    with pytest.raises(SplitIntegrityError, match="hash mismatch"):
        assert_split_invariants(manifest, gold)


def test_manifest_rejects_a_blank_row_in_a_slice(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    manifest = build_split_manifest(plan, gold)
    slices: Any = manifest["slices"]
    slices[TUNE_SLICE]["item_ids"].append("blank-1")
    slices[TUNE_SLICE]["count"] += 1
    manifest["content_sha256"] = _rehash(manifest)
    with pytest.raises(SplitIntegrityError, match="blank gold row"):
        assert_split_invariants(manifest, gold)


def test_manifest_rejects_gold_it_was_not_drawn_against(gold_path: Path, tmp_path: Path) -> None:
    gold = load_gold(gold_path)
    plan = build_splits(gold.scored())
    manifest = build_split_manifest(plan, gold)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = load_gold(write_gold(other_dir, sample_rows(), gold_id="v2-other"))
    with pytest.raises(SplitIntegrityError, match="gold_id"):
        assert_split_invariants(manifest, other)


def _rehash(manifest: dict[str, Any]) -> str:
    body = {key: value for key, value in manifest.items() if key != "content_sha256"}
    return sha256_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
