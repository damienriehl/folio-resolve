"""Gold loading and the frozen/tune split (U3, R6, KTD4).

Two jobs live here because the second cannot be trusted without the first.

**Loading (R7, KTD7).** :func:`load_gold` reads ``gold_v{N}.jsonl`` and re-hashes it against the
``content_sha256`` recorded in ``gold_v{N}.manifest.json``, and -- when the caller supplies the
observed ontology-cache hash -- against the manifest's ``ontology_cache_sha256``. Either mismatch
raises. A score measured against a gold file that drifted from its manifest is not a score.

**Splitting (KTD4).** The frozen slice is 20% of Firm 1, stratified over its practice groups and
assigned at **Level-2-group granularity** so a cascaded Level-2 mapping can never appear on both
sides of the split. The rules, all asserted again at load:

* Flagged items never enter frozen. A group is frozen-eligible only if *none* of its scored items
  carries a flag in :data:`FROZEN_EXCLUDED_FLAGS` -- group-granular assignment means one flagged
  row disqualifies its whole group, which is the conservative reading of KTD4's "suspects never
  enter frozen" plus "all rows under a parent land in one slice".
* Strata with fewer than :data:`MIN_STRATUM_ITEMS` scored items contribute **zero** frozen items.
  Five of Firm 1's 25 strata have <= 7 scored items; a 20% frozen draw from them is one or two
  rows, which cannot be stratified, cannot move a bootstrap CI, and would cost the tune slice a
  disproportionate share of a scarce stratum. They are listed on the manifest by ``stratum_id``.
* No item key appears in two slices.
* A normalized surface string (the leaf label, ``label_key``-folded) appearing in **both** tune
  and frozen is dropped from frozen. It is dropped from **scoring entirely** -- not moved to tune
  -- because moving it would put its Level-2 group's cascaded gold on both sides of the split,
  which is the leak the group rule exists to prevent. The excluded ids and the number of groups
  they touch are on the manifest.
* The draw is deterministic under the seed pinned in the manifest.

**Measured tension worth knowing (2026-07-27, gold v1-e1f3124bf68b).** Firm 1's 1494 scored items
carry only 1027 distinct leaf surfaces: 207 surfaces are duplicated and account for 674 items
(45%). The 20% stratified target and the surface-disjointness rule therefore cannot both be
satisfied -- the realized frozen slice comes in below 20%, and the shortfall is reported on the
manifest (``realized_frozen_fraction``) rather than papered over by preferring low-duplicate
groups, which would bias the holdout away from exactly the duplicate-label cluster KTD3 flags as
partially unsolvable.

Firm 2 is evaluated whole as the directional cross-firm signal (KD2): it is never split.

The manifest carries item **ids** and stratum/group **ids** only -- no firm surface strings --
but it still lands under gitignored ``eval/data/gold/`` because membership itself is derived data.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .intake import DEFAULT_DATA_DIR
from .normalize import label_key

#: Flags that keep an item out of the frozen slice (KTD4/KTD6 low-confidence pre-flags).
#: ``pairing_ambiguous`` and ``gold_inconsistent`` are the gold-v2 additions: both mark rows whose
#: gold set is *pending adjudication*, and a holdout must not be drawn from gold that may move.
FROZEN_EXCLUDED_FLAGS = frozenset(
    {
        "notes_flagged",
        "suspect_question_mark",
        "ambiguous_label",
        "term_deprecated",
        "pairing_ambiguous",
        "gold_inconsistent",
    }
)

#: The statistical holdout is 20% of Firm 1 (R6).
FROZEN_FRACTION = 0.2

#: Strata smaller than this contribute no frozen items (see module docstring).
MIN_STRATUM_ITEMS = 8

#: Pinned in the split manifest; changing it re-draws the slice and is a deliberate re-baseline.
DEFAULT_SEED = 20260727

TUNE_SLICE = "tune"
FROZEN_SLICE = "frozen"
SIGNAL_SLICE = "firm2"
SLICE_NAMES = (TUNE_SLICE, FROZEN_SLICE, SIGNAL_SLICE)

TUNE_FIRM = "firm1"
SIGNAL_FIRM = "firm2"

DEFAULT_GOLD_DIR = DEFAULT_DATA_DIR / "gold"
SPLIT_MANIFEST_VERSION = 1
DEFAULT_SPLIT_MANIFEST = DEFAULT_GOLD_DIR / f"split_manifest_v{SPLIT_MANIFEST_VERSION}.json"


class GoldIntegrityError(RuntimeError):
    """Raised when gold, its manifest, or the ontology pin disagree (KTD7: abort, never score)."""


class SplitIntegrityError(RuntimeError):
    """Raised when a split manifest violates a KTD4 invariant."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


# --------------------------------------------------------------------------------------
# Gold loading
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoldItemRecord:
    """One scoring item as the harness reads it (a subset of ``gold.GoldItem``'s JSON)."""

    item_id: str
    firm: str
    stratum: str
    stratum_id: str
    ancestor_path: tuple[str, ...]
    leaf: str
    input_text: str
    gold_iris: frozenset[str]
    flags: frozenset[str]
    blank: bool
    #: Gold v2 writes the Level-2 family id directly, because a v2 row's ``ancestor_path`` is
    #: empty by design (KTD3 v2: the pipeline input is the cell text alone) and a Level-2 heading
    #: item must group with its own children rather than with its parent practice group.
    family_id: str = ""

    @property
    def group_id(self) -> str:
        """Stable id of the Level-2 group this item hangs under (the split's assignment unit).

        Gold v2 supplies it as ``family_id``. For gold v1, Firm 1 items carry
        ``ancestor_path = (level1, level2)``, so the path *is* the group key; an item with no
        Level-2 parent groups under its practice group alone. Hashed either way, so the split
        manifest never carries a firm surface string.
        """
        if self.family_id:
            return self.family_id
        payload = json.dumps([self.firm, self.stratum, list(self.ancestor_path)], ensure_ascii=False)
        return sha256_text(payload)[:12]

    @property
    def surface_key(self) -> str:
        """Normalized surface string for the cross-slice duplicate rule (KTD4)."""
        return label_key(self.leaf)

    @property
    def frozen_eligible(self) -> bool:
        return not (self.flags & FROZEN_EXCLUDED_FLAGS)


@dataclass(frozen=True, slots=True)
class GoldSet:
    """Verified gold: the items plus the manifest identity every report must cite."""

    items: tuple[GoldItemRecord, ...]
    gold_id: str
    gold_version: int
    content_sha256: str
    ontology_cache_sha256: str
    manifest: Mapping[str, object]

    def by_id(self) -> dict[str, GoldItemRecord]:
        return {item.item_id: item for item in self.items}

    def scored(self) -> list[GoldItemRecord]:
        """Blank rows are coverage, never denominator (KD7 / AE2)."""
        return [item for item in self.items if not item.blank]


def _record_from_json(payload: Mapping[str, object]) -> GoldItemRecord:
    path_raw = payload.get("ancestor_path") or []
    flags_raw = payload.get("flags") or []
    iris_raw = payload.get("gold_iris") or []
    if not isinstance(path_raw, list) or not isinstance(flags_raw, list) or not isinstance(iris_raw, list):
        raise GoldIntegrityError(f"malformed gold row: {payload.get('item_id')!r}")
    return GoldItemRecord(
        item_id=str(payload["item_id"]),
        firm=str(payload["firm"]),
        stratum=str(payload.get("stratum", "")),
        stratum_id=str(payload["stratum_id"]),
        ancestor_path=tuple(str(part) for part in path_raw),
        leaf=str(payload.get("leaf", "")),
        input_text=str(payload.get("input_text", "")),
        gold_iris=frozenset(str(iri) for iri in iris_raw),
        flags=frozenset(str(flag) for flag in flags_raw),
        blank=bool(payload.get("blank", False)),
        family_id=str(payload.get("family_id", "")),
    )


def load_gold(
    gold_path: Path,
    *,
    manifest_path: Path | None = None,
    ontology_sha256: str | None = None,
) -> GoldSet:
    """Load and verify gold. Content hash always; ontology pin when ``ontology_sha256`` is given.

    Callers that can reach the FOLIO cache (the CLI, via ``selftest.assert_ontology_pin``) pass
    the observed cache hash so a silently swapped ontology snapshot aborts the run rather than
    producing an incomparable score (KTD7).
    """
    manifest_file = manifest_path or gold_path.with_suffix("").with_suffix(".manifest.json")
    if not gold_path.exists():
        raise GoldIntegrityError(f"gold file not found: {gold_path}")
    if not manifest_file.exists():
        raise GoldIntegrityError(f"gold manifest not found: {manifest_file}")

    gold_text = gold_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise GoldIntegrityError(f"gold manifest is not a JSON object: {manifest_file}")

    actual_content = sha256_text(gold_text)
    expected_content = str(manifest.get("content_sha256", ""))
    if actual_content != expected_content:
        raise GoldIntegrityError(
            "gold verification failed: check=content_sha256 "
            f"expected={expected_content} actual={actual_content}"
        )

    expected_ontology = str(manifest.get("ontology_cache_sha256", ""))
    if ontology_sha256 is not None and ontology_sha256 != expected_ontology:
        raise GoldIntegrityError(
            "gold verification failed: check=ontology_cache_sha256 "
            f"expected={expected_ontology} actual={ontology_sha256} — "
            "the ontology snapshot moved; re-baseline deliberately (KTD7) rather than scoring"
        )

    items = tuple(
        _record_from_json(json.loads(line)) for line in gold_text.splitlines() if line.strip()
    )
    seen: set[str] = set()
    for item in items:
        if item.item_id in seen:
            raise GoldIntegrityError(f"duplicate item_id in gold: {item.item_id}")
        seen.add(item.item_id)

    return GoldSet(
        items=items,
        gold_id=str(manifest.get("gold_id", "")),
        gold_version=int(manifest.get("gold_version", 0)),
        content_sha256=actual_content,
        ontology_cache_sha256=expected_ontology,
        manifest=manifest,
    )


# --------------------------------------------------------------------------------------
# Split construction
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StratumPlan:
    """Per-stratum bookkeeping, publishable by ``stratum_id`` alone."""

    stratum_id: str
    firm: str
    scored: int
    frozen: int
    tune: int
    groups: int
    eligible_groups: int
    flagged_items: int
    target: int
    reason: str = ""
    excluded: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "stratum_id": self.stratum_id,
            "firm": self.firm,
            "scored": self.scored,
            "frozen": self.frozen,
            "tune": self.tune,
            "excluded": self.excluded,
            "groups": self.groups,
            "eligible_groups": self.eligible_groups,
            "flagged_items": self.flagged_items,
            "target": self.target,
            "reason": self.reason,
        }


@dataclass
class SplitPlan:
    """The drawn split, before it becomes a manifest."""

    seed: int
    slices: dict[str, list[str]] = field(default_factory=dict)
    strata: list[StratumPlan] = field(default_factory=list)
    excluded_surface_duplicates: list[str] = field(default_factory=list)
    excluded_surface_duplicate_groups: list[str] = field(default_factory=list)
    small_strata: list[str] = field(default_factory=list)

    @property
    def frozen_ids(self) -> list[str]:
        return self.slices.get(FROZEN_SLICE, [])

    @property
    def tune_ids(self) -> list[str]:
        return self.slices.get(TUNE_SLICE, [])


def _target_frozen(count: int, fraction: float) -> int:
    """Round-half-up 20% target, so an 8-item stratum contributes 2, not 1."""
    return int(count * fraction + 0.5)


def build_splits(
    items: Iterable[GoldItemRecord],
    *,
    seed: int = DEFAULT_SEED,
    frozen_fraction: float = FROZEN_FRACTION,
    min_stratum_items: int = MIN_STRATUM_ITEMS,
) -> SplitPlan:
    """Draw the frozen slice deterministically at Level-2-group granularity (KTD4)."""
    scored = sorted((item for item in items if not item.blank), key=lambda item: item.item_id)
    plan = SplitPlan(seed=seed)

    tune_firm_items = [item for item in scored if item.firm == TUNE_FIRM]
    signal_items = [item for item in scored if item.firm == SIGNAL_FIRM]

    by_stratum: dict[str, list[GoldItemRecord]] = {}
    for item in tune_firm_items:
        by_stratum.setdefault(item.stratum_id, []).append(item)

    frozen_ids: list[str] = []
    for stratum_key in sorted(by_stratum):
        stratum_items = by_stratum[stratum_key]
        groups: dict[str, list[GoldItemRecord]] = {}
        for item in stratum_items:
            groups.setdefault(item.group_id, []).append(item)
        flagged = sum(1 for item in stratum_items if not item.frozen_eligible)
        eligible = sorted(
            group_id
            for group_id, members in groups.items()
            if all(member.frozen_eligible for member in members)
        )
        target = _target_frozen(len(stratum_items), frozen_fraction)

        if len(stratum_items) < min_stratum_items:
            plan.small_strata.append(stratum_key)
            plan.strata.append(
                StratumPlan(
                    stratum_id=stratum_key,
                    firm=TUNE_FIRM,
                    scored=len(stratum_items),
                    frozen=0,
                    tune=len(stratum_items),
                    groups=len(groups),
                    eligible_groups=len(eligible),
                    flagged_items=flagged,
                    target=0,
                    reason=f"stratum below min_stratum_items={min_stratum_items}: contributes 0",
                )
            )
            continue

        rng = random.Random(f"{seed}|{stratum_key}")
        order = list(eligible)
        rng.shuffle(order)

        chosen: list[str] = []
        total = 0
        for group_id in order:
            size = len(groups[group_id])
            if total + size <= target:
                chosen.append(group_id)
                total += size
            if total == target:
                break

        reason = ""
        if not chosen and target >= 1 and eligible:
            # Every eligible group overshoots the target. Take the smallest one when it stays
            # within 2x the target, so the stratum is still represented; otherwise contribute 0.
            smallest = min(eligible, key=lambda group_id: (len(groups[group_id]), group_id))
            if len(groups[smallest]) <= 2 * target:
                chosen = [smallest]
                total = len(groups[smallest])
                reason = "no group fit the target; smallest eligible group taken"
            else:
                reason = "every eligible group exceeds 2x the target: stratum contributes 0"
        elif not eligible:
            reason = "no frozen-eligible group (every group carries a flagged item)"

        stratum_frozen = sorted(
            item.item_id for group_id in chosen for item in groups[group_id]
        )
        frozen_ids.extend(stratum_frozen)
        plan.strata.append(
            StratumPlan(
                stratum_id=stratum_key,
                firm=TUNE_FIRM,
                scored=len(stratum_items),
                frozen=len(stratum_frozen),
                tune=len(stratum_items) - len(stratum_frozen),
                groups=len(groups),
                eligible_groups=len(eligible),
                flagged_items=flagged,
                target=target,
                reason=reason,
            )
        )

    frozen_set = set(frozen_ids)

    # KTD4's cross-slice duplicate rule. Tune is everything Firm-1-scored that the draw did not
    # freeze, so it is fixed the moment the draw ends; a frozen item whose normalized surface
    # also occurs there is dropped from scoring altogether (see the module docstring for why it
    # is not moved to tune). One pass, no cascade: dropping an item adds nothing to tune.
    tune_surfaces = {item.surface_key for item in tune_firm_items if item.item_id not in frozen_set}
    excluded = sorted(
        item.item_id
        for item in tune_firm_items
        if item.item_id in frozen_set and item.surface_key in tune_surfaces
    )
    excluded_set = set(excluded)
    touched_groups = sorted(
        {item.group_id for item in tune_firm_items if item.item_id in excluded_set}
    )
    frozen_set -= excluded_set

    if excluded:
        for index, entry in enumerate(plan.strata):
            dropped = sum(
                1 for item in by_stratum.get(entry.stratum_id, []) if item.item_id in excluded_set
            )
            if not dropped:
                continue
            plan.strata[index] = StratumPlan(
                stratum_id=entry.stratum_id,
                firm=entry.firm,
                scored=entry.scored,
                frozen=entry.frozen - dropped,
                tune=entry.tune,
                excluded=dropped,
                groups=entry.groups,
                eligible_groups=entry.eligible_groups,
                flagged_items=entry.flagged_items,
                target=entry.target,
                reason="; ".join(
                    part
                    for part in (entry.reason, f"{dropped} item(s) dropped: surface duplicate")
                    if part
                ),
            )

    plan.excluded_surface_duplicates = excluded
    plan.excluded_surface_duplicate_groups = touched_groups
    plan.slices = {
        TUNE_SLICE: sorted(
            item.item_id
            for item in tune_firm_items
            if item.item_id not in frozen_set and item.item_id not in excluded_set
        ),
        FROZEN_SLICE: sorted(frozen_set),
        SIGNAL_SLICE: sorted(item.item_id for item in signal_items),
    }
    return plan


# --------------------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------------------


def build_split_manifest(plan: SplitPlan, gold: GoldSet) -> dict[str, object]:
    """Assemble the manifest body plus its own content hash."""
    tune_firm_scored = sum(1 for item in gold.scored() if item.firm == TUNE_FIRM)
    frozen_count = len(plan.slices.get(FROZEN_SLICE, []))
    body: dict[str, object] = {
        "split_manifest_version": SPLIT_MANIFEST_VERSION,
        "seed": plan.seed,
        "gold_id": gold.gold_id,
        "gold_version": gold.gold_version,
        "gold_content_sha256": gold.content_sha256,
        "ontology_cache_sha256": gold.ontology_cache_sha256,
        "frozen_fraction": FROZEN_FRACTION,
        "realized_frozen_fraction": round(frozen_count / tune_firm_scored, 6)
        if tune_firm_scored
        else 0.0,
        "tune_firm_scored_items": tune_firm_scored,
        "min_stratum_items": MIN_STRATUM_ITEMS,
        "frozen_excluded_flags": sorted(FROZEN_EXCLUDED_FLAGS),
        "slices": {
            name: {"count": len(plan.slices.get(name, [])), "item_ids": plan.slices.get(name, [])}
            for name in SLICE_NAMES
        },
        "excluded_surface_duplicates": {
            "count": len(plan.excluded_surface_duplicates),
            "item_ids": plan.excluded_surface_duplicates,
            "touched_group_count": len(plan.excluded_surface_duplicate_groups),
            "touched_group_ids": plan.excluded_surface_duplicate_groups,
        },
        "small_strata": plan.small_strata,
        "strata": [entry.to_json() for entry in plan.strata],
    }
    body["content_sha256"] = sha256_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return body


def write_split_manifest(plan: SplitPlan, gold: GoldSet, path: Path) -> dict[str, object]:
    manifest = build_split_manifest(plan, gold)
    _atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


@dataclass(frozen=True, slots=True)
class SplitManifest:
    """A verified split manifest: slice membership plus the identity it was drawn against."""

    seed: int
    gold_id: str
    gold_content_sha256: str
    ontology_cache_sha256: str
    content_sha256: str
    slices: Mapping[str, tuple[str, ...]]
    excluded_surface_duplicates: tuple[str, ...]
    small_strata: tuple[str, ...]
    strata: tuple[Mapping[str, object], ...]

    def slice_items(self, name: str, gold: GoldSet) -> list[GoldItemRecord]:
        by_id = gold.by_id()
        return [by_id[item_id] for item_id in self.slices[name]]


def assert_split_invariants(manifest: Mapping[str, object], gold: GoldSet) -> None:
    """Re-check every KTD4 invariant against the gold the run is actually about to score."""
    stored_hash = str(manifest.get("content_sha256", ""))
    body = {key: value for key, value in manifest.items() if key != "content_sha256"}
    recomputed = sha256_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if stored_hash != recomputed:
        raise SplitIntegrityError(
            f"split manifest hash mismatch: stored={stored_hash} recomputed={recomputed}"
        )

    if str(manifest.get("gold_id", "")) != gold.gold_id:
        raise SplitIntegrityError(
            f"split manifest gold_id={manifest.get('gold_id')!r} != gold {gold.gold_id!r}"
        )
    if str(manifest.get("gold_content_sha256", "")) != gold.content_sha256:
        raise SplitIntegrityError("split manifest was drawn against different gold content")

    slices_raw = manifest.get("slices")
    if not isinstance(slices_raw, dict):
        raise SplitIntegrityError("split manifest has no slices object")

    by_id = gold.by_id()
    membership: dict[str, str] = {}
    for name in SLICE_NAMES:
        entry = slices_raw.get(name)
        if not isinstance(entry, dict):
            raise SplitIntegrityError(f"split manifest is missing slice {name!r}")
        item_ids = entry.get("item_ids")
        if not isinstance(item_ids, list):
            raise SplitIntegrityError(f"slice {name!r} has no item_ids list")
        if entry.get("count") != len(item_ids):
            raise SplitIntegrityError(f"slice {name!r} count disagrees with its item_ids length")
        for item_id in item_ids:
            key = str(item_id)
            record = by_id.get(key)
            if record is None:
                raise SplitIntegrityError(f"slice {name!r} names an item absent from gold: {key}")
            if record.blank:
                raise SplitIntegrityError(f"slice {name!r} contains a blank gold row: {key}")
            previous = membership.get(key)
            if previous is not None:
                raise SplitIntegrityError(
                    f"item {key} appears in two slices: {previous!r} and {name!r}"
                )
            membership[key] = name

    frozen = [by_id[item_id] for item_id in membership if membership[item_id] == FROZEN_SLICE]
    tune = [by_id[item_id] for item_id in membership if membership[item_id] == TUNE_SLICE]

    flagged = sorted(item.item_id for item in frozen if not item.frozen_eligible)
    if flagged:
        raise SplitIntegrityError(f"frozen slice contains flagged items: {flagged[:5]}")

    tune_surfaces = {item.surface_key for item in tune}
    shared = sorted({item.surface_key for item in frozen} & tune_surfaces)
    if shared:
        raise SplitIntegrityError(
            f"{len(shared)} normalized surface string(s) appear in both tune and frozen"
        )

    frozen_groups = {item.group_id for item in frozen}
    straddling = sorted(frozen_groups & {item.group_id for item in tune})
    if straddling:
        raise SplitIntegrityError(
            f"{len(straddling)} Level-2 group(s) straddle the tune/frozen boundary"
        )


def load_split_manifest(path: Path, gold: GoldSet) -> SplitManifest:
    """Read a split manifest and assert every invariant before any score is computed."""
    if not path.exists():
        raise SplitIntegrityError(f"split manifest not found: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise SplitIntegrityError(f"split manifest is not a JSON object: {path}")
    assert_split_invariants(manifest, gold)

    slices_raw = manifest["slices"]
    strata_raw = manifest.get("strata") or []
    duplicates = manifest.get("excluded_surface_duplicates") or {}
    return SplitManifest(
        seed=int(manifest["seed"]),
        gold_id=str(manifest["gold_id"]),
        gold_content_sha256=str(manifest["gold_content_sha256"]),
        ontology_cache_sha256=str(manifest.get("ontology_cache_sha256", "")),
        content_sha256=str(manifest["content_sha256"]),
        slices={
            name: tuple(str(item_id) for item_id in slices_raw[name]["item_ids"])
            for name in SLICE_NAMES
        },
        excluded_surface_duplicates=tuple(
            str(item_id) for item_id in (duplicates.get("item_ids") or [])
        ),
        small_strata=tuple(str(key) for key in (manifest.get("small_strata") or [])),
        strata=tuple(entry for entry in strata_raw if isinstance(entry, dict)),
    )


def summarize_plan(plan: SplitPlan, gold: GoldSet) -> dict[str, object]:
    """Counts-only summary safe to print or commit (ids and counts, no surface strings)."""
    scored = gold.scored()
    return {
        "gold_id": gold.gold_id,
        "seed": plan.seed,
        "items_total": len(gold.items),
        "items_scored": len(scored),
        "items_blank": len(gold.items) - len(scored),
        "slice_sizes": {name: len(plan.slices.get(name, [])) for name in SLICE_NAMES},
        "excluded_surface_duplicates": len(plan.excluded_surface_duplicates),
        "excluded_surface_duplicate_groups": len(plan.excluded_surface_duplicate_groups),
        "realized_frozen_fraction": round(
            len(plan.frozen_ids) / max(1, sum(1 for item in scored if item.firm == TUNE_FIRM)), 4
        ),
        "small_strata": len(plan.small_strata),
        "strata_total": len(plan.strata),
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from .resolve_labels import ontology_cache_sha256

    parser = argparse.ArgumentParser(
        prog="python -m folio_eval.splits",
        description="Draw and verify the frozen/tune split manifest (U3, KTD4).",
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR / "gold_v1.jsonl")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--skip-ontology-pin",
        action="store_true",
        help="do not verify the FOLIO cache hash against the gold manifest (diagnostics only)",
    )
    args = parser.parse_args(argv)

    observed = None if args.skip_ontology_pin else ontology_cache_sha256()
    gold = load_gold(args.gold, manifest_path=args.manifest, ontology_sha256=observed)
    plan = build_splits(gold.scored(), seed=args.seed)
    manifest = write_split_manifest(plan, gold, args.out)
    reloaded = load_gold(args.gold, manifest_path=args.manifest)
    load_split_manifest(args.out, reloaded)

    summary = summarize_plan(plan, gold)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nsplit_manifest_sha256={manifest['content_sha256']}")
    print(f"written: {args.out}")
    print("\n[per-stratum] stratum_id scored frozen tune excl groups elig flag target")
    for entry in plan.strata:
        print(
            f"  {entry.stratum_id} {entry.scored:>5} {entry.frozen:>6} {entry.tune:>5}"
            f" {entry.excluded:>4} {entry.groups:>6} {entry.eligible_groups:>4}"
            f" {entry.flagged_items:>4} {entry.target:>6}  {entry.reason}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
