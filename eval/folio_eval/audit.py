"""The audit gate: evidence packet, gold-derivation variants, and the decision fold (U5).

Three jobs, in the order Damien meets them.

**1. The packet (KTD9, R11).** :func:`build_packet` merges four queues into one reviewable
object — the cascade/denominator decision, the KTD4 split ratification, the gold suspects, the
R2 resolution batch, and the capped new-gold candidates (KTD5) — and gives every reviewable row
a stable ``decision_id`` plus the evidence KTD9 names: item id, firm/stratum, ancestor path,
surface label, gold labels + IRIs *with their per-IRI origin*, the pipeline's top candidates with
labels and scores, FOLIO definition snippets for the gold and the top pipeline candidate, the
curator's SALI NOTES text, a one-line reason class, and a suggested action. Suspect rows sort by
confidence x label frequency with the SALI-NOTES rows leading (KTD6) and cap at
:data:`SUSPECT_ROW_CAP`; the remainder is reported as counts by reason.

**2. The gold-derivation variants.** The single biggest open question at this gate is whether
inherited (cascaded) gold belongs in the denominator at all: U4 measured own-origin gold at
~21.7% recall against inherited-origin gold at ~2.4%. :func:`variant_iris` recomputes each item's
gold set under two alternatives *without regenerating from the workbooks* — the v1 rows already
carry per-IRI ``origin`` and the item's cascade rule tags:

* ``own_only`` — drop every inherited IRI (``origin`` in :data:`INHERITED_ORIGINS`). An item whose
  gold set empties becomes blank, i.e. coverage rather than denominator (KD7).
* ``no_shared_row_cascade`` — drop only the IRIs whose rule is ``cascade_from_shared_row`` (a
  Level-2 row that also carried a Level-3 label, whose mapping KTD6 cascades to every child).

:func:`replay_counts` re-scores a slice under any variant from the *already committed* per-item
predictions. The committed answer set is gold-blind by construction (KTD2), so changing the gold
set changes only the counting — no pipeline re-run, and no risk of the replay drifting from the
baseline it is being compared against.

**3. The fold (R3, KD5).** :func:`fold_decisions` applies an ``{decision_id: {action, ...}}``
decisions file to the gold rows and emits gold v2 plus its manifest: accepted new-gold items are
tagged ``provenance=pipeline_suggested`` (and flagged so every report can publish a sensitivity
score excluding them, KTD5); accepted corrections and edits are ``damien_corrected``; everything
untouched carries forward as ``curator_workbook``. Every decision also appends a record to
``eval/reports/gold_decisions.jsonl`` — **committed, and therefore IDs, IRIs, decisions, and
reason classes only, never a firm surface string** (KTD1/KTD9), which :func:`append_decisions`
enforces with the U4 leak scanner rather than trusting the caller. Rejections become the
rejection memory: :func:`rejection_memory` keys them by (item id, proposed IRI set) so an
identical proposal is suppressed on the next triage — and released when the ontology hash moves.
The suppressed count is always reported.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .answer_rule import AnswerRuleConfig, RankedCandidate, commit_from_ranked
from .clusters import RawCandidate, assert_no_surfaces, token_jaccard, tokens
from .intake import DEFAULT_DATA_DIR, DEFAULT_MANIFEST_PATH, load_sheet_rows, read_manifest
from .normalize import label_key, plural_variants
from .resolve_labels import LabelIndex
from .score import MicroCounts
from .splits import sha256_text

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

#: KTD9: at most 50 detailed suspect rows per batch; the rest are reported as counts by reason.
SUSPECT_ROW_CAP = 50

#: KTD5: at most 25 new-gold candidates per check-in, drawn from term sets that already have gold.
NEW_GOLD_CAP = 25

#: KTD9 evidence: the first N words of a FOLIO definition, for the gold and pipeline sides.
DEFINITION_SNIPPET_WORDS = 25

#: How many pipeline candidates ride along on a suspect row.
PIPELINE_CANDIDATES_SHOWN = 5

#: How many proposals the resolution batch offers per unresolved label.
RESOLUTION_PROPOSALS_SHOWN = 5

#: Gold-derivation variants for the cascade/denominator decision.
GOLD_VARIANTS = ("v1_as_is", "own_only", "no_shared_row_cascade")

#: Per-IRI origins that mean "cascaded down from a heading", not "this row's own cell" (KTD6).
INHERITED_ORIGINS = frozenset({"level1", "level2"})

#: The item-level rule tag that marks a Level-2 mapping shared with a Level-3 label (KTD6).
SHARED_ROW_RULE = "cascade_from_shared_row"

SECTIONS = ("cascade", "split", "suspect", "resolution", "new_gold")

DECISION_ACTIONS = frozenset({"accept", "reject", "edit"})

PROVENANCE_CURATOR = "curator_workbook"
PROVENANCE_PIPELINE = "pipeline_suggested"
PROVENANCE_CORRECTED = "damien_corrected"

#: The gitignored packet directory (KTD9: packets render as local files, never link-shared).
DEFAULT_PACKET_DIR = DEFAULT_DATA_DIR / "reports" / "audit_packet_v1"

#: The committed decision log. ID-keyed records only.
_EVAL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DECISION_LOG = _EVAL_ROOT / "reports" / "gold_decisions.jsonl"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _as_version(value: object) -> int:
    return int(value) if isinstance(value, (int, str)) and str(value).strip() else 1


def _as_float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _as_int(value: object, default: int = 0) -> int:
    """A JSON integer field read defensively: anything non-numeric reads as ``default``."""
    return int(value) if isinstance(value, (int, float, str)) and str(value).strip() else default


def _items(value: object) -> list[object]:
    """A JSON list field read defensively: anything that is not a list reads as empty."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _iri_count(payload: Mapping[str, object]) -> int:
    iris = payload.get("gold_iris")
    return len(iris) if isinstance(iris, (list, tuple)) else 0


def _digest(*parts: str) -> str:
    return hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()[:8]


def definition_snippet(text: str | None, *, words: int = DEFINITION_SNIPPET_WORDS) -> str:
    """The first ``words`` words of a FOLIO definition, ellipsised when truncated."""
    if not text:
        return ""
    parts = str(text).split()
    if len(parts) <= words:
        return " ".join(parts)
    return " ".join(parts[:words]) + "…"


# --------------------------------------------------------------------------------------
# Gold rows (full fidelity — splits.GoldItemRecord drops the per-IRI provenance this needs)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoldValue:
    """One resolved gold IRI with the provenance the variants and the packet read."""

    raw: str
    iri: str
    origin: str
    column: str = ""
    branch: str = ""
    parse_branch: str = "plain"
    ambiguous: bool = False
    suspect: bool = False

    @property
    def inherited(self) -> bool:
        return self.origin in INHERITED_ORIGINS


@dataclass(frozen=True, slots=True)
class GoldRow:
    """A gold row as written by U2, keeping its original payload for carry-forward."""

    item_id: str
    firm: str
    stratum: str
    stratum_id: str
    ancestor_path: tuple[str, ...]
    leaf: str
    input_text: str
    gold_iris: tuple[str, ...]
    values: tuple[GoldValue, ...]
    flags: tuple[str, ...]
    rules: tuple[str, ...]
    blank: bool
    notes: str | None
    provenance: str
    gold_version: int
    payload: Mapping[str, object]


def _str_list(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if raw is not None and not isinstance(raw, (list, tuple)):
        raise ValueError(f"gold row {payload.get('item_id')!r}: {key} is not a list")
    return tuple(str(entry) for entry in _items(raw))


def gold_row_from_json(payload: Mapping[str, object]) -> GoldRow:
    """Read one ``gold_vN.jsonl`` line, keeping every field the fold must carry forward."""
    values_raw = payload.get("values") or []
    if not isinstance(values_raw, (list, tuple)):
        raise ValueError(f"gold row {payload.get('item_id')!r}: values is not a list")
    values = tuple(
        GoldValue(
            raw=str(entry.get("raw", "")),
            iri=str(entry["iri"]),
            origin=str(entry.get("origin", "own")),
            column=str(entry.get("column", "")),
            branch=str(entry.get("branch", "")),
            parse_branch=str(entry.get("parse_branch", "plain")),
            ambiguous=bool(entry.get("ambiguous", False)),
            suspect=bool(entry.get("suspect", False)),
        )
        for entry in values_raw
        if isinstance(entry, Mapping)
    )
    notes_raw = payload.get("notes")
    return GoldRow(
        item_id=str(payload["item_id"]),
        firm=str(payload.get("firm", "")),
        stratum=str(payload.get("stratum", "")),
        stratum_id=str(payload.get("stratum_id", "")),
        ancestor_path=_str_list(payload, "ancestor_path"),
        leaf=str(payload.get("leaf", "")),
        input_text=str(payload.get("input_text", "")),
        gold_iris=_str_list(payload, "gold_iris"),
        values=values,
        flags=_str_list(payload, "flags"),
        rules=_str_list(payload, "rules"),
        blank=bool(payload.get("blank", False)),
        notes=None if notes_raw is None else str(notes_raw),
        provenance=str(payload.get("provenance", PROVENANCE_CURATOR)),
        gold_version=_as_version(payload.get("gold_version", 1)),
        payload=dict(payload),
    )


def load_gold_rows(path: Path) -> list[GoldRow]:
    """Full-fidelity gold rows. Verification lives in :func:`splits.load_gold`; this is the read."""
    rows: list[GoldRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(gold_row_from_json(json.loads(line)))
    return rows


# --------------------------------------------------------------------------------------
# Gold-derivation variants (the cascade / denominator decision)
# --------------------------------------------------------------------------------------


def variant_iris(row: GoldRow, variant: str) -> tuple[str, ...]:
    """The item's gold IRI set under one derivation variant. Sorted; never mutates ``row``."""
    if variant not in GOLD_VARIANTS:
        raise ValueError(f"unknown gold variant: {variant!r} (expected one of {GOLD_VARIANTS})")
    if variant == "v1_as_is":
        return tuple(sorted(row.gold_iris))
    if variant == "own_only":
        return tuple(sorted({value.iri for value in row.values if not value.inherited}))
    # no_shared_row_cascade: only items whose rules record the shared-row cascade lose their
    # Level-2 inheritance. A firm-1 item inherits from exactly one Level-2 row, so the item-level
    # rule tag and the per-value origin together identify the affected IRIs exactly.
    if SHARED_ROW_RULE not in row.rules:
        return tuple(sorted(row.gold_iris))
    return tuple(sorted({value.iri for value in row.values if value.origin != "level2"}))


@dataclass(frozen=True, slots=True)
class VariantStats:
    """What one derivation variant does to the denominator."""

    variant: str
    items_total: int
    items_scored: int
    items_blank: int
    gold_iris: int
    distinct_iris: int
    mean_set_size: float
    dropped_iris: int

    def to_json(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "items_total": self.items_total,
            "items_scored": self.items_scored,
            "items_blank": self.items_blank,
            "gold_iris": self.gold_iris,
            "distinct_iris": self.distinct_iris,
            "mean_set_size": round(self.mean_set_size, 4),
            "dropped_iris": self.dropped_iris,
        }


def variant_gold(rows: Sequence[GoldRow], variant: str) -> dict[str, tuple[str, ...]]:
    """``item_id -> gold IRI set`` under one variant (empty tuple = the item became blank)."""
    return {row.item_id: variant_iris(row, variant) for row in rows}


def variant_stats(rows: Sequence[GoldRow], variant: str) -> VariantStats:
    """Items / IRIs / mean-set-size under one variant, against v1's IRI total for the delta."""
    sets = variant_gold(rows, variant)
    scored = [iris for iris in sets.values() if iris]
    total_iris = sum(len(iris) for iris in scored)
    distinct = {iri for iris in scored for iri in iris}
    baseline = sum(len(row.gold_iris) for row in rows)
    return VariantStats(
        variant=variant,
        items_total=len(rows),
        items_scored=len(scored),
        items_blank=len(sets) - len(scored),
        gold_iris=total_iris,
        distinct_iris=len(distinct),
        mean_set_size=(total_iris / len(scored)) if scored else 0.0,
        dropped_iris=baseline - total_iris,
    )


def variant_table(rows: Sequence[GoldRow]) -> tuple[VariantStats, ...]:
    """One :class:`VariantStats` per variant, in :data:`GOLD_VARIANTS` order."""
    return tuple(variant_stats(rows, variant) for variant in GOLD_VARIANTS)


def replay_counts(
    predictions: Mapping[str, Sequence[str]],
    gold_by_item: Mapping[str, Sequence[str]],
) -> MicroCounts:
    """Re-count TP/FP/FN from committed predictions against a (possibly different) gold set.

    The committed answer set never saw gold (KTD2), so a variant re-score is pure counting: the
    same predictions, a new denominator. Items whose gold set is empty under the variant leave
    the denominator entirely (KD7 — blanks are coverage), taking their predictions with them.
    """
    counts = MicroCounts()
    for item_id in sorted(gold_by_item):
        gold = {iri for iri in gold_by_item[item_id] if iri}
        if not gold:
            continue
        predicted = {iri for iri in predictions.get(item_id, ()) if iri}
        true_positive = len(predicted & gold)
        counts.items += 1
        counts.gold += len(gold)
        counts.predicted += len(predicted)
        counts.tp += true_positive
        counts.fp += len(predicted - gold)
        counts.fn += len(gold - predicted)
        counts.exact_items += 1 if predicted == gold else 0
        counts.empty_prediction_items += 0 if predicted else 1
    return counts


def replay_variants(
    predictions: Mapping[str, Sequence[str]], rows: Sequence[GoldRow]
) -> dict[str, MicroCounts]:
    """Replayed micro-counts for every variant, keyed by variant name."""
    return {
        variant: replay_counts(predictions, variant_gold(rows, variant))
        for variant in GOLD_VARIANTS
    }


def load_item_predictions(path: Path) -> dict[str, tuple[str, ...]]:
    """Read the committed IRIs per item out of a per-item CSV written by ``report.write_item_csv``."""
    import csv

    out: dict[str, tuple[str, ...]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            item_id = (record.get("item_id") or "").strip()
            if not item_id:
                continue
            out[item_id] = tuple((record.get("committed_iris") or "").split())
    return out


# --------------------------------------------------------------------------------------
# Resolution-batch enrichment (R2)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LabelProposal:
    """A best-effort FOLIO concept for a gold label the R2 ladder could not resolve."""

    iri: str
    label: str
    score: float
    method: str  # "containment" | "search"

    def to_json(self) -> dict[str, object]:
        return {
            "iri": self.iri,
            "label": self.label,
            "score": round(self.score, 3),
            "method": self.method,
        }


SearchFn = Callable[..., Sequence[LabelProposal]]


def _padded_contains(outer: str, inner: str) -> bool:
    """Word-boundary containment: ``art`` must not match inside ``cartography``."""
    return bool(inner) and f" {inner} " in f" {outer} "


def containment_proposals(
    label: str, index: LabelIndex, *, limit: int = RESOLUTION_PROPOSALS_SHOWN
) -> tuple[LabelProposal, ...]:
    """FOLIO labels that contain the gold string (or are contained by it), best fit first.

    This is the rung the R2 exact/normalized/lemma ladder cannot reach: a curator writes a
    country's short name where FOLIO carries its long official form, or a singular service label
    where FOLIO carries the plural with a parenthetical qualifier. Both directions are checked --
    the gold string may be the shorter or the longer of the pair -- at word boundaries, so a
    three-letter fragment cannot match inside an unrelated word.
    """
    needles = {label_key(label)} | {label_key(variant) for variant in plural_variants(label)}
    needles = {needle for needle in needles if needle}
    if not needles:
        return ()
    seen: dict[str, LabelProposal] = {}
    for table in (index.norm_preferred, index.norm_alternative):
        for key, iris in table.items():
            if not key:
                continue
            hit = next(
                (
                    needle
                    for needle in needles
                    if needle != key
                    and (_padded_contains(key, needle) or _padded_contains(needle, key))
                ),
                None,
            )
            if hit is None:
                continue
            ratio = min(len(hit), len(key)) / max(len(hit), len(key))
            for iri in iris:
                current = seen.get(iri)
                if current is None or ratio * 100 > current.score:
                    seen[iri] = LabelProposal(
                        iri=iri,
                        label=index.label_for(iri) or key,
                        score=round(ratio * 100, 3),
                        method="containment",
                    )
    ordered = sorted(seen.values(), key=lambda entry: (-entry.score, entry.iri))
    return tuple(ordered[:limit])


def plausible(label: str, candidate_label: str) -> bool:
    """A proposal is plausible only if it shares a content token with the gold string.

    ``folio-python``'s ``search_by_label`` degrades to a fuzzy fallback on strings FOLIO has no
    concept for, and hands back wholly unrelated concepts (country names, court names) at score
    90 — measured on the real batch, 2026-07-27. Accepting those would both mis-propose and,
    worse, hide the genuine coverage gaps this batch exists to find: before this filter every one
    of the 29 unresolved labels "had a candidate" and the coverage-gap count was zero.
    """
    return bool(tokens(label) & tokens(candidate_label))


def search_proposals(
    label: str, search: SearchFn, *, limit: int = RESOLUTION_PROPOSALS_SHOWN, search_limit: int = 20
) -> tuple[LabelProposal, ...]:
    """Direct-search proposals, junk filtered out and re-ranked by token overlap.

    The provider's own score is kept for display but is not the sort key: it is 90.0 for both a
    real hit and a fuzzy-fallback miss, so token overlap decides the order.
    """
    query = tokens(label)
    kept: list[tuple[float, LabelProposal]] = []
    for entry in search(label, limit=search_limit):
        if not plausible(label, entry.label):
            continue
        kept.append((token_jaccard(query, tokens(entry.label)), entry))
    kept.sort(key=lambda pair: (-pair[0], -pair[1].score, pair[1].iri))
    return tuple(entry for _, entry in kept[:limit])


def propose_for_label(
    label: str,
    *,
    index: LabelIndex,
    search: SearchFn,
    limit: int = RESOLUTION_PROPOSALS_SHOWN,
    search_limit: int = 20,
) -> tuple[LabelProposal, ...]:
    """Containment first (it is the precise rung), then filtered direct search for the rest."""
    proposals = list(containment_proposals(label, index, limit=limit))
    known = {entry.iri for entry in proposals}
    for entry in search_proposals(label, search, limit=limit, search_limit=search_limit):
        if entry.iri in known:
            continue
        known.add(entry.iri)
        proposals.append(entry)
    return tuple(proposals[:limit])


# --------------------------------------------------------------------------------------
# Decision records and the rejection memory (KTD9)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One committed decision. IDs, IRIs, decisions, reason classes — never a surface string."""

    decision_id: str
    item_id: str
    section: str
    action: str
    reason_class: str
    gold_version: int
    ontology_sha256: str
    proposed_iris: tuple[str, ...]
    resulting_iris: tuple[str, ...]
    recorded_at: str

    def to_json(self) -> dict[str, object]:
        return {
            "action": self.action,
            "decision_id": self.decision_id,
            "gold_version": self.gold_version,
            "item_id": self.item_id,
            "ontology_sha256": self.ontology_sha256,
            "proposed_iris": list(self.proposed_iris),
            "reason_class": self.reason_class,
            "recorded_at": self.recorded_at,
            "resulting_iris": list(self.resulting_iris),
            "section": self.section,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> DecisionRecord:
        return cls(
            decision_id=str(payload.get("decision_id", "")),
            item_id=str(payload.get("item_id", "")),
            section=str(payload.get("section", "")),
            action=str(payload.get("action", "")),
            reason_class=str(payload.get("reason_class", "")),
            gold_version=_as_version(payload.get("gold_version", 0)),
            ontology_sha256=str(payload.get("ontology_sha256", "")),
            proposed_iris=_str_list(payload, "proposed_iris"),
            resulting_iris=_str_list(payload, "resulting_iris"),
            recorded_at=str(payload.get("recorded_at", "")),
        )


def load_decisions(path: Path) -> tuple[DecisionRecord, ...]:
    if not path.exists():
        return ()
    return tuple(
        DecisionRecord.from_json(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def append_decisions(
    path: Path, records: Sequence[DecisionRecord], *, surfaces: Iterable[str] = ()
) -> Path:
    """Append to the committed decision log, refusing to write a firm surface string (KTD1)."""
    text = "".join(
        json.dumps(record.to_json(), ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    assert_no_surfaces(text, surfaces, what="gold_decisions.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
    return path


def rejection_key(item_id: str, iris: Iterable[str]) -> str:
    """KTD9's suppression key: the item plus the exact proposed IRI set."""
    return item_id + "|" + "|".join(sorted(iris))


def rejection_memory(records: Iterable[DecisionRecord], *, ontology_sha256: str) -> frozenset[str]:
    """Rejected (item, proposal) pairs still in force — a moved ontology releases them all."""
    return frozenset(
        rejection_key(record.item_id, record.proposed_iris)
        for record in records
        if record.action == "reject"
        and record.proposed_iris
        and record.ontology_sha256 == ontology_sha256
    )


# --------------------------------------------------------------------------------------
# The packet
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SplitFacts:
    """The KTD4 split as realized, for the ratification note."""

    seed: int
    tune: int
    frozen: int
    firm2: int
    excluded_surface_duplicates: int
    realized_frozen_fraction: float
    small_strata: int
    manifest_sha256: str

    def to_json(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "tune": self.tune,
            "frozen": self.frozen,
            "firm2": self.firm2,
            "excluded_surface_duplicates": self.excluded_surface_duplicates,
            "realized_frozen_fraction": round(self.realized_frozen_fraction, 4),
            "small_strata": self.small_strata,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class PacketRow:
    """One reviewable row: a stable decision id plus every piece of KTD9 evidence."""

    decision_id: str
    section: str
    item_id: str
    firm: str
    stratum: str
    stratum_id: str
    ancestor_path: tuple[str, ...]
    surface_label: str
    input_text: str
    slice_name: str
    reason_class: str
    suggested_action: str
    gold: tuple[Mapping[str, object], ...] = ()
    pipeline: tuple[Mapping[str, object], ...] = ()
    proposed_iris: tuple[str, ...] = ()
    notes_text: str | None = None
    confidence: float = 0.0
    label_frequency: int = 0
    sort_score: float = 0.0
    extra: Mapping[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "section": self.section,
            "item_id": self.item_id,
            "firm": self.firm,
            "stratum": self.stratum,
            "stratum_id": self.stratum_id,
            "ancestor_path": list(self.ancestor_path),
            "surface_label": self.surface_label,
            "input_text": self.input_text,
            "slice": self.slice_name,
            "reason_class": self.reason_class,
            "suggested_action": self.suggested_action,
            "gold": [dict(entry) for entry in self.gold],
            "pipeline": [dict(entry) for entry in self.pipeline],
            "proposed_iris": list(self.proposed_iris),
            "notes_text": self.notes_text,
            "confidence": round(self.confidence, 6),
            "label_frequency": self.label_frequency,
            "sort_score": round(self.sort_score, 6),
            "extra": dict(self.extra),
        }


@dataclass(frozen=True, slots=True)
class Packet:
    """The whole audit gate: rows, the variant table, the replay, and the counts that frame it."""

    rows: tuple[PacketRow, ...]
    variants: tuple[VariantStats, ...]
    replay: Mapping[str, Mapping[str, object]]
    split: SplitFacts | None
    counts: Mapping[str, int]
    overflow: Mapping[str, int]
    meta: Mapping[str, object]

    def section(self, name: str) -> tuple[PacketRow, ...]:
        return tuple(row for row in self.rows if row.section == name)

    def to_json(self) -> dict[str, object]:
        return {
            "meta": dict(self.meta),
            "counts": dict(self.counts),
            "overflow": dict(self.overflow),
            "variants": [entry.to_json() for entry in self.variants],
            "replay": {key: dict(value) for key, value in self.replay.items()},
            "split": self.split.to_json() if self.split else None,
            "rows": [row.to_json() for row in self.rows],
        }


def _gold_evidence(
    row: GoldRow | None,
    iris: Sequence[str],
    labels: Sequence[str],
    definitions: Mapping[str, str],
) -> tuple[Mapping[str, object], ...]:
    """Gold labels + IRIs + per-IRI origin + definition snippet, in IRI order."""
    by_iri: dict[str, GoldValue] = {}
    if row is not None:
        by_iri = {value.iri: value for value in row.values}
    fallback = dict(zip(sorted(iris), labels, strict=False)) if labels else {}
    out: list[Mapping[str, object]] = []
    for iri in sorted(iris):
        value = by_iri.get(iri)
        out.append(
            {
                "iri": iri,
                "label": value.raw if value else fallback.get(iri, ""),
                "origin": value.origin if value else "",
                "column": value.column if value else "",
                "branch": value.branch if value else "",
                "definition": definition_snippet(definitions.get(iri)),
            }
        )
    return tuple(out)


def _pipeline_evidence(
    candidates: Sequence[RankedCandidate],
    definitions: Mapping[str, str],
    *,
    shown: int = PIPELINE_CANDIDATES_SHOWN,
) -> tuple[Mapping[str, object], ...]:
    """Top pipeline candidates with labels + scores; the leader also carries its definition."""
    out: list[Mapping[str, object]] = []
    for position, candidate in enumerate(candidates[:shown]):
        out.append(
            {
                "iri": candidate.iri,
                "label": candidate.label,
                "score": candidate.score,
                "probability": round(candidate.probability, 6),
                "rank": candidate.rank,
                "extraction_path": candidate.extraction_path,
                "definition": definition_snippet(definitions.get(candidate.iri))
                if position == 0
                else "",
            }
        )
    return tuple(out)


def _label_frequency(rows: Sequence[GoldRow]) -> dict[str, int]:
    frequency: dict[str, int] = {}
    for row in rows:
        key = label_key(row.leaf)
        frequency[key] = frequency.get(key, 0) + 1
    return frequency


def _strata_with_gold(rows: Sequence[GoldRow]) -> frozenset[str]:
    """KTD5: new-gold candidates come only from term sets that already have gold."""
    return frozenset(row.stratum_id for row in rows if not row.blank and row.gold_iris)


def build_packet(
    *,
    gold_rows: Sequence[GoldRow],
    suspects: Sequence[Mapping[str, object]] = (),
    resolution_batch: Sequence[Mapping[str, object]] = (),
    cluster_rows: Sequence[Mapping[str, object]] = (),
    predictions: Mapping[str, Sequence[RankedCandidate]] | None = None,
    definitions: Mapping[str, str] | None = None,
    label_proposals: Mapping[str, Sequence[LabelProposal]] | None = None,
    frozen_ids: Iterable[str] = (),
    rejected: Iterable[str] = (),
    eligible_strata: Iterable[str] | None = None,
    slice_by_item: Mapping[str, str] | None = None,
    split: SplitFacts | None = None,
    replay: Mapping[str, Mapping[str, object]] | None = None,
    ontology_sha256: str = "",
    gold_version: int = 1,
    gold_id: str = "",
    harness_config_sha256: str = "",
    generated_at: str | None = None,
    suspect_cap: int = SUSPECT_ROW_CAP,
    new_gold_cap: int = NEW_GOLD_CAP,
    extra_meta: Mapping[str, object] | None = None,
) -> Packet:
    """Assemble the audit gate. Reads gold; never writes it (AE1: gold moves only at the fold)."""
    predictions = predictions or {}
    definitions = definitions or {}
    label_proposals = label_proposals or {}
    slice_by_item = slice_by_item or {}
    frozen = frozenset(frozen_ids)
    memory = frozenset(rejected)
    eligible = (
        frozenset(eligible_strata) if eligible_strata is not None else _strata_with_gold(gold_rows)
    )
    by_id = {row.item_id: row for row in gold_rows}
    frequency = _label_frequency(gold_rows)
    variants = variant_table(gold_rows)

    counts: dict[str, int] = {
        "suspects_total": 0,
        "suspects_shown": 0,
        "score_driven_suspects": 0,
        "frozen_suspects_barred": 0,
        "suppressed_by_rejection_memory": 0,
        "resolution_total": 0,
        "resolution_with_candidates": 0,
        "resolution_coverage_gaps": 0,
        "new_gold_pool": 0,
        "new_gold_shown": 0,
    }
    overflow: dict[str, int] = {}
    rows: list[PacketRow] = []

    # -- [1] the cascade / denominator decision -----------------------------------------
    rows.append(
        PacketRow(
            decision_id="cascade:gold-derivation:v1",
            section="cascade",
            item_id="",
            firm="",
            stratum="",
            stratum_id="",
            ancestor_path=(),
            surface_label="Gold derivation: does inherited (cascaded) gold stay in the denominator?",
            input_text="",
            slice_name="",
            reason_class="cascade_denominator",
            suggested_action=(
                "Accept = keep v1 as-is (own + cascaded gold). Reject = own_only (drop every "
                "inherited IRI; items that empty become coverage). Edit = name a variant in the "
                "note, e.g. no_shared_row_cascade (drop only the Level-2-shared-row cascade)."
            ),
            extra={
                "variants": [entry.to_json() for entry in variants],
                "replay": {key: dict(value) for key, value in (replay or {}).items()},
            },
        )
    )

    # -- [2] KTD4 split ratification ----------------------------------------------------
    if split is not None:
        rows.append(
            PacketRow(
                decision_id="split:ktd4-ratification:v1",
                section="split",
                item_id="",
                firm="",
                stratum="",
                stratum_id="",
                ancestor_path=(),
                surface_label="Ratify the frozen/tune split as drawn",
                input_text="",
                slice_name="",
                reason_class="split_ratification",
                suggested_action=(
                    "Accept = the split stands and the frozen slice stays sealed until the final "
                    "report. Reject = re-draw (a re-draw re-baselines every number measured so "
                    "far). Edit = note the constraint you want changed."
                ),
                extra=split.to_json(),
            )
        )

    # -- [3] suspects -------------------------------------------------------------------
    candidates: list[PacketRow] = []
    for entry in suspects:
        item_id = str(entry.get("item_id", ""))
        row = by_id.get(item_id)
        reasons = [str(reason) for reason in _items(entry.get("reasons"))]
        reason_class = reasons[0].split(":", 1)[0] if reasons else "gold_suspect"
        ranked = list(predictions.get(item_id, ()))
        proposed = tuple(candidate.iri for candidate in ranked[:1])
        confidence = ranked[0].probability if ranked else 0.0
        leaf = str(entry.get("leaf", row.leaf if row else ""))
        source_rows = ",".join(str(value) for value in _items(entry.get("source_rows")))
        candidates.append(
            PacketRow(
                decision_id=f"suspect:{item_id}:{_digest(reason_class, source_rows, *proposed)}",
                section="suspect",
                item_id=item_id,
                firm=str(entry.get("firm", row.firm if row else "")),
                stratum=str(entry.get("stratum", row.stratum if row else "")),
                stratum_id=row.stratum_id if row else "",
                ancestor_path=tuple(str(part) for part in _items(entry.get("ancestor_path"))),
                surface_label=leaf,
                input_text=row.input_text if row else leaf,
                slice_name=slice_by_item.get(item_id, ""),
                reason_class=reason_class,
                suggested_action=_suspect_action(reason_class, proposed),
                gold=_gold_evidence(
                    row,
                    [str(iri) for iri in _items(entry.get("gold_iris"))],
                    [str(label) for label in _items(entry.get("gold_labels_raw"))],
                    definitions,
                ),
                pipeline=_pipeline_evidence(ranked, definitions),
                proposed_iris=proposed,
                notes_text=None if entry.get("notes") is None else str(entry.get("notes")),
                confidence=confidence,
                label_frequency=frequency.get(label_key(leaf), 1),
                sort_score=confidence * max(frequency.get(label_key(leaf), 1), 1),
                extra={"reasons": reasons, "source": "gold_builder"},
            )
        )

    # score-driven suspects: own-origin synonymy misses whose leaf and gold share no token
    for entry in _score_driven_suspects(cluster_rows, by_id):
        item_id = str(entry["item_id"])
        if item_id in frozen:
            counts["frozen_suspects_barred"] += 1
            continue
        row = by_id.get(item_id)
        if row is None:
            continue
        ranked = list(predictions.get(item_id, ()))
        proposed = tuple(candidate.iri for candidate in ranked[:1])
        confidence = ranked[0].probability if ranked else 0.0
        counts["score_driven_suspects"] += 1
        missed = entry["gold_iris"]
        gold_iris = [str(iri) for iri in missed] if isinstance(missed, list) else []
        candidates.append(
            PacketRow(
                decision_id=f"suspect:{item_id}:{_digest('own_origin_synonymy', *proposed)}",
                section="suspect",
                item_id=item_id,
                firm=row.firm,
                stratum=row.stratum,
                stratum_id=row.stratum_id,
                ancestor_path=row.ancestor_path,
                surface_label=row.leaf,
                input_text=row.input_text,
                slice_name=slice_by_item.get(item_id, str(entry.get("slice", ""))),
                reason_class="own_origin_synonymy",
                suggested_action=_suspect_action("own_origin_synonymy", proposed),
                gold=_gold_evidence(row, gold_iris, [], definitions),
                pipeline=_pipeline_evidence(ranked, definitions),
                proposed_iris=proposed,
                notes_text=row.notes,
                confidence=confidence,
                label_frequency=frequency.get(label_key(row.leaf), 1),
                sort_score=confidence * max(frequency.get(label_key(row.leaf), 1), 1),
                extra={"source": "cluster_triage", "missed_gold_iris": gold_iris},
            )
        )

    counts["suspects_total"] = len(candidates)
    counts["deferred_to_consistency"] = sum(
        1 for row in rows if row.section == "consistency" and row.item_id
    )
    kept, suppressed = _suppress(candidates, memory)
    counts["suppressed_by_rejection_memory"] += suppressed
    kept.sort(key=_suspect_sort_key)
    shown, spilled = kept[:suspect_cap], kept[suspect_cap:]
    for spill in spilled:
        overflow[spill.reason_class] = overflow.get(spill.reason_class, 0) + 1
    counts["suspects_shown"] = len(shown)
    rows.extend(shown)

    # -- [4] the R2 resolution batch ----------------------------------------------------
    seen_resolution: set[str] = set()
    by_label: dict[str, list[str]] = {}
    for entry in resolution_batch:
        key_label = str(entry.get("normalized") or entry.get("raw") or "")
        holder = by_label.setdefault(key_label, [])
        holder_id = str(entry.get("item_id", ""))
        if holder_id and holder_id not in holder:
            holder.append(holder_id)
    for entry in resolution_batch:
        normalized = str(entry.get("normalized") or entry.get("raw") or "")
        item_id = str(entry.get("item_id", ""))
        key = f"{normalized}|{entry.get('reason', '')}"
        if key in seen_resolution:
            continue
        seen_resolution.add(key)
        counts["resolution_total"] += 1
        proposals = tuple(label_proposals.get(normalized, ()))
        reason_class = str(entry.get("reason", "unresolved"))
        if proposals:
            counts["resolution_with_candidates"] += 1
        else:
            counts["resolution_coverage_gaps"] += 1
            reason_class = "folio_coverage_gap"
        gold_row = by_id.get(item_id)
        rows.append(
            PacketRow(
                decision_id=f"resolution:{_digest(normalized, str(entry.get('reason', '')))}",
                section="resolution",
                item_id=item_id,
                firm=str(entry.get("firm", "")),
                stratum=str(entry.get("stratum", "")),
                stratum_id=gold_row.stratum_id if gold_row else "",
                ancestor_path=tuple(str(part) for part in _items(entry.get("ancestor_path"))),
                surface_label=normalized,
                input_text=gold_row.input_text if gold_row else str(entry.get("leaf", "")),
                slice_name=slice_by_item.get(item_id, ""),
                reason_class=reason_class,
                suggested_action=(
                    "Accept = map this label to the first proposal. Reject = FOLIO has no concept "
                    "for it (stays excluded and counted as coverage). Edit = paste the right IRIs."
                    if proposals
                    else "No plausible FOLIO concept found — Accept records the coverage gap; "
                    "Edit if you know the IRI."
                ),
                pipeline=tuple(proposal.to_json() for proposal in proposals),
                proposed_iris=tuple(proposal.iri for proposal in proposals[:1]),
                confidence=proposals[0].score / 100 if proposals else 0.0,
                extra={
                    "raw": str(entry.get("raw", "")),
                    "column": str(entry.get("column", "")),
                    "origin": str(entry.get("origin", "")),
                    "parse_branch": str(entry.get("parse_branch", "")),
                    "occurrences": len(by_label.get(normalized, ())),
                    "item_ids": list(by_label.get(normalized, ())),
                },
            )
        )

    # -- [5] new-gold candidates (KTD5) -------------------------------------------------
    pool: list[PacketRow] = []
    for row in gold_rows:
        if not row.blank or row.stratum_id not in eligible:
            continue
        ranked = list(predictions.get(row.item_id, ()))
        if not ranked:
            continue
        counts["new_gold_pool"] += 1
        proposed = tuple(candidate.iri for candidate in ranked[:1])
        confidence = ranked[0].probability
        exact_label = label_key(row.leaf) == label_key(ranked[0].label)
        pool.append(
            PacketRow(
                decision_id=f"new_gold:{row.item_id}:{_digest(*proposed)}",
                section="new_gold",
                item_id=row.item_id,
                firm=row.firm,
                stratum=row.stratum,
                stratum_id=row.stratum_id,
                ancestor_path=row.ancestor_path,
                surface_label=row.leaf,
                input_text=row.input_text,
                slice_name=slice_by_item.get(row.item_id, ""),
                reason_class="new_gold_candidate",
                suggested_action=(
                    "Accept = the proposed concept becomes gold for this blank row, tagged "
                    "provenance=pipeline_suggested (every report carries a sensitivity score "
                    "excluding these). Reject = the row stays blank. Edit = paste the right IRIs."
                ),
                pipeline=_pipeline_evidence(ranked, definitions),
                proposed_iris=proposed,
                notes_text=row.notes,
                confidence=confidence,
                label_frequency=frequency.get(label_key(row.leaf), 1),
                sort_score=confidence,
                extra={
                    "source": "blank_row",
                    "exact_label_match": exact_label,
                    "top_score": ranked[0].score,
                    "candidates_ranked": len(ranked),
                },
            )
        )
    pool, suppressed_new = _suppress(pool, memory)
    counts["suppressed_by_rejection_memory"] += suppressed_new
    pool.sort(key=_new_gold_sort_key)
    counts["new_gold_shown"] = min(len(pool), new_gold_cap)
    rows.extend(pool[:new_gold_cap])

    meta: dict[str, object] = {
        "gold_id": gold_id,
        "gold_version": gold_version,
        "ontology_cache_sha256": ontology_sha256,
        "harness_config_sha256": harness_config_sha256,
        "generated_at": generated_at or _now(),
        "suspect_cap": suspect_cap,
        "new_gold_cap": new_gold_cap,
        "sections": list(SECTIONS),
    }
    meta.update(extra_meta or {})
    return Packet(
        rows=tuple(rows),
        variants=variants,
        replay={key: dict(value) for key, value in (replay or {}).items()},
        split=split,
        counts=counts,
        overflow=overflow,
        meta=meta,
    )


# --------------------------------------------------------------------------------------
# Gold v2 — the per-cell packet (sections A-E, per-concept granular grading)
# --------------------------------------------------------------------------------------

#: The v2 sheet's sections, in the order Damien meets them.
SECTIONS_V2 = ("pairing", "consistency", "suspect", "resolution", "new_gold", "improvement")

#: Section F is a bounded pilot: enough rows to judge whether the pattern generalizes, few enough
#: that Damien can read every one of them in a sitting.
IMPROVEMENT_CAP = 40

#: The gitignored v2 packet directory.
DEFAULT_PACKET_DIR_V2 = DEFAULT_DATA_DIR / "reports" / "audit_packet_v2"

#: Per-concept verdicts the v2 sheet emits.
GOLD_VERDICTS = frozenset({"keep", "remove"})
PIPELINE_VERDICTS = frozenset({"elevate", "not_gold"})
PAIRING_CHOICES = frozenset({"heuristic", "alternative"})

#: The reading already baked into gold when a pairing packet row is built (see
#: :func:`precheck_pairing`). The fold diffs a decision against this reading, never against
#: the item's current gold wholesale, because gold v2 dedupes one item across many source-row
#: instances (KTD3 v2) -- an item's gold is the *union* of its instances' contributions.
PAIRING_APPLIED_READING = "heuristic"

#: Rulings Damien already made on the v1 sheet, carried forward into v2 pre-checked (ask
#: ``folio-resolve-2026-07-28-gold-audit-gate``, q4: "both gold concepts stand; the pipeline junk
#: tail is a precision defect, not gold"). Keyed by the *normalized input cell text*, because the
#: re-derivation gave every row a new decision id. The keys are firm surface strings, so the map
#: lives in a gitignored file (KTD1) and is empty unless that file is present.
DEFAULT_PREFILL_PATH = DEFAULT_DATA_DIR / "gold" / "prefill_rulings_v2.json"

#: Damien's worked ruling on the section-A pairing principle, quoted at the top of that section.
#: It names a practice area, i.e. a firm surface string, so it lives in a gitignored file rather
#: than as a default in this committed module (KTD1).
DEFAULT_PAIRING_NOTE_PATH = DEFAULT_DATA_DIR / "gold" / "pairing_note_v2.txt"


def load_prefill_rulings(path: Path = DEFAULT_PREFILL_PATH) -> dict[str, str]:
    """``{normalized input text: why it is pre-filled}``. Missing file = no pre-fills."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"prefill rulings file is not a JSON object: {path}")
    return {label_key(str(key)): str(value) for key, value in payload.items()}


def load_pairing_note(path: Path = DEFAULT_PAIRING_NOTE_PATH) -> str:
    """The one-line ruling quoted in section A's banner. Missing file = no quote."""
    if not path.exists():
        return ""
    return " ".join(path.read_text(encoding="utf-8").split())


@dataclass(frozen=True, slots=True)
class GoldRowV2:
    """A gold v2 row: a deduped input cell with its instances and its family id."""

    item_id: str
    firm: str
    stratum: str
    stratum_id: str
    family_id: str
    level: int
    levels: tuple[int, ...]
    leaf: str
    input_text: str
    gold_iris: tuple[str, ...]
    values: tuple[GoldValue, ...]
    flags: tuple[str, ...]
    rules: tuple[str, ...]
    blank: bool
    notes: str | None
    instances: tuple[Mapping[str, object], ...]
    payload: Mapping[str, object]

    @property
    def first_path(self) -> tuple[str, ...]:
        if not self.instances:
            return ()
        raw = self.instances[0].get("ancestor_path")
        return tuple(str(part) for part in _items(raw))


def gold_row_v2_from_json(payload: Mapping[str, object]) -> GoldRowV2:
    """Read one ``gold_v2.jsonl`` line. Shares :func:`gold_row_from_json`'s value parsing."""
    base = gold_row_from_json(payload)
    instances_raw = payload.get("instances") or []
    instances = tuple(dict(entry) for entry in _items(instances_raw) if isinstance(entry, Mapping))
    levels_raw = payload.get("levels") or []
    return GoldRowV2(
        item_id=base.item_id,
        firm=base.firm,
        stratum=base.stratum,
        stratum_id=base.stratum_id,
        family_id=str(payload.get("family_id", "")),
        level=_as_int(payload.get("level")),
        levels=tuple(_as_int(value) for value in _items(levels_raw)),
        leaf=base.leaf,
        input_text=base.input_text,
        gold_iris=base.gold_iris,
        values=base.values,
        flags=base.flags,
        rules=base.rules,
        blank=base.blank,
        notes=base.notes,
        instances=instances,
        payload=base.payload,
    )


def load_gold_rows_v2(path: Path) -> list[GoldRowV2]:
    rows: list[GoldRowV2] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(gold_row_v2_from_json(json.loads(line)))
    return rows


def _gold_block(
    row: GoldRowV2 | None,
    iris: Sequence[str],
    definitions: Mapping[str, str],
    label_lookup: Mapping[str, str] | None = None,
) -> tuple[Mapping[str, object], ...]:
    """Every gold concept as its own gradeable entry (label, IRI, definition snippet).

    ``label_lookup`` (iri -> raw label) fills in a concept ``row.values`` never recorded -- an
    IRI a fold elevated or amended onto the item that the curator workbook itself never named.
    """
    label_lookup = label_lookup or {}
    by_iri = {value.iri: value for value in row.values} if row is not None else {}
    out: list[Mapping[str, object]] = []
    for iri in sorted(iris):
        value = by_iri.get(iri)
        out.append(
            {
                "iri": iri,
                "label": value.raw if value else label_lookup.get(iri, ""),
                "column": value.column if value else "",
                "branch": value.branch if value else "",
                "definition": definition_snippet(definitions.get(iri)),
            }
        )
    return tuple(out)


def _committed_iris(
    candidates: Sequence[RankedCandidate], config: AnswerRuleConfig | None
) -> frozenset[str]:
    """The IRIs folio-resolve would actually answer with today (KTD2: probability bar, top-k)."""
    if config is None:
        return frozenset()
    return frozenset(entry.iri for entry in commit_from_ranked(candidates, config))


def _pipeline_block(
    candidates: Sequence[RankedCandidate],
    definitions: Mapping[str, str],
    *,
    shown: int = PIPELINE_CANDIDATES_SHOWN,
    gold_iris: Iterable[str] = (),
    committed: Iterable[str] = (),
) -> tuple[Mapping[str, object], ...]:
    """Every pipeline candidate as its own gradeable entry — definitions on **all** of them.

    A candidate the workbook already names as gold is marked ``already_gold`` so the sheet can say
    so: grading it "not gold" in the pipeline block must never read as removing it from gold (the
    gold block above is the only place a curated concept can be removed). ``committed`` marks the
    candidates the answer rule would actually commit, so the sheet can separate today's answer
    from the ranked tail behind it.
    """
    gold = frozenset(gold_iris)
    answered = frozenset(committed)
    return tuple(
        {
            "iri": candidate.iri,
            "label": candidate.label,
            "score": candidate.score,
            "probability": round(candidate.probability, 6),
            "rank": candidate.rank,
            "extraction_path": candidate.extraction_path,
            "definition": definition_snippet(definitions.get(candidate.iri)),
            "already_gold": candidate.iri in gold,
            "committed": candidate.iri in answered,
        }
        for candidate in candidates[:shown]
    )


def _pipeline_reference(
    candidates: Sequence[RankedCandidate],
    definitions: Mapping[str, str],
    *,
    config: AnswerRuleConfig | None,
    gold_iris: Iterable[str] = (),
    shown: int = PIPELINE_CANDIDATES_SHOWN,
) -> dict[str, object]:
    """What folio-resolve says about this input today — the read-only reference panel's data."""
    committed = _committed_iris(candidates, config)
    return {
        "candidates": [
            dict(entry)
            for entry in _pipeline_block(
                candidates, definitions, shown=shown, gold_iris=gold_iris, committed=committed
            )
        ],
        "ranked_total": len(candidates),
        "committed_total": len(committed),
        "top_k": config.top_k if config is not None else 0,
        "threshold": config.threshold if config is not None else 0.0,
    }


def _instances_json(row: GoldRowV2) -> list[Mapping[str, object]]:
    return [
        {
            "path": [str(part) for part in _items(instance.get("ancestor_path"))],
            "level": _as_int(instance.get("level")),
            "row": _as_int(instance.get("row")),
            "stratum": str(instance.get("stratum", "")),
            "gold_iris": [str(iri) for iri in _items(instance.get("gold_iris"))],
            "gold_labels_raw": [str(label) for label in _items(instance.get("gold_labels_raw"))],
        }
        for instance in row.instances
    ]


def _prefill_for(
    row: GoldRowV2,
    gold: Sequence[Mapping[str, object]],
    pipeline: Sequence[Mapping[str, object]],
    rulings: Mapping[str, str],
) -> dict[str, object]:
    """Damien's carried-forward rulings, as per-concept verdicts the sheet renders pre-checked."""
    note = rulings.get(label_key(row.input_text))
    if not note:
        return {}
    return {
        "gold": {str(entry["iri"]): "keep" for entry in gold},
        "pipeline": {
            str(entry["iri"]): "not_gold" for entry in pipeline if not entry.get("already_gold")
        },
        "note": note,
    }


def _hierarchy_key(row: GoldRowV2) -> tuple[str, tuple[str, ...], int, str]:
    """Sort key that renders a section as the taxonomy reads: group, then path, then level."""
    return (row.stratum, row.first_path, row.level, label_key(row.input_text))


# --------------------------------------------------------------------------------------
# Original-spreadsheet context: the source rows behind an adjudication
# --------------------------------------------------------------------------------------

#: The columns a curator actually reads. A SharePoint term-set export carries ~40 columns of
#: GUIDs, LCIDs and timestamps; mirroring those would bury the three that carry the mapping.
_GRID_COLUMN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^level\s*\d+\b", re.IGNORECASE),
    re.compile(r"^sali\b", re.IGNORECASE),
    re.compile(r"^additional sali\b", re.IGNORECASE),
    re.compile(r"^term set name$", re.IGNORECASE),
    re.compile(r"^term description$", re.IGNORECASE),
    re.compile(r"^term depreciated$", re.IGNORECASE),
    re.compile(r"code$", re.IGNORECASE),
)

#: How many source rows one adjudication shows before the grid says "+N more".
SOURCE_ROWS_SHOWN = 6


@dataclass(frozen=True, slots=True)
class SheetSource:
    """One derived sheet, addressable by its 1-based spreadsheet row number.

    ``rows[0]`` is spreadsheet row 2 — the header occupies row 1, exactly as
    :func:`folio_eval.gold.parse_firm1_v2` numbers it (``enumerate(rows[1:], start=2)``), so a
    ``source_rows`` / ``instances[].row`` value indexes straight back into the workbook a curator
    is looking at.
    """

    firm: str
    sheet_id: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    #: Column indices mirrored in the grid, in sheet order.
    columns: tuple[int, ...]

    def cells(self, row_number: int) -> tuple[str, ...] | None:
        """The whole data row at 1-based ``row_number``, or ``None`` when the sheet is shorter."""
        position = row_number - 2
        if position < 0 or position >= len(self.rows):
            return None
        return self.rows[position]

    def grid_headers(self) -> list[str]:
        return [self.headers[index] if index < len(self.headers) else "" for index in self.columns]

    def grid_cells(self, row_number: int) -> list[str] | None:
        row = self.cells(row_number)
        if row is None:
            return None
        return [row[index] if index < len(row) else "" for index in self.columns]


def sheet_source(firm: str, sheet_id: str, rows: Sequence[Sequence[object]]) -> SheetSource:
    """Wrap a derived sheet (header row first) as an addressable :class:`SheetSource`.

    Columns are the curator-facing ones (see :data:`_GRID_COLUMN_PATTERNS`), minus any that are
    empty in every data row of *this* sheet — a term-set export declares Level 1..10 and uses
    three. The choice is a pure function of the sheet, so two runs render the same grid.
    """
    header = tuple(str(cell) if cell is not None else "" for cell in (rows[0] if rows else ()))
    body = tuple(
        tuple(str(cell) if cell is not None else "" for cell in row) for row in list(rows)[1:]
    )
    wanted = [
        index
        for index, name in enumerate(header)
        if any(pattern.search(name.strip()) for pattern in _GRID_COLUMN_PATTERNS)
    ]
    used = [
        index for index in wanted if any(index < len(row) and row[index].strip() for row in body)
    ]
    columns = tuple(used or wanted or range(len(header)))
    return SheetSource(firm=firm, sheet_id=sheet_id, headers=header, rows=body, columns=columns)


def load_sheet_sources(
    *, data_dir: Path = DEFAULT_DATA_DIR, manifest_path: Path = DEFAULT_MANIFEST_PATH
) -> list[SheetSource]:
    """Every in-scope derived sheet, verified against the intake manifest, ready to be quoted."""
    if not manifest_path.exists():
        return []
    out: list[SheetSource] = []
    for entry in read_manifest(manifest_path):
        for sheet in entry.sheets:
            rows = load_sheet_rows(
                entry.firm,
                sheet.sheet_name_hash,
                data_dir=data_dir,
                manifest_path=manifest_path,
            )
            out.append(sheet_source(entry.firm, sheet.sheet_name_hash[:12], rows))
    return out


def _cell_keys(cells: Sequence[str]) -> set[str]:
    return {key for key in (label_key(cell) for cell in cells) if key}


def locate_source_rows(
    sources: Sequence[SheetSource],
    *,
    firm: str,
    row_numbers: Sequence[int],
    needles: Sequence[str],
    limit: int = SOURCE_ROWS_SHOWN,
) -> dict[str, object]:
    """Re-locate an adjudication's workbook rows, joining on the cell text that produced it.

    The gold rows carry row numbers, but a firm can export several sheets and a row number alone
    is ambiguous across them. The join is therefore *verified*: a candidate sheet must actually
    carry one of the adjudication's own cell strings, normalized, on that row. A row no sheet can
    confirm is reported under ``unlocated`` rather than guessed at.
    """
    keys = {key for key in (label_key(needle) for needle in needles) if key}
    ordered = sorted({int(number) for number in row_numbers if int(number) > 1})
    firm_sources = [source for source in sources if source.firm == firm]
    grids: dict[str, dict[str, object]] = {}
    unlocated: list[int] = []
    ambiguous = False
    shown = 0
    overflow = 0

    for number in ordered:
        matches = [
            source
            for source in firm_sources
            if (cells := source.cells(number)) is not None
            and (not keys or _cell_keys(cells) & keys)
        ]
        if not matches:
            unlocated.append(number)
            continue
        if len(matches) > 1:
            ambiguous = True
        if shown >= limit:
            overflow += 1
            continue
        source = matches[0]
        grid = grids.setdefault(
            source.sheet_id,
            {"sheet": source.sheet_id, "headers": source.grid_headers(), "rows": []},
        )
        rendered = grid["rows"]
        assert isinstance(rendered, list)
        rendered.append({"row": number, "cells": source.grid_cells(number)})
        shown += 1

    return {
        "grids": [grids[key] for key in sorted(grids)],
        "unlocated": unlocated,
        "more": overflow,
        "ambiguous": ambiguous,
    }


# --------------------------------------------------------------------------------------
# Section A: which pairing reading survives Damien's principle
# --------------------------------------------------------------------------------------

#: A reading that leaves one of the row's input cells with no mapping at all.
PAIRING_VIOLATION_EMPTY = "input_maps_to_nothing"

#: A reading that lands the same output concept on one input cell twice.
PAIRING_VIOLATION_DUPLICATE = "outputs_duplicate"


def _pairing_blocks(raw: object, value_iris: Mapping[str, str]) -> list[dict[str, object]]:
    """One output *cell* per block, its tags carried individually with the IRI each resolved to.

    A pipe cell (``A | B``) is one block holding two tags, and the sheet has to say so: rendering
    it as ``A, B`` reads as a single comma-containing concept name, which is exactly how the
    Islamic-finance row looked wrong to Damien. ``from_pipe`` comes off the derivation record when
    it is there and falls back to "more than one tag in one cell", which is the same thing.
    """
    out: list[dict[str, object]] = []
    for block in _items(raw):
        if not isinstance(block, Mapping):
            continue
        values = [str(value) for value in _items(block.get("values"))]
        flagged = block.get("from_pipe")
        out.append(
            {
                "column": str(block.get("column", "")),
                "values": values,
                "from_pipe": bool(flagged) if flagged is not None else len(values) > 1,
                "tags": [{"label": value, "iri": value_iris.get(value, "")} for value in values],
            }
        )
    return out


def gold_rows_by_input(rows: Sequence[GoldRowV2]) -> dict[tuple[str, str], GoldRowV2]:
    """``{(firm, normalized input text): row}`` — the only safe way to look an input cell up.

    A v2 item id is ``sha256(firm, derivation, label_key(text))`` (:func:`gold.cell_item_id`), so
    the *firm* is half the key. Keying a lookup on the text alone silently binds one firm's input
    cell to the other firm's item wherever both workbooks name the same thing: items sort
    firm1-before-firm2, so a dict comprehension keeps firm2 and every colliding firm1 pairing row
    shows firm2's gold. That is the bug Damien hit on the Islamic-finance row, where the pipe cell
    resolved to two concepts but the panel showed the one concept firm2's like-named cell carries
    (2026-07-28).
    """
    return {(row.firm, label_key(row.input_text)): row for row in rows}


def pairing_violations(reading: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Damien's principle, as a check: dis-prefer a reading that empties an input or duplicates.

    ``reading`` is one output-label list per input cell, in input order — exactly the shape
    :func:`folio_eval.gold.pair_blocks` produces for either reading.

    Duplication is judged *within* an input cell, not across the row. Damien's own worked example
    settles that: a row whose cascade-down block repeats the two concepts its per-attribute blocks
    also name reads correctly as "each input keeps its own copy" (the heuristic — the reading he
    ruled for), and reads wrongly as "one input collects all four values", which lands each
    concept on that cell twice. Two *different* input cells legitimately mapping to the same
    concept is ordinary taxonomy, not a pairing artefact.
    """
    out: list[str] = []
    if any(not list(labels) for labels in reading):
        out.append(PAIRING_VIOLATION_EMPTY)
    for labels in reading:
        keys = [label_key(str(label)) for label in labels]
        if len(keys) != len(set(keys)):
            out.append(PAIRING_VIOLATION_DUPLICATE)
            break
    return tuple(out)


def precheck_pairing(
    heuristic: Sequence[Sequence[str]], alternative: Sequence[Sequence[str]]
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """``(pre-checked choice, heuristic violations, alternative violations)``.

    The heuristic is the reading already applied to gold, so it wins every tie: a sheet Damien
    never touches must fold to no change. When *both* readings break the principle nothing is
    pre-checked and the row is badged for his eye — guessing there would be the one case where a
    pre-check could silently carry a wrong answer into gold.
    """
    heuristic_bad = pairing_violations(heuristic)
    alternative_bad = pairing_violations(alternative)
    if not heuristic_bad:
        return "heuristic", heuristic_bad, alternative_bad
    if not alternative_bad:
        return "alternative", heuristic_bad, alternative_bad
    return "", heuristic_bad, alternative_bad


def pairing_reading_matches_applied(assignments: Mapping[str, object], reading: str) -> bool:
    """Whether ``reading``'s per-item IRI sets equal the row's live ("applied") gold exactly.

    Decides, on a folded pairing row, which of Heuristic/Alternative (if either) is still what is
    actually in gold today. A custom edit (Damien substituting his own IRI set for a target) can
    match neither -- the sheet must not pretend one of the two canned readings is still in force
    when what is live is his own correction.
    """
    proposed = _pairing_reading_by_item(assignments, reading)
    applied = _pairing_reading_by_item(assignments, "applied")
    if not proposed and not applied:
        return False
    return proposed == applied


def pairing_applied_reading_name(assignments: Mapping[str, object]) -> str | None:
    """Which canned reading (if either) the row's live gold still equals, for the JS baseline.

    Heuristic is checked first: when a row's several input-cell instances collapse to the same
    item (:func:`_pairing_reading_by_item` unions per item id), both readings can coincidentally
    produce an identical per-item set, and heuristic is the one "already applied to gold" by
    construction (:data:`PAIRING_APPLIED_READING`) -- the same tie-break :func:`precheck_pairing`
    already uses, so a genuinely-tied row is never reported as "alternative" by an arbitrary sort.
    """
    for reading in (PAIRING_APPLIED_READING, "alternative"):
        if pairing_reading_matches_applied(assignments, reading):
            return reading
    return None


def _applied_baseline(row: PacketRow, record: Mapping[str, object]) -> dict[str, object]:
    """A folded row's live state, as the no-op baseline the sheet's own JS diffs against.

    ``row.gold``/``row.pipeline`` already read the *current* gold version by the time this runs
    (:func:`build_packet_v2` sources every reference and grading block from ``current_gold_rows``,
    not the pre-fold snapshot), so "every gold entry -> keep, every pipeline entry -> elevate iff
    already gold" **is** what is actually live. The sheet's ``collect()`` strips anything a
    re-submission leaves equal to this before assembling the Copy-decisions JSON, so an untouched
    folded row folds to nothing and a genuine amendment is the only entry that survives. The note
    fields ride along too, so a re-typed-but-identical note does not count as a change either.
    """
    gold = {str(entry["iri"]): "keep" for entry in row.gold}
    pipeline = {
        str(entry["iri"]): ("elevate" if entry.get("already_gold") else "not_gold")
        for entry in row.pipeline
    }
    pairing_choice: str | None = None
    if row.section == "pairing":
        assignments = row.extra.get("assignments")
        if isinstance(assignments, Mapping):
            pairing_choice = pairing_applied_reading_name(assignments)
    return {
        "gold": gold,
        "pipeline": pipeline,
        "pairing": pairing_choice,
        "note": str(record.get("note", "") or ""),
        "gold_note": str(record.get("gold_note", "") or ""),
        "pipeline_note": str(record.get("pipeline_note", "") or ""),
    }


def build_packet_v2(
    *,
    gold_rows: Sequence[GoldRowV2],
    current_gold_rows: Sequence[GoldRowV2] | None = None,
    current_gold_version: int = 0,
    current_gold_id: str = "",
    pairing_rows: Sequence[Mapping[str, object]] = (),
    inconsistent_groups: Sequence[Mapping[str, object]] = (),
    suspects: Sequence[Mapping[str, object]] = (),
    resolution_batch: Sequence[Mapping[str, object]] = (),
    cluster_rows: Sequence[Mapping[str, object]] = (),
    predictions: Mapping[str, Sequence[RankedCandidate]] | None = None,
    definitions: Mapping[str, str] | None = None,
    label_proposals: Mapping[str, Sequence[LabelProposal]] | None = None,
    value_iris: Mapping[str, str] | None = None,
    frozen_ids: Iterable[str] = (),
    rejected: Iterable[str] = (),
    eligible_strata: Iterable[str] | None = None,
    slice_by_item: Mapping[str, str] | None = None,
    split: SplitFacts | None = None,
    metrics: Mapping[str, object] | None = None,
    prefill_rulings: Mapping[str, str] | None = None,
    sheet_sources: Sequence[SheetSource] = (),
    answer_config: AnswerRuleConfig | None = None,
    pairing_note: str = "",
    ontology_sha256: str = "",
    gold_version: int = 2,
    gold_id: str = "",
    parent_gold_id: str = "",
    harness_config_sha256: str = "",
    generated_at: str | None = None,
    suspect_cap: int = SUSPECT_ROW_CAP,
    new_gold_cap: int = NEW_GOLD_CAP,
    improvements: Sequence[Mapping[str, object]] = (),
    improvement_cap: int = IMPROVEMENT_CAP,
    folded_decisions: Mapping[str, Mapping[str, object]] | None = None,
    extra_meta: Mapping[str, object] | None = None,
) -> Packet:
    """Assemble the v2 gate: six sections, every concept individually gradeable.

    ``gold_rows`` is the *base* snapshot the sheet's own questions are asked against (which rows
    exist in which section, and decision-id stability across regenerations) -- unchanged from
    before. ``current_gold_rows`` is the *live* gold state -- the latest gold version on disk,
    which already carries every decision folded so far. Every reference panel and per-concept
    grading list reads from ``current_gold_rows`` (falling back to ``gold_rows`` for an item
    ``current_gold_rows`` does not carry), so a row already folded shows what is actually in gold
    today rather than the pre-fold snapshot the packet was built from (Damien, 2026-07-28: a
    folded pairing row's reading panel kept showing the superseded pre-edit assignment). Omitting
    ``current_gold_rows`` makes current == base, exactly today's behavior.
    """
    predictions = predictions or {}
    definitions = definitions or {}
    label_proposals = label_proposals or {}
    value_iris = value_iris or {}
    slice_by_item = slice_by_item or {}
    rulings = prefill_rulings or {}
    frozen = frozenset(frozen_ids)
    memory = frozenset(rejected)
    by_id = {row.item_id: row for row in gold_rows}
    by_input = gold_rows_by_input(gold_rows)
    current_by_id = (
        {row.item_id: row for row in current_gold_rows} if current_gold_rows is not None else by_id
    )
    current_version = current_gold_version or gold_version
    current_id = current_gold_id or gold_id
    # Fallback raw label for a current-gold IRI a row's own ``values`` never recorded -- happens
    # when a decision elevates a pipeline candidate that was never a curator cell for this item.
    # Any IRI any curator anywhere typed a label for is a reasonable stand-in (KTD1-safe: labels,
    # not surfaces).
    label_lookup = {iri: raw for raw, iri in value_iris.items()}

    def current_row(item_id: str) -> GoldRowV2 | None:
        return current_by_id.get(item_id) or by_id.get(item_id)

    eligible = (
        frozenset(eligible_strata)
        if eligible_strata is not None
        else frozenset(row.stratum_id for row in gold_rows if not row.blank and row.gold_iris)
    )

    counts: dict[str, int] = {
        "pairing_rows": 0,
        "pairing_precheck_heuristic": 0,
        "pairing_precheck_alternative": 0,
        "pairing_needs_your_eye": 0,
        "consistency_groups": 0,
        "suspects_total": 0,
        "suspects_shown": 0,
        "score_driven_suspects": 0,
        "frozen_suspects_barred": 0,
        "suppressed_by_rejection_memory": 0,
        "resolution_total": 0,
        "resolution_with_candidates": 0,
        "resolution_coverage_gaps": 0,
        "new_gold_pool": 0,
        "new_gold_shown": 0,
        "prefilled_rulings": 0,
        "rows_with_source_grid": 0,
        "rows_with_unlocated_source_rows": 0,
        "pairing_inputs_unmatched": 0,
        "pairing_pipe_blocks": 0,
        "improvement_items": 0,
        "improvement_proposals": 0,
        "folded_rows_locked": 0,
    }
    overflow: dict[str, int] = {}
    rows: list[PacketRow] = []

    def source_grid(
        firm: str, row_numbers: Sequence[int], needles: Sequence[str]
    ) -> dict[str, object]:
        return locate_source_rows(
            sheet_sources, firm=firm, row_numbers=row_numbers, needles=needles
        )

    def gold_reference(row: GoldRowV2 | None) -> list[Mapping[str, object]]:
        """The current-gold reference block for whichever row is passed (base or current)."""
        if row is None:
            return []
        return [dict(entry) for entry in _gold_block(row, row.gold_iris, definitions, label_lookup)]

    def pipeline_reference(row: GoldRowV2 | None, item_id: str) -> dict[str, object]:
        ranked = list(predictions.get(item_id, ()))
        return _pipeline_reference(
            ranked,
            definitions,
            config=answer_config,
            gold_iris=row.gold_iris if row is not None else (),
        )

    # -- [A] shared-row pairing adjudications -------------------------------------------
    for entry in sorted(
        pairing_rows,
        key=lambda record: (str(record.get("stratum", "")), _as_int(record.get("row"))),
    ):
        inputs = [
            dict(value) for value in _items(entry.get("inputs")) if isinstance(value, Mapping)
        ]
        heuristic = [
            [str(text) for text in _items(block)] for block in _items(entry.get("heuristic"))
        ]
        alternative = [
            [str(text) for text in _items(block)] for block in _items(entry.get("alternative"))
        ]
        pairing_firm = str(entry.get("firm", ""))
        targets = [
            by_input.get((pairing_firm, label_key(str(value.get("text", ""))))) for value in inputs
        ]
        counts["pairing_inputs_unmatched"] += sum(1 for target in targets if target is None)
        assignments = {
            "heuristic": [
                {
                    "item_id": target.item_id if target else "",
                    "input": str(inputs[position].get("text", "")),
                    "level": _as_int(inputs[position].get("level")),
                    "labels": labels,
                    "tags": [{"label": text, "iri": value_iris.get(text, "")} for text in labels],
                    "iris": sorted({value_iris[text] for text in labels if text in value_iris}),
                }
                for position, labels in enumerate(heuristic)
                for target in (targets[position],)
            ],
            "alternative": [
                {
                    "item_id": target.item_id if target else "",
                    "input": str(inputs[position].get("text", "")),
                    "level": _as_int(inputs[position].get("level")),
                    "labels": labels,
                    "tags": [{"label": text, "iri": value_iris.get(text, "")} for text in labels],
                    "iris": sorted({value_iris[text] for text in labels if text in value_iris}),
                }
                for position, labels in enumerate(alternative)
                for target in (targets[position],)
            ],
            # The row's own contribution as it actually stands in gold right now -- not a fixed
            # candidate reading like heuristic/alternative, but whatever the live IRI set is. For
            # a never-folded row this equals the heuristic reading exactly (heuristic is baked
            # into gold at build time, KTD3 v2), so it changes nothing for a row Damien hasn't
            # touched. For a folded row it is the ground truth the "applied" panel renders, and
            # the fold's own baseline for computing an amendment's delta (never the stale
            # heuristic default, which would silently discard a prior edit).
            "applied": [
                {
                    "item_id": target.item_id if target else "",
                    "input": str(inputs[position].get("text", "")),
                    "level": _as_int(inputs[position].get("level")),
                    "labels": [entry["label"] for entry in _applied_current],
                    "tags": [
                        {"label": entry["label"], "iri": entry["iri"]} for entry in _applied_current
                    ],
                    "iris": sorted(_applied_row.gold_iris) if _applied_row else [],
                }
                for position, target in enumerate(targets)
                for _applied_row in (current_row(target.item_id) if target else None,)
                for _applied_current in (gold_reference(_applied_row),)
            ],
        }
        counts["pairing_rows"] += 1
        choice, heuristic_bad, alternative_bad = precheck_pairing(heuristic, alternative)
        if choice == "heuristic":
            counts["pairing_precheck_heuristic"] += 1
        elif choice == "alternative":
            counts["pairing_precheck_alternative"] += 1
        else:
            counts["pairing_needs_your_eye"] += 1
        firm = pairing_firm
        source_row = _as_int(entry.get("row"))
        input_texts = [str(value.get("text", "")) for value in inputs]
        blocks_json = _pairing_blocks(entry.get("blocks"), value_iris)
        counts["pairing_pipe_blocks"] += sum(1 for block in blocks_json if block["from_pipe"])
        context = [
            {
                "item_id": target.item_id if target else "",
                "level": _as_int(inputs[position].get("level")),
                "text": input_texts[position],
                "gold": gold_reference(current_row(target.item_id) if target else None),
                "pipeline": pipeline_reference(
                    current_row(target.item_id) if target else None,
                    target.item_id if target else "",
                ),
                "workbook_gold": gold_reference(target),
            }
            for position, target in enumerate(targets)
        ]
        rows.append(
            PacketRow(
                decision_id=f"pairing:{_digest(firm, str(entry.get('row', '')))}",
                section="pairing",
                item_id="",
                firm=firm,
                stratum=str(entry.get("stratum", "")),
                stratum_id="",
                ancestor_path=tuple(input_texts),
                surface_label=" + ".join(input_texts),
                input_text="",
                slice_name="",
                reason_class="pairing_ambiguous",
                suggested_action=(
                    "The row's input cells and output cells do not line up 1:1. Pick the reading: "
                    "heuristic (first output block to the first input, the rest to the deepest "
                    "input) or alternative (every output belongs to the deepest input)."
                ),
                extra={
                    "row": source_row,
                    "inputs": inputs,
                    "blocks": blocks_json,
                    "assignments": assignments,
                    "input_context": context,
                    "precheck": {
                        "choice": choice,
                        "heuristic_violations": list(heuristic_bad),
                        "alternative_violations": list(alternative_bad),
                        "needs_your_eye": not choice,
                    },
                    "source_grid": source_grid(firm, [source_row], input_texts),
                },
            )
        )

    # -- [B] duplicate-consistency adjudications ----------------------------------------
    for entry in sorted(inconsistent_groups, key=lambda record: str(record.get("input_text", ""))):
        item_id = str(entry.get("item_id", ""))
        row = by_id.get(item_id)
        if row is None:
            continue
        counts["consistency_groups"] += 1
        ranked = list(predictions.get(item_id, ()))
        current = current_row(item_id) or row
        gold_entries = _gold_block(current, current.gold_iris, definitions, label_lookup)
        pipeline_entries = _pipeline_block(
            ranked,
            definitions,
            gold_iris=current.gold_iris,
            committed=_committed_iris(ranked, answer_config),
        )
        prefill = _prefill_for(row, gold_entries, pipeline_entries, rulings)
        if prefill:
            counts["prefilled_rulings"] += 1
        rows.append(
            PacketRow(
                decision_id=f"consistency:{item_id}",
                section="consistency",
                item_id=item_id,
                firm=row.firm,
                stratum=row.stratum,
                stratum_id=row.stratum_id,
                ancestor_path=row.first_path,
                surface_label=row.input_text,
                input_text=row.input_text,
                slice_name=slice_by_item.get(item_id, ""),
                reason_class="gold_inconsistent",
                suggested_action=(
                    "The same input cell was answered differently in different places. Gold is the "
                    "union today — keep the concepts that belong to this input and remove the rest."
                ),
                gold=gold_entries,
                pipeline=pipeline_entries,
                proposed_iris=tuple(candidate.iri for candidate in ranked[:1]),
                notes_text=row.notes,
                extra={
                    "level": row.level,
                    "levels": list(row.levels),
                    "instances": _instances_json(row),
                    "union_gold_iris": [str(iri) for iri in _items(entry.get("union_gold_iris"))],
                    "hierarchy": list(_hierarchy_key(row)[1]),
                    "prefill": prefill,
                    "gold_ref": gold_reference(current),
                    "pipeline_ref": pipeline_reference(current, item_id),
                    "workbook_gold": gold_reference(row),
                    "source_grid": source_grid(
                        row.firm,
                        [_as_int(instance.get("row")) for instance in row.instances],
                        [row.input_text],
                    ),
                },
            )
        )

    # -- [C] suspects, regraded per concept ---------------------------------------------
    # An item already adjudicated in section B is not asked again here: one item, one decision,
    # so the fold can never receive two conflicting verdicts for the same gold set.
    candidates: list[PacketRow] = []
    seen_suspects: set[str] = {row.item_id for row in rows if row.section == "consistency"}
    for entry in suspects:
        item_id = str(entry.get("item_id", ""))
        row = by_id.get(item_id)
        if row is None or item_id in seen_suspects:
            continue
        seen_suspects.add(item_id)
        reasons = [str(reason) for reason in _items(entry.get("reasons"))]
        reason_class = reasons[0].split(":", 1)[0] if reasons else "gold_suspect"
        ranked = list(predictions.get(item_id, ()))
        current = current_row(item_id) or row
        gold_entries = _gold_block(current, current.gold_iris, definitions, label_lookup)
        pipeline_entries = _pipeline_block(
            ranked,
            definitions,
            gold_iris=current.gold_iris,
            committed=_committed_iris(ranked, answer_config),
        )
        prefill = _prefill_for(row, gold_entries, pipeline_entries, rulings)
        if prefill:
            counts["prefilled_rulings"] += 1
        confidence = ranked[0].probability if ranked else 0.0
        candidates.append(
            PacketRow(
                decision_id=f"suspect:{item_id}:{_digest(reason_class)}",
                section="suspect",
                item_id=item_id,
                firm=row.firm,
                stratum=row.stratum,
                stratum_id=row.stratum_id,
                ancestor_path=row.first_path,
                surface_label=row.input_text,
                input_text=row.input_text,
                slice_name=slice_by_item.get(item_id, ""),
                reason_class=reason_class,
                suggested_action=(
                    "Grade each concept on its own: keep the gold that belongs to this input cell, "
                    "remove what does not, and elevate any pipeline candidate that should be gold."
                ),
                gold=gold_entries,
                pipeline=pipeline_entries,
                proposed_iris=tuple(candidate.iri for candidate in ranked[:1]),
                notes_text=row.notes,
                confidence=confidence,
                label_frequency=len(row.instances),
                sort_score=confidence * max(len(row.instances), 1),
                extra={
                    "level": row.level,
                    "levels": list(row.levels),
                    "instances": _instances_json(row),
                    "reasons": reasons,
                    "source": "gold_builder",
                    "prefill": prefill,
                    "gold_ref": gold_reference(current),
                    "pipeline_ref": pipeline_reference(current, item_id),
                    "workbook_gold": gold_reference(row),
                    "source_grid": source_grid(
                        row.firm,
                        [_as_int(instance.get("row")) for instance in row.instances],
                        [row.input_text],
                    ),
                },
            )
        )

    for entry in _score_driven_suspects_v2(cluster_rows, by_id):
        item_id = str(entry["item_id"])
        if item_id in seen_suspects:
            continue
        if item_id in frozen:
            counts["frozen_suspects_barred"] += 1
            continue
        row = by_id.get(item_id)
        if row is None:
            continue
        seen_suspects.add(item_id)
        counts["score_driven_suspects"] += 1
        ranked = list(predictions.get(item_id, ()))
        current = current_row(item_id) or row
        gold_entries = _gold_block(current, current.gold_iris, definitions, label_lookup)
        pipeline_entries = _pipeline_block(
            ranked,
            definitions,
            gold_iris=current.gold_iris,
            committed=_committed_iris(ranked, answer_config),
        )
        prefill = _prefill_for(row, gold_entries, pipeline_entries, rulings)
        if prefill:
            counts["prefilled_rulings"] += 1
        confidence = ranked[0].probability if ranked else 0.0
        candidates.append(
            PacketRow(
                decision_id=f"suspect:{item_id}:{_digest('own_cell_synonymy')}",
                section="suspect",
                item_id=item_id,
                firm=row.firm,
                stratum=row.stratum,
                stratum_id=row.stratum_id,
                ancestor_path=row.first_path,
                surface_label=row.input_text,
                input_text=row.input_text,
                slice_name=slice_by_item.get(item_id, str(entry.get("slice", ""))),
                reason_class="own_cell_synonymy",
                suggested_action=(
                    "This cell's gold shares no word with the cell text. Keep it if the mapping is "
                    "a genuine synonym; remove it if the workbook is wrong."
                ),
                gold=gold_entries,
                pipeline=pipeline_entries,
                proposed_iris=tuple(candidate.iri for candidate in ranked[:1]),
                notes_text=row.notes,
                confidence=confidence,
                label_frequency=len(row.instances),
                sort_score=confidence * max(len(row.instances), 1),
                extra={
                    "level": row.level,
                    "levels": list(row.levels),
                    "instances": _instances_json(row),
                    "source": "cluster_triage",
                    "prefill": prefill,
                    "gold_ref": gold_reference(current),
                    "pipeline_ref": pipeline_reference(current, item_id),
                    "workbook_gold": gold_reference(row),
                    "source_grid": source_grid(
                        row.firm,
                        [_as_int(instance.get("row")) for instance in row.instances],
                        [row.input_text],
                    ),
                },
            )
        )

    counts["suspects_total"] = len(candidates)
    kept, suppressed = _suppress(candidates, memory)
    counts["suppressed_by_rejection_memory"] += suppressed
    kept.sort(key=_suspect_sort_key)
    shown, spilled = kept[:suspect_cap], kept[suspect_cap:]
    for spill in spilled:
        overflow[spill.reason_class] = overflow.get(spill.reason_class, 0) + 1
    counts["suspects_shown"] = len(shown)
    # Rendered in taxonomy order, chosen by confidence x instance count.
    shown.sort(
        key=lambda entry: (
            entry.stratum,
            entry.ancestor_path,
            entry.extra.get("level", 0),
            entry.input_text,
        )
    )
    rows.extend(shown)

    # -- [D] the R2 resolution batch ----------------------------------------------------
    seen_resolution: set[str] = set()
    by_label: dict[str, list[str]] = {}
    for entry in resolution_batch:
        key_label = str(entry.get("normalized") or entry.get("raw") or "")
        holder = by_label.setdefault(key_label, [])
        holder_id = str(entry.get("item_id", ""))
        if holder_id and holder_id not in holder:
            holder.append(holder_id)
    for entry in resolution_batch:
        normalized = str(entry.get("normalized") or entry.get("raw") or "")
        item_id = str(entry.get("item_id", ""))
        key = f"{normalized}|{entry.get('reason', '')}"
        if key in seen_resolution:
            continue
        seen_resolution.add(key)
        counts["resolution_total"] += 1
        proposals = tuple(label_proposals.get(normalized, ()))
        reason_class = str(entry.get("reason", "unresolved"))
        if proposals:
            counts["resolution_with_candidates"] += 1
        else:
            counts["resolution_coverage_gaps"] += 1
            reason_class = "folio_coverage_gap"
        gold_row = by_id.get(item_id)
        rows.append(
            PacketRow(
                decision_id=f"resolution:{_digest(normalized, str(entry.get('reason', '')))}",
                section="resolution",
                item_id=item_id,
                firm=str(entry.get("firm", "")),
                stratum=str(entry.get("stratum", "")),
                stratum_id=gold_row.stratum_id if gold_row else "",
                ancestor_path=tuple(str(part) for part in _items(entry.get("ancestor_path"))),
                surface_label=normalized,
                input_text=gold_row.input_text if gold_row else str(entry.get("leaf", "")),
                slice_name=slice_by_item.get(item_id, ""),
                reason_class=reason_class,
                suggested_action=(
                    "Elevate the FOLIO concept this gold label meant; leave every proposal at "
                    "'not gold' when FOLIO has no concept for it (recorded as a coverage gap)."
                ),
                pipeline=tuple(
                    {
                        "iri": proposal.iri,
                        "label": proposal.label,
                        "score": proposal.score,
                        "probability": round(proposal.score / 100.0, 6),
                        "rank": position + 1,
                        "extraction_path": proposal.method,
                        "definition": definition_snippet(definitions.get(proposal.iri)),
                    }
                    for position, proposal in enumerate(proposals)
                ),
                proposed_iris=tuple(proposal.iri for proposal in proposals[:1]),
                confidence=proposals[0].score / 100 if proposals else 0.0,
                extra={
                    "raw": str(entry.get("raw", "")),
                    "column": str(entry.get("column", "")),
                    "parse_branch": str(entry.get("parse_branch", "")),
                    "occurrences": len(by_label.get(normalized, ())),
                    "item_ids": list(by_label.get(normalized, ())),
                    "level": gold_row.level if gold_row else 0,
                    "gold_ref": gold_reference(current_row(item_id) if item_id else None),
                    "pipeline_ref": pipeline_reference(
                        current_row(item_id) if item_id else None, item_id
                    ),
                    "workbook_gold": gold_reference(gold_row),
                    "source_grid": source_grid(
                        gold_row.firm if gold_row else str(entry.get("firm", "")),
                        [_as_int(instance.get("row")) for instance in gold_row.instances]
                        if gold_row
                        else [],
                        [gold_row.input_text] if gold_row else [],
                    ),
                },
            )
        )

    # -- [E] new-gold candidates (KTD5) -------------------------------------------------
    pool: list[PacketRow] = []
    for row in gold_rows:
        if not row.blank or row.stratum_id not in eligible:
            continue
        ranked = list(predictions.get(row.item_id, ()))
        if not ranked:
            continue
        counts["new_gold_pool"] += 1
        exact_label = label_key(row.input_text) == label_key(ranked[0].label)
        current = current_row(row.item_id) or row
        pool.append(
            PacketRow(
                decision_id=f"new_gold:{row.item_id}",
                section="new_gold",
                item_id=row.item_id,
                firm=row.firm,
                stratum=row.stratum,
                stratum_id=row.stratum_id,
                ancestor_path=row.first_path,
                surface_label=row.input_text,
                input_text=row.input_text,
                slice_name=slice_by_item.get(row.item_id, ""),
                reason_class="new_gold_candidate",
                suggested_action=(
                    "This input cell has no curated mapping. Elevate any candidate that should be "
                    "gold (tagged provenance=pipeline_suggested); leave the rest at 'not gold'."
                ),
                pipeline=_pipeline_block(
                    ranked,
                    definitions,
                    gold_iris=current.gold_iris,
                    committed=_committed_iris(ranked, answer_config),
                ),
                proposed_iris=tuple(candidate.iri for candidate in ranked[:1]),
                notes_text=row.notes,
                confidence=ranked[0].probability,
                label_frequency=len(row.instances),
                sort_score=ranked[0].probability,
                extra={
                    "level": row.level,
                    "levels": list(row.levels),
                    "instances": _instances_json(row),
                    "source": "blank_cell",
                    "exact_label_match": exact_label,
                    "top_score": ranked[0].score,
                    "candidates_ranked": len(ranked),
                    "gold_ref": gold_reference(current),
                    "pipeline_ref": pipeline_reference(current, row.item_id),
                    "workbook_gold": gold_reference(row),
                    "source_grid": source_grid(
                        row.firm,
                        [_as_int(instance.get("row")) for instance in row.instances],
                        [row.input_text],
                    ),
                },
            )
        )
    pool, suppressed_new = _suppress(pool, memory)
    counts["suppressed_by_rejection_memory"] += suppressed_new
    pool.sort(key=_new_gold_sort_key)
    counts["new_gold_shown"] = min(len(pool), new_gold_cap)
    chosen_new = pool[:new_gold_cap]
    chosen_new.sort(key=lambda entry: (entry.stratum, entry.ancestor_path, entry.input_text))
    rows.extend(chosen_new)

    # -- [F] proposed gold improvements (pilot, machine-proposed) ------------------------
    for entry in list(improvements)[:improvement_cap]:
        item_id = str(entry.get("item_id", ""))
        row = by_id.get(item_id)
        atom_proposals = [
            dict(proposal)
            for proposal in _items(entry.get("proposals"))
            if isinstance(proposal, Mapping)
        ]
        if row is None or not atom_proposals:
            continue
        counts["improvement_items"] += 1
        counts["improvement_proposals"] += len(atom_proposals)
        current = current_row(item_id) or row
        current_iris = frozenset(current.gold_iris)
        rows.append(
            PacketRow(
                decision_id=f"improvement:{item_id}",
                section="improvement",
                item_id=item_id,
                firm=row.firm,
                stratum=row.stratum,
                stratum_id=row.stratum_id,
                ancestor_path=row.first_path,
                surface_label=row.input_text,
                input_text=row.input_text,
                slice_name=slice_by_item.get(item_id, ""),
                reason_class="proposed_improvement",
                suggested_action=(
                    "Machine-proposed, from your own six corrections: this cell names atoms its "
                    "gold does not carry yet. Accept the ones that belong to this cell as gold; "
                    "reject the rest. Nothing here is gold until you accept it."
                ),
                gold=_gold_block(current, current.gold_iris, definitions, label_lookup),
                pipeline=tuple(
                    {
                        "iri": str(proposal.get("iri", "")),
                        "label": str(proposal.get("label", "")),
                        "score": proposal.get("score", 0.0),
                        "rank": position + 1,
                        "extraction_path": (
                            f"{proposal.get('method', '')}:{proposal.get('branch', '')}"
                        ),
                        "definition": definition_snippet(
                            str(proposal.get("definition", ""))
                            or definitions.get(str(proposal.get("iri", "")))
                        ),
                        "already_gold": str(proposal.get("iri", "")) in current_iris,
                    }
                    for position, proposal in enumerate(atom_proposals)
                ),
                proposed_iris=tuple(str(proposal.get("iri", "")) for proposal in atom_proposals),
                notes_text=row.notes,
                label_frequency=len(row.instances),
                extra={
                    "level": row.level,
                    "levels": list(row.levels),
                    "instances": _instances_json(row),
                    "machine_proposed": True,
                    "proposals": atom_proposals,
                    "gold_ref": gold_reference(current),
                    "pipeline_ref": pipeline_reference(current, item_id),
                    "workbook_gold": gold_reference(row),
                    "source_grid": source_grid(
                        row.firm,
                        [_as_int(instance.get("row")) for instance in row.instances],
                        [row.input_text],
                    ),
                },
            )
        )

    # -- decisions already folded into a later gold version render pre-filled, not blank --------
    # Every input on these rows stays enabled (Damien, 2026-07-28: "let me add notes and change
    # items even where you think things are settled") -- ``folded`` marks the row as decided and
    # supplies the baseline the sheet's own JS diffs a re-submission against, so an untouched row
    # still folds to nothing and only a genuine amendment reaches the Copy-decisions JSON.
    folded = {str(key): dict(value) for key, value in (folded_decisions or {}).items()}
    if folded:
        applied: list[PacketRow] = []
        for packet_row in rows:
            record = folded.get(packet_row.decision_id)
            if record is None:
                applied.append(packet_row)
                continue
            counts["folded_rows_locked"] += 1
            extra = dict(packet_row.extra)
            extra["folded"] = record
            extra["baseline"] = _applied_baseline(packet_row, record)
            applied.append(replace(packet_row, extra=extra))
        rows = applied

    # Counted over what actually renders, not over the candidate pools the caps threw away.
    for packet_row in rows:
        grid = packet_row.extra.get("source_grid")
        if not isinstance(grid, Mapping):
            continue
        if grid.get("grids"):
            counts["rows_with_source_grid"] += 1
        if grid.get("unlocated"):
            counts["rows_with_unlocated_source_rows"] += 1

    meta: dict[str, object] = {
        "gold_id": gold_id,
        "gold_version": gold_version,
        "current_gold_id": current_id,
        "current_gold_version": current_version,
        "parent_gold_id": parent_gold_id,
        "derivation": "per_cell_v2",
        "ontology_cache_sha256": ontology_sha256,
        "harness_config_sha256": harness_config_sha256,
        "generated_at": generated_at or _now(),
        "suspect_cap": suspect_cap,
        "new_gold_cap": new_gold_cap,
        "improvement_cap": improvement_cap,
        "sections": list(SECTIONS_V2),
        "metrics": dict(metrics or {}),
        "split": split.to_json() if split else {},
        # Damien's own worked ruling on the pairing principle. Firm-surface text, so it lives in a
        # gitignored file and rides the (gitignored) packet — never a committed default (KTD1).
        "pairing_note": pairing_note,
        "answer_rule": {
            "top_k": answer_config.top_k if answer_config else 0,
            "threshold": answer_config.threshold if answer_config else 0.0,
            "calibrated": bool(answer_config.calibrated) if answer_config else False,
        },
    }
    meta.update(extra_meta or {})
    return Packet(
        rows=tuple(rows),
        variants=(),
        replay={},
        split=split,
        counts=counts,
        overflow=overflow,
        meta=meta,
    )


def _score_driven_suspects_v2(
    cluster_rows: Sequence[Mapping[str, object]], by_id: Mapping[str, GoldRowV2]
) -> list[dict[str, object]]:
    """v2 has no inherited gold, so every zero-overlap synonymy miss is an own-cell suspect."""
    grouped: dict[str, dict[str, object]] = {}
    for entry in cluster_rows:
        if str(entry.get("kind", "")) != "fn" or str(entry.get("cluster", "")) != "synonymy":
            continue
        signals = entry.get("signals")
        jaccard = 0.0
        if isinstance(signals, Mapping):
            jaccard = float(signals.get("max_token_jaccard", 0.0) or 0.0)
        if jaccard > 0.0:
            continue
        item_id = str(entry.get("item_id", ""))
        if item_id not in by_id:
            continue
        gold_iri = str(entry.get("gold_iri", ""))
        bucket = grouped.setdefault(
            item_id,
            {"item_id": item_id, "gold_iris": [], "slice": str(entry.get("slice", ""))},
        )
        iris = bucket["gold_iris"]
        assert isinstance(iris, list)
        if gold_iri and gold_iri not in iris:
            iris.append(gold_iri)
    return [grouped[item_id] for item_id in sorted(grouped)]


def _suspect_action(reason_class: str, proposed: Sequence[str]) -> str:
    if not proposed:
        return (
            "No pipeline candidate to compare — Accept records the gold as reviewed, Edit to "
            "paste the right IRIs."
        )
    return (
        "Accept = replace this item's gold with the pipeline's top candidate. Reject = the "
        "workbook is right (remembered; this exact proposal never resurfaces). Edit = paste the "
        f"IRI set you want ({reason_class})."
    )


def _score_driven_suspects(
    cluster_rows: Sequence[Mapping[str, object]], by_id: Mapping[str, GoldRow]
) -> list[dict[str, object]]:
    """Own-origin synonymy misses: leaf and gold concept share no token, yet gold says they map.

    U4's headline gold-suspect signal. Grouped per item so one item yields one row however many
    of its gold IRIs missed.
    """
    grouped: dict[str, dict[str, object]] = {}
    for entry in cluster_rows:
        if str(entry.get("kind", "")) != "fn" or str(entry.get("cluster", "")) != "synonymy":
            continue
        signals = entry.get("signals")
        jaccard = 0.0
        if isinstance(signals, Mapping):
            jaccard = float(signals.get("max_token_jaccard", 0.0) or 0.0)
        if jaccard > 0.0:
            continue
        item_id = str(entry.get("item_id", ""))
        gold_iri = str(entry.get("gold_iri", ""))
        row = by_id.get(item_id)
        if row is None:
            continue
        origins = {value.origin for value in row.values if value.iri == gold_iri}
        if not origins or origins & INHERITED_ORIGINS:
            continue
        bucket = grouped.setdefault(
            item_id,
            {"item_id": item_id, "gold_iris": [], "slice": str(entry.get("slice", ""))},
        )
        iris = bucket["gold_iris"]
        assert isinstance(iris, list)
        if gold_iri not in iris:
            iris.append(gold_iri)
    return [grouped[item_id] for item_id in sorted(grouped)]


def _suppress(rows: Sequence[PacketRow], memory: frozenset[str]) -> tuple[list[PacketRow], int]:
    if not memory:
        return list(rows), 0
    kept = [
        row
        for row in rows
        if not row.proposed_iris or rejection_key(row.item_id, row.proposed_iris) not in memory
    ]
    return kept, len(rows) - len(kept)


def _new_gold_sort_key(row: PacketRow) -> tuple[float, int, float, str]:
    """KTD5 ranks by score, but the fitted calibration saturates: every top-score candidate lands
    on the same probability step, so ranking by probability alone degenerates to item-id order.
    An exact normalized label match is the tie-break that spends the 25 slots on proposals a
    curator can ratify at a glance, rather than on acronyms colliding with court names.
    """
    exact = 1 if row.extra.get("exact_label_match") else 0
    top_score = _as_float(row.extra.get("top_score"))
    return (-row.sort_score, -exact, -top_score, row.item_id)


def _suspect_sort_key(row: PacketRow) -> tuple[int, float, str]:
    """KTD6: SALI-NOTES rows lead. KTD9: then confidence x label frequency, descending."""
    lead = 0 if row.reason_class == "sali_notes_flagged" else 1
    return (lead, -row.sort_score, row.decision_id)


# --------------------------------------------------------------------------------------
# The fold: gold v2 (R3)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Gold v2 as rows + manifest + the decision records the committed log will carry.

    ``notes`` holds the free text Damien typed on the sheet, keyed by decision id. It is
    deliberately *not* part of :class:`DecisionRecord`: the decision log is committed and
    leak-scanned (KTD1), and a note is the one field on the sheet that can contain anything he
    feels like writing. :func:`write_decision_notes` puts it in the gitignored gold directory.
    """

    rows: tuple[Mapping[str, object], ...]
    manifest: Mapping[str, object]
    records: tuple[DecisionRecord, ...]
    counts: Mapping[str, int]
    gold_text: str
    notes: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


def _resolution_targets(row: PacketRow) -> tuple[str, ...]:
    """Every item whose unresolved cell carried this label (the batch groups by label, not item)."""
    targets = _items(row.extra.get("item_ids"))
    return (
        tuple(str(item) for item in targets) if targets else ((row.item_id,) if row.item_id else ())
    )


def _decision_iris(decision: Mapping[str, object], row: PacketRow) -> tuple[str, ...]:
    action = str(decision.get("action", ""))
    if action == "edit":
        edited = decision.get("edited_iris") or []
        if not isinstance(edited, (list, tuple)):
            raise ValueError(f"decision {row.decision_id}: edited_iris must be a list")
        return tuple(sorted({str(iri) for iri in edited}))
    return tuple(sorted(row.proposed_iris))


def fold_decisions(
    gold_rows: Sequence[GoldRow],
    decisions: Mapping[str, Mapping[str, object]],
    *,
    packet: Packet,
    ontology_sha256: str,
    now: str | None = None,
    parent_gold_id: str | None = None,
) -> FoldResult:
    """Apply Accept/Reject/Edit to gold and emit v2 + manifest + decision records (R3, KTD5)."""
    stamp = now or _now()
    rows_by_decision = {row.decision_id: row for row in packet.rows}
    parent = parent_gold_id or str(packet.meta.get("gold_id", ""))
    base_version = _as_version(packet.meta.get("gold_version", 1))
    next_version = base_version + 1

    replacements: dict[str, tuple[tuple[str, ...], str]] = {}
    additions: dict[str, set[str]] = {}
    records: list[DecisionRecord] = []
    counts: dict[str, int] = {
        "accepted": 0,
        "rejected": 0,
        "edited": 0,
        "policy_decisions": 0,
        "carried_forward": 0,
        "changed_items": 0,
    }

    for decision_id in sorted(decisions):
        decision = decisions[decision_id]
        row = rows_by_decision.get(decision_id)
        if row is None:
            raise KeyError(f"no packet row for decision id {decision_id!r}")
        action = str(decision.get("action", ""))
        if action not in DECISION_ACTIONS:
            raise ValueError(
                f"unknown action {action!r} for {decision_id!r} (expected one of "
                f"{sorted(DECISION_ACTIONS)})"
            )
        resulting = _decision_iris(decision, row) if action != "reject" else ()
        counts[{"accept": "accepted", "reject": "rejected", "edit": "edited"}[action]] += 1

        if row.section in ("cascade", "split"):
            counts["policy_decisions"] += 1
        elif action in ("accept", "edit") and resulting:
            if row.section == "resolution":
                # A resolved label is one cell among the item's several: it is *added* to every
                # item whose unresolved cell carried that label, never a replacement of the set.
                for touched in _resolution_targets(row):
                    additions.setdefault(touched, set()).update(resulting)
            elif row.item_id:
                provenance = (
                    PROVENANCE_PIPELINE
                    if action == "accept" and row.section == "new_gold"
                    else PROVENANCE_CORRECTED
                )
                replacements[row.item_id] = (resulting, provenance)

        records.append(
            DecisionRecord(
                decision_id=decision_id,
                item_id=row.item_id,
                section=row.section,
                action=action,
                reason_class=row.reason_class,
                gold_version=base_version,
                ontology_sha256=ontology_sha256,
                proposed_iris=tuple(sorted(row.proposed_iris)),
                resulting_iris=resulting,
                recorded_at=stamp,
            )
        )

    out_rows: list[Mapping[str, object]] = []
    provenance_counts: dict[str, int] = {}
    sensitivity = 0
    for gold in gold_rows:
        payload = dict(gold.payload)
        payload["gold_version"] = next_version
        replaced = replacements.get(gold.item_id)
        added = additions.get(gold.item_id)
        if replaced is None and not added:
            provenance = gold.provenance
            counts["carried_forward"] += 1
        else:
            base = set(replaced[0]) if replaced is not None else set(gold.gold_iris)
            provenance = replaced[1] if replaced is not None else PROVENANCE_CORRECTED
            iris = tuple(sorted(base | (added or set())))
            payload["gold_iris"] = list(iris)
            payload["blank"] = not iris
            payload["provenance"] = provenance
            flags = [str(flag) for flag in _items(payload.get("flags"))]
            if provenance == PROVENANCE_PIPELINE and PROVENANCE_PIPELINE not in flags:
                flags.append(PROVENANCE_PIPELINE)
            payload["flags"] = flags
            counts["changed_items"] += 1
        payload["provenance"] = provenance
        provenance_counts[provenance] = provenance_counts.get(provenance, 0) + 1
        if provenance == PROVENANCE_PIPELINE:
            sensitivity += 1
        out_rows.append(payload)

    text = "".join(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n" for payload in out_rows
    )
    content_sha256 = sha256_text(text)
    scored = [payload for payload in out_rows if payload.get("gold_iris")]
    manifest: dict[str, object] = {
        "gold_version": next_version,
        "gold_id": f"v{next_version}-{content_sha256[:12]}",
        "parent_gold_id": parent,
        "content_sha256": content_sha256,
        "ontology_cache_sha256": ontology_sha256,
        "generated_at": stamp,
        "items_total": len(out_rows),
        "items_scored": len(scored),
        "items_blank": len(out_rows) - len(scored),
        "gold_iris_total": sum(_iri_count(payload) for payload in scored),
        "provenance_counts": dict(sorted(provenance_counts.items())),
        "decision_counts": dict(sorted(counts.items())),
        "sensitivity_excluded_items": sensitivity,
    }
    return FoldResult(
        rows=tuple(out_rows),
        manifest=manifest,
        records=tuple(records),
        counts=counts,
        gold_text=text,
    )


# --------------------------------------------------------------------------------------
# The granular fold: gold v3 from per-concept verdicts (KTD6 v2 sheet)
# --------------------------------------------------------------------------------------


#: Free-text fields the v2 sheet emits, in the order they read on the page. ``note`` is the
#: per-decision note that now sits under *every* decision unit, including pairing adjudications
#: and consistency groups, which had nowhere to write one before (Damien, 2026-07-28).
DECISION_NOTE_KEYS = ("note", "gold_note", "pipeline_note")


def _notes(decision: Mapping[str, object], decision_id: str) -> dict[str, str]:
    """The note fields on one decision. Anything non-string is a malformed decisions file."""
    out: dict[str, str] = {}
    for key in DECISION_NOTE_KEYS:
        raw = decision.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise ValueError(
                f"decision {decision_id}: {key} must be a string, got {type(raw).__name__}"
            )
        text = raw.strip()
        if text:
            out[key] = text
    return out


def _verdicts(decision: Mapping[str, object], key: str, allowed: frozenset[str]) -> dict[str, str]:
    raw = decision.get(key) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{key} must be an object of {{iri: verdict}}, got {type(raw).__name__}")
    out: dict[str, str] = {}
    for iri, verdict in raw.items():
        text = str(verdict)
        if text not in allowed:
            raise ValueError(f"unknown {key} verdict {text!r} (expected one of {sorted(allowed)})")
        out[str(iri)] = text
    return out


def _pairing_reading_by_item(
    assignments: Mapping[str, object], reading: str
) -> dict[str, set[str]]:
    """One pairing row's per-item IRI contribution under one reading (heuristic|alternative).

    A single pairing packet row can name more than one target item id (its input cells); a single
    item id can also receive contributions from more than one pairing packet row, when the same
    input text recurs across source-row instances that each needed their own adjudication (gold v2
    dedup, KTD3 v2). The fold must diff *this row's own* contribution between readings, never
    replace the item's whole gold set, or it silently drops what the item's other instances
    contributed.
    """
    out: dict[str, set[str]] = {}
    raw = assignments.get(reading) if isinstance(assignments, Mapping) else None
    for entry in _items(raw):
        if not isinstance(entry, Mapping):
            continue
        target = str(entry.get("item_id", ""))
        if not target:
            continue
        out.setdefault(target, set()).update(str(iri) for iri in _items(entry.get("iris")))
    return out


def _pairing_edits(
    decision: Mapping[str, object], decision_id: str, targets: Iterable[str]
) -> dict[str, set[str]]:
    """``edited_iris`` on a pairing decision: the corrected gold set *per input cell*.

    Damien's rulings do more than pick a reading — they rewrite what an input cell means ("a child
    cell never implies its parent": *Borrower* is Borrower, not Finance and Lending Law). That is
    the existing ``edit`` action, keyed to the items the row names, and it stays row-scoped: the
    edit replaces **this row's own contribution** to the item, exactly as a reading swap does, so
    the item keeps whatever its other source-row instances contributed (KTD3 v2 dedup).
    """
    raw = decision.get("edited_iris")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"decision {decision_id}: edited_iris must be an object of {{item_id: [iri, ...]}}, "
            f"got {type(raw).__name__}"
        )
    known = set(targets)
    out: dict[str, set[str]] = {}
    for item_id, iris in raw.items():
        key = str(item_id)
        if key not in known:
            raise ValueError(
                f"decision {decision_id}: edited_iris names item {key!r}, which is not one of "
                f"this row's input cells ({sorted(known)})"
            )
        if not isinstance(iris, (list, tuple)):
            raise ValueError(f"decision {decision_id}: edited_iris[{key}] must be a list of IRIs")
        out[key] = {str(iri) for iri in iris}
    return out


def fold_granular_decisions(
    gold_rows: Sequence[GoldRowV2],
    decisions: Mapping[str, Mapping[str, object]],
    *,
    packet: Packet,
    ontology_sha256: str,
    now: str | None = None,
    parent_gold_id: str | None = None,
    base_gold_version: int | None = None,
) -> FoldResult:
    """Apply the v2 sheet's per-concept verdicts and emit gold v3 + manifest + decision records.

    Each decision is ``{gold: {iri: keep|remove}, pipeline: {iri: elevate|not_gold},
    pairing?: heuristic|alternative, note?, gold_note?, pipeline_note?}``. A gold IRI the sheet did
    not mention is **kept** — silence never deletes curated gold. A ``not_gold`` verdict becomes a
    rejection record so the same proposal is suppressed on the next triage (KTD9). Notes ride out
    on :attr:`FoldResult.notes`, never on the committed decision log.
    """
    stamp = now or _now()
    rows_by_decision = {row.decision_id: row for row in packet.rows}
    parent = parent_gold_id or str(packet.meta.get("gold_id", ""))
    base_version = (
        _as_version(base_gold_version)
        if base_gold_version is not None
        else _as_version(packet.meta.get("gold_version", 2))
    )
    next_version = base_version + 1

    replacements: dict[str, tuple[tuple[str, ...], str]] = {}
    additions: dict[str, set[str]] = {}
    removals: dict[str, set[str]] = {}
    records: list[DecisionRecord] = []
    counts: dict[str, int] = {
        "accepted": 0,
        "rejected": 0,
        "edited": 0,
        "policy_decisions": 0,
        "carried_forward": 0,
        "changed_items": 0,
        "gold_kept": 0,
        "gold_removed": 0,
        "pipeline_elevated": 0,
        "pipeline_rejected": 0,
        "pairing_alternative": 0,
        "pairing_gold_edited": 0,
        "notes_recorded": 0,
    }
    notes: dict[str, Mapping[str, str]] = {}

    for decision_id in sorted(decisions):
        decision = decisions[decision_id]
        row = rows_by_decision.get(decision_id)
        if row is None:
            raise KeyError(f"no packet row for decision id {decision_id!r}")
        written = _notes(decision, decision_id)
        if written:
            notes[decision_id] = written
            counts["notes_recorded"] += 1
        gold_verdicts = _verdicts(decision, "gold", GOLD_VERDICTS)
        pipeline_verdicts = _verdicts(decision, "pipeline", PIPELINE_VERDICTS)
        counts["gold_removed"] += sum(1 for v in gold_verdicts.values() if v == "remove")
        counts["gold_kept"] += sum(1 for v in gold_verdicts.values() if v == "keep")
        counts["pipeline_elevated"] += sum(1 for v in pipeline_verdicts.values() if v == "elevate")
        counts["pipeline_rejected"] += sum(1 for v in pipeline_verdicts.values() if v == "not_gold")
        elevated = {iri for iri, verdict in pipeline_verdicts.items() if verdict == "elevate"}
        offered = tuple(sorted(str(entry["iri"]) for entry in row.pipeline))
        resulting: tuple[str, ...] = ()
        action = "accept"

        if row.section == "pairing":
            assignments = row.extra.get("assignments")
            assignments_map = assignments if isinstance(assignments, Mapping) else {}
            # Only a row build_packet_v2 itself marked "folded" gets the live-gold ("applied")
            # baseline -- the correct fold baseline for an amendment to a row already decided. A
            # never-folded row keeps the static heuristic reading as its baseline, even when it
            # shares a target item with a *different*, already-folded pairing row (gold v2 dedupes
            # one item across many source-row instances, KTD3 v2): the item's *total* current gold
            # can differ from this row's own untouched heuristic contribution once a sibling row's
            # edit has trimmed it, and blaming that on this row would silently "change" gold Damien
            # never asked to touch (measured: 12 items, real packet, 2026-07-28).
            applied_by_item = (
                _pairing_reading_by_item(assignments_map, "applied")
                if row.extra.get("folded")
                else {}
            ) or _pairing_reading_by_item(assignments_map, PAIRING_APPLIED_READING)
            choice_raw = decision.get("pairing")
            if choice_raw is None:
                # No reading re-pick requested: start from what is already live, so a note-only or
                # edited_iris-only amendment never silently reverts a prior edit back to heuristic.
                choice = ""
                chosen_by_item = {target: set(iris) for target, iris in applied_by_item.items()}
            else:
                choice = str(choice_raw)
                if choice not in PAIRING_CHOICES:
                    raise ValueError(
                        f"decision {decision_id}: pairing must be one of {sorted(PAIRING_CHOICES)}"
                    )
                chosen_by_item = _pairing_reading_by_item(assignments_map, choice)
            counts["policy_decisions"] += 1
            if choice == "alternative":
                counts["pairing_alternative"] += 1
            edits = _pairing_edits(
                decision, decision_id, set(applied_by_item) | set(chosen_by_item)
            )
            for target, edited in edits.items():
                chosen_by_item[target] = set(edited)
                counts["pairing_gold_edited"] += 1
            resulting = tuple(sorted({iri for iris in chosen_by_item.values() for iri in iris}))
            # Re-assign only THIS row's own contribution to each target: subtract what it
            # contributed under the previously-applied reading, add what it contributes under the
            # chosen one. Every other instance's contribution to the same item is untouched --
            # gold v2 dedupes an item across many source rows, so its gold is the union of all of
            # them (KTD3 v2), not just this one row's reading.
            for target in sorted(set(applied_by_item) | set(chosen_by_item)):
                prev_iris = applied_by_item.get(target, set())
                new_iris = chosen_by_item.get(target, set())
                if new_iris == prev_iris:
                    continue  # this row's contribution to this item is unchanged
                action = "edit"
                to_remove = prev_iris - new_iris
                to_add = new_iris - prev_iris
                if to_remove:
                    removals.setdefault(target, set()).update(to_remove)
                if to_add:
                    additions.setdefault(target, set()).update(to_add)
        elif row.section == "resolution":
            if elevated:
                action = "edit"
                for target in _resolution_targets(row):
                    additions.setdefault(target, set()).update(elevated)
            else:
                action = "reject"
            resulting = tuple(sorted(elevated))
        else:
            current = {str(entry["iri"]) for entry in row.gold}
            kept = {iri for iri in current if gold_verdicts.get(iri, "keep") == "keep"}
            resulting = tuple(sorted(kept | elevated))
            if set(resulting) != current:
                action = "edit"
                provenance = (
                    PROVENANCE_PIPELINE if not current and elevated else PROVENANCE_CORRECTED
                )
                if row.item_id:
                    replacements[row.item_id] = (resulting, provenance)

        counts[{"accept": "accepted", "reject": "rejected", "edit": "edited"}[action]] += 1
        records.append(
            DecisionRecord(
                decision_id=decision_id,
                item_id=row.item_id,
                section=row.section,
                action=action,
                reason_class=row.reason_class,
                gold_version=base_version,
                ontology_sha256=ontology_sha256,
                proposed_iris=offered,
                resulting_iris=resulting,
                recorded_at=stamp,
            )
        )
        # Per-candidate rejection memory: a concept graded 'not gold' never resurfaces unchanged.
        for iri in sorted(
            iri for iri, verdict in pipeline_verdicts.items() if verdict == "not_gold"
        ):
            records.append(
                DecisionRecord(
                    decision_id=f"{decision_id}#not_gold:{iri.rsplit('/', 1)[-1]}",
                    item_id=row.item_id,
                    section=row.section,
                    action="reject",
                    reason_class=row.reason_class,
                    gold_version=base_version,
                    ontology_sha256=ontology_sha256,
                    proposed_iris=(iri,),
                    resulting_iris=(),
                    recorded_at=stamp,
                )
            )

    out_rows: list[Mapping[str, object]] = []
    provenance_counts: dict[str, int] = {}
    sensitivity = 0
    for gold in gold_rows:
        payload = dict(gold.payload)
        payload["gold_version"] = next_version
        replaced = replacements.get(gold.item_id)
        added = additions.get(gold.item_id)
        removed = removals.get(gold.item_id)
        if replaced is None and not added and not removed:
            provenance = str(payload.get("provenance", PROVENANCE_CURATOR))
            counts["carried_forward"] += 1
        else:
            base = set(replaced[0]) if replaced is not None else set(gold.gold_iris)
            provenance = replaced[1] if replaced is not None else PROVENANCE_CORRECTED
            iris = tuple(sorted((base - (removed or set())) | (added or set())))
            payload["gold_iris"] = list(iris)
            payload["blank"] = not iris
            flags = [str(flag) for flag in _items(payload.get("flags"))]
            if provenance == PROVENANCE_PIPELINE and PROVENANCE_PIPELINE not in flags:
                flags.append(PROVENANCE_PIPELINE)
            payload["flags"] = flags
            counts["changed_items"] += 1
        payload["provenance"] = provenance
        provenance_counts[provenance] = provenance_counts.get(provenance, 0) + 1
        if provenance == PROVENANCE_PIPELINE:
            sensitivity += 1
        out_rows.append(payload)

    text = "".join(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n" for payload in out_rows
    )
    content_sha256 = sha256_text(text)
    scored = [payload for payload in out_rows if payload.get("gold_iris")]
    manifest: dict[str, object] = {
        "gold_version": next_version,
        "gold_id": f"v{next_version}-{content_sha256[:12]}",
        "parent_gold_id": parent,
        "derivation": str(packet.meta.get("derivation", "per_cell_v2")),
        "content_sha256": content_sha256,
        "ontology_cache_sha256": ontology_sha256,
        "generated_at": stamp,
        "items_total": len(out_rows),
        "items_scored": len(scored),
        "items_blank": len(out_rows) - len(scored),
        "gold_iris_total": sum(_iri_count(payload) for payload in scored),
        "provenance_counts": dict(sorted(provenance_counts.items())),
        "decision_counts": dict(sorted(counts.items())),
        "sensitivity_excluded_items": sensitivity,
    }
    return FoldResult(
        rows=tuple(out_rows),
        manifest=manifest,
        records=tuple(records),
        counts=counts,
        gold_text=text,
        notes=notes,
    )


def write_decision_notes(result: FoldResult, out_dir: Path) -> Path | None:
    """Park the sheet's free-text notes beside the gold they explain. Gitignored, never committed."""
    if not result.notes:
        return None
    version = int(result.manifest["gold_version"])  # type: ignore[call-overload]
    path = out_dir / f"decision_notes_v{version}.json"
    _atomic_write_text(
        path,
        json.dumps(
            {key: dict(value) for key, value in result.notes.items()},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return path


def latest_folded_path(out_dir: Path, *, at_most_version: int | None = None) -> Path | None:
    """Return the newest ``folded_vN.json`` history, optionally capped at a gold version."""
    best: tuple[int, Path] | None = None
    if out_dir.exists():
        for candidate in out_dir.glob("folded_v*.json"):
            match = re.fullmatch(r"folded_v(\d+)\.json", candidate.name)
            if not match:
                continue
            version = int(match.group(1))
            if at_most_version is not None and version > at_most_version:
                continue
            if best is None or version > best[0]:
                best = (version, candidate)
    return best[1] if best else None


def write_folded_history(
    result: FoldResult,
    decisions: Mapping[str, Mapping[str, object]],
    out_dir: Path,
    *,
    prior_path: Path | None = None,
) -> Path:
    """Carry reviewed-row history into the next packet without requiring a manual sidecar."""
    prior: dict[str, object] = {}
    if prior_path is not None and prior_path.exists():
        loaded = json.loads(prior_path.read_text(encoding="utf-8"))
        if isinstance(loaded, Mapping):
            prior = {str(key): value for key, value in loaded.items()}
    version = int(result.manifest["gold_version"])  # type: ignore[call-overload]
    gold_id = str(result.manifest["gold_id"])
    for decision_id, decision in decisions.items():
        choice = str(decision.get("pairing") or decision.get("action") or "reviewed")
        existing = prior.get(str(decision_id))
        entry = dict(existing) if isinstance(existing, Mapping) else {}
        entry.update({"summary": choice, "gold_version": version, "gold_id": gold_id})
        for key in DECISION_NOTE_KEYS:
            if key in decision:
                value = str(decision.get(key, "") or "")
                if value:
                    entry[key] = value
                else:
                    entry.pop(key, None)
        prior[str(decision_id)] = entry
    path = out_dir / f"folded_v{version}.json"
    _atomic_write_text(
        path,
        json.dumps(prior, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return path


def write_gold_version(result: FoldResult, out_dir: Path) -> dict[str, Path]:
    """Write ``gold_vN.jsonl`` + its manifest atomically. Gitignored (KTD1): rows carry surfaces."""
    version = int(result.manifest["gold_version"])  # type: ignore[call-overload]
    gold_path = out_dir / f"gold_v{version}.jsonl"
    manifest_path = out_dir / f"gold_v{version}.manifest.json"
    _atomic_write_text(gold_path, result.gold_text)
    _atomic_write_text(
        manifest_path,
        json.dumps(dict(result.manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return {"gold": gold_path, "manifest": manifest_path}


# --------------------------------------------------------------------------------------
# Console rendering of the variant table (the number Damien decides on)
# --------------------------------------------------------------------------------------


def render_variant_table(
    variants: Sequence[VariantStats], replay: Mapping[str, Mapping[str, object]]
) -> str:
    """A plain-text table: items / IRIs / mean set size / replayed tune P/R/F1 per variant."""
    header = (
        f"{'variant':<24}{'items':>8}{'gold IRIs':>11}{'mean set':>10}"
        f"{'tune P':>9}{'tune R':>9}{'tune F1':>9}"
    )
    lines = [header, "-" * len(header)]
    for entry in variants:
        scores = replay.get(entry.variant, {})
        lines.append(
            f"{entry.variant:<24}{entry.items_scored:>8}{entry.gold_iris:>11}"
            f"{entry.mean_set_size:>10.3f}"
            f"{_as_float(scores.get('precision')):>9.4f}"
            f"{_as_float(scores.get('recall')):>9.4f}"
            f"{_as_float(scores.get('f1')):>9.4f}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _folio_search(folio: Any, index: LabelIndex) -> SearchFn:
    def search(query: str, limit: int = 20) -> Sequence[LabelProposal]:
        try:
            results = folio.search_by_label(query)
        except Exception:  # pragma: no cover - provider-specific failure modes
            return ()
        out: list[LabelProposal] = []
        for item in list(results)[:limit]:
            owl, score = item if isinstance(item, tuple) else (item, 0.0)
            iri = str(getattr(owl, "iri", "") or "")
            if not iri:
                continue
            out.append(
                LabelProposal(
                    iri=iri,
                    label=index.label_for(iri) or str(getattr(owl, "label", "") or ""),
                    score=float(score),
                    method="search",
                )
            )
        return out

    return search


def _read_jsonl(path: Path) -> list[dict[str, object]]:  # pragma: no cover - CLI helper
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _slice_metrics(
    baseline: Path, gold_manifest: Path, split_manifest: Path
) -> dict[str, object]:  # pragma: no cover - CLI helper
    """The handful of numbers the v2 sheet compares side by side."""
    out: dict[str, object] = {}
    if baseline.exists():
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        slices = payload.get("slices") or {}
        for name, prefix in (("tune", "tune"), ("firm2", "firm2")):
            entry = slices.get(name) or {}
            overall = entry.get("overall") or {}
            out[f"{prefix}_items"] = overall.get("items")
            out[f"{prefix}_gold_iris"] = overall.get("gold")
            out[f"{prefix}_precision"] = overall.get("precision")
            out[f"{prefix}_recall"] = overall.get("recall")
            out[f"{prefix}_f1"] = overall.get("f1")
        recall_at_k = (slices.get("tune") or {}).get("recall_at_k") or {}
        for k in ("1", "5", "10"):
            out[f"recall_at_{k}"] = recall_at_k.get(k)
    if gold_manifest.exists():
        manifest = json.loads(gold_manifest.read_text(encoding="utf-8"))
        counts = manifest.get("counts") or {}
        out["items_total"] = counts.get("items_total")
        out["items_scored"] = counts.get("items_scored")
    if split_manifest.exists():
        manifest = json.loads(split_manifest.read_text(encoding="utf-8"))
        slices = manifest.get("slices") or {}
        out["frozen"] = (slices.get("frozen") or {}).get("count")
    return {key: value for key, value in out.items() if value is not None}


def _main_v2(args: Any, parser: Any) -> int:  # pragma: no cover - I/O orchestration
    """The gold-v2 gate: per-cell packet in Damien's format, or the granular fold into v3."""
    import sys

    from .answer_rule import load_config, rank_candidates
    from .clusters import collect_raw_candidates, surface_strings
    from .packet_render import write_packet_v2
    from .resolve_labels import index_from_folio
    from .score import PipelineAdapter, build_folio_provider, build_pipeline
    from .selftest import OntologyPinError, assert_ontology_pin
    from .splits import FROZEN_SLICE, SLICE_NAMES, load_gold, load_split_manifest

    gold_set = load_gold(args.gold)
    try:
        pin = assert_ontology_pin(gold_set.ontology_cache_sha256)
    except OntologyPinError as error:
        if not args.allow_ontology_bump:
            print(f"ABORT: {error}", file=sys.stderr)
            return 2
        print(f"WARNING (--allow-ontology-bump): {error}", file=sys.stderr)
        pin = assert_ontology_pin("")
    rows = load_gold_rows_v2(args.gold)
    out_dir = Path(args.out)
    packet_path = out_dir / "packet.json"

    current_gold_path = (
        Path(args.current_gold) if args.current_gold else latest_gold_path(Path(args.gold))
    )
    if current_gold_path == Path(args.gold):
        current_rows = rows
        current_gold_version = gold_set.gold_version
        current_gold_id = gold_set.gold_id
    else:
        current_rows = load_gold_rows_v2(current_gold_path)
        current_manifest_path = current_gold_path.parent / (
            current_gold_path.stem + ".manifest.json"
        )
        current_manifest = (
            json.loads(current_manifest_path.read_text(encoding="utf-8"))
            if current_manifest_path.exists()
            else {}
        )
        current_gold_version = _as_version(
            current_manifest.get("gold_version")
            or (current_rows[0].payload.get("gold_version") if current_rows else 0)
        )
        current_gold_id = str(current_manifest.get("gold_id", current_gold_path.stem))
        print(
            f"reference panels read live gold from {current_gold_path} "
            f"(v{current_gold_version}, {current_gold_id})",
            file=sys.stderr,
        )

    if args.mode == "fold-v2":
        if args.decisions is None:
            print("--decisions is required in fold-v2 mode", file=sys.stderr)
            return 2
        packet = packet_v2_from_json(json.loads(packet_path.read_text(encoding="utf-8")))
        decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
        result = fold_granular_decisions(
            current_rows,
            decisions,
            packet=packet,
            ontology_sha256=pin.sha256,
            parent_gold_id=current_gold_id,
            base_gold_version=current_gold_version,
        )
        written = write_gold_version(result, Path(args.gold_out))
        append_decisions(
            Path(args.decision_log), result.records, surfaces=surface_strings(gold_set)
        )
        notes_path = write_decision_notes(result, Path(args.gold_out))
        prior_folded = args.folded or latest_folded_path(
            Path(args.gold_out), at_most_version=current_gold_version
        )
        folded_path = write_folded_history(
            result, decisions, Path(args.gold_out), prior_path=prior_folded
        )
        print(json.dumps(dict(result.manifest), indent=2, sort_keys=True))
        print(f"wrote {written['gold']} and {written['manifest']}")
        print(f"wrote folded review history to {folded_path}")
        if notes_path is not None:
            print(f"wrote {len(result.notes)} decision note(s) to {notes_path}")
        return 0

    split_manifest = load_split_manifest(args.split_manifest, gold_set)
    config = load_config(args.config)
    slice_by_item = {
        item_id: name for name in SLICE_NAMES for item_id in split_manifest.slices[name]
    }
    suspects = _read_jsonl(args.suspects)
    resolution_batch = _read_jsonl(args.resolution_batch)
    pairing_rows = _read_jsonl(args.pairing)
    inconsistent = _read_jsonl(args.inconsistent)
    cluster_rows = _read_jsonl(args.clusters)

    from folio import FOLIO

    print("loading FOLIO…", file=sys.stderr)
    folio = FOLIO()
    provider = build_folio_provider(folio)
    index = index_from_folio(folio)
    pipeline = build_pipeline(provider, label_search_limit=args.label_search_limit)
    adapter = PipelineAdapter(pipeline)

    by_id = {row.item_id: row for row in rows}
    by_input = gold_rows_by_input(rows)
    eligible = frozenset(row.stratum_id for row in rows if not row.blank and row.gold_iris)
    # Every row of every section now shows "what folio-resolve says about this input today", so
    # the pairing rows' own input cells and the resolution batch's items need predictions too.
    # Firm-scoped, like every other input-cell lookup: a text-only key binds a firm-1 pairing row
    # to firm 2's like-named item and caches the wrong item's predictions.
    pairing_targets = {
        by_input[key].item_id
        for entry in pairing_rows
        for value in _items(entry.get("inputs"))
        if isinstance(value, Mapping)
        for key in ((str(entry.get("firm", "")), label_key(str(value.get("text", "")))),)
        if key in by_input
    }
    needed = sorted(
        {str(entry.get("item_id", "")) for entry in suspects}
        | {str(entry.get("item_id", "")) for entry in inconsistent}
        | {str(entry.get("item_id", "")) for entry in resolution_batch}
        | pairing_targets
        | {str(entry["item_id"]) for entry in _score_driven_suspects_v2(cluster_rows, by_id)}
        | {row.item_id for row in rows if row.blank and row.stratum_id in eligible}
    )
    needed = [item_id for item_id in needed if item_id in by_id]
    gold_records = {record.item_id: record for record in gold_set.items}
    cache_path = out_dir / "predictions.json"
    # Top the cache up rather than replacing it: adding a panel must not cost a 3,000-item re-run.
    cache = {} if args.refresh_predictions else _read_prediction_cache(cache_path)
    fresh = [item_id for item_id in needed if item_id not in cache and item_id in gold_records]
    if fresh:
        print(
            f"matching {len(fresh)} items not in the cache ({len(cache)} replayed)…",
            file=sys.stderr,
        )
        cache.update(
            collect_raw_candidates(
                [gold_records[item_id] for item_id in fresh],
                adapter,
                label="audit-v2",
                progress_every=args.progress_every,
            )
        )
        _write_prediction_cache(cache_path, cache)
    else:
        print(f"replaying {len(cache)} cached prediction lists from {cache_path}", file=sys.stderr)
    predictions = {
        item_id: tuple(rank_candidates(candidates, config)) for item_id, candidates in cache.items()
    }

    wanted: set[str] = set()
    for row in rows:
        if row.item_id in predictions or "gold_inconsistent" in row.flags:
            wanted.update(row.gold_iris)
    for entry in suspects:
        wanted.update(str(iri) for iri in _items(entry.get("gold_iris")))
    for ranked in predictions.values():
        wanted.update(candidate.iri for candidate in ranked[:PIPELINE_CANDIDATES_SHOWN])
    definitions: dict[str, str] = {}
    for iri in sorted(wanted):
        concept = provider.get_concept(iri)
        if concept is not None and concept.definition:
            definitions[iri] = concept.definition

    search = _folio_search(folio, index)
    labels = sorted(
        {str(entry.get("normalized") or entry.get("raw") or "") for entry in resolution_batch}
    )
    proposals = {
        label: propose_for_label(label, index=index, search=search) for label in labels if label
    }
    for entries in proposals.values():
        for proposal in entries:
            concept = provider.get_concept(proposal.iri)
            if concept is not None and concept.definition:
                definitions.setdefault(proposal.iri, concept.definition)

    value_iris = {value.raw: value.iri for row in rows for value in row.values}

    # -- section F: the bounded proposed-improvements pilot ------------------------------
    improvements: list[dict[str, object]] = []
    wanted_strata = frozenset(args.improvement_stratum_id or ())
    if wanted_strata:
        from .improve import branch_index_from_folio, propose_atoms

        print("indexing FOLIO branches for the improvement pilot…", file=sys.stderr)
        branch_index = branch_index_from_folio(folio)
        # A cell already queued in sections A-E is not asked a second time in F.
        asked: set[str] = (
            pairing_targets
            | {
                str(entry.get("item_id", ""))
                for batch in (inconsistent, suspects, resolution_batch)
                for entry in batch
            }
            | {str(entry["item_id"]) for entry in _score_driven_suspects_v2(cluster_rows, by_id)}
        )
        candidates: list[tuple[int, str, str, list[dict[str, object]]]] = []
        for row in rows:
            if row.firm != "firm1" or row.stratum_id not in wanted_strata:
                continue
            if row.blank or not row.gold_iris or row.item_id in asked:
                continue
            proposed = propose_atoms(
                row.input_text,
                index=index,
                branches=branch_index,
                search=lambda query, limit: [
                    (proposal.iri, proposal.label, proposal.score)
                    for proposal in _folio_search(folio, index)(query, limit)
                ],
                gold_iris=row.gold_iris,
            )
            if not proposed:
                continue
            candidates.append(
                (-len(proposed), row.stratum, row.input_text, [p.to_json() for p in proposed])
            )
        candidates.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
        chosen = candidates[: args.improvement_cap]
        chosen.sort(key=lambda entry: (entry[1], entry[2]))
        by_text_item = {(row.stratum, row.input_text): row.item_id for row in rows}
        for _, stratum, text, payloads in chosen:
            improvements.append({"item_id": by_text_item[(stratum, text)], "proposals": payloads})
            for payload in payloads:
                iri = str(payload["iri"])
                concept = provider.get_concept(iri)
                if concept is not None and concept.definition:
                    definitions.setdefault(iri, concept.definition)
        print(f"improvement pilot: {len(improvements)} cells", file=sys.stderr)

    review_history_path = args.folded or latest_folded_path(
        Path(args.gold).parent, at_most_version=current_gold_version
    )
    folded_decisions = (
        json.loads(review_history_path.read_text(encoding="utf-8"))
        if review_history_path and review_history_path.exists()
        else {}
    )

    split_facts = SplitFacts(
        seed=split_manifest.seed,
        tune=len(split_manifest.slices["tune"]),
        frozen=len(split_manifest.slices[FROZEN_SLICE]),
        firm2=len(split_manifest.slices["firm2"]),
        excluded_surface_duplicates=len(split_manifest.excluded_surface_duplicates),
        realized_frozen_fraction=_realized_fraction(split_manifest),
        small_strata=len(split_manifest.small_strata),
        manifest_sha256=split_manifest.content_sha256,
    )
    gold_dir = Path(args.gold).parent
    metrics = {
        "v1": _slice_metrics(
            args.baseline_v1,
            gold_dir / "gold_v1.manifest.json",
            gold_dir / "split_manifest_v1.json",
        ),
        "v2": _slice_metrics(
            args.baseline_v2,
            gold_dir / "gold_v2.manifest.json",
            Path(args.split_manifest),
        ),
    }

    packet = build_packet_v2(
        gold_rows=rows,
        current_gold_rows=current_rows,
        current_gold_version=current_gold_version,
        current_gold_id=current_gold_id,
        pairing_rows=pairing_rows,
        inconsistent_groups=inconsistent,
        suspects=suspects,
        resolution_batch=resolution_batch,
        cluster_rows=cluster_rows,
        predictions=predictions,
        definitions=definitions,
        label_proposals=proposals,
        value_iris=value_iris,
        frozen_ids=split_manifest.slices[FROZEN_SLICE],
        rejected=rejection_memory(
            load_decisions(Path(args.decision_log)), ontology_sha256=pin.sha256
        ),
        eligible_strata=eligible,
        slice_by_item=slice_by_item,
        split=split_facts,
        metrics=metrics,
        ontology_sha256=pin.sha256,
        gold_version=gold_set.gold_version,
        gold_id=gold_set.gold_id,
        parent_gold_id=str(gold_set.manifest.get("parent_gold_id", "")),
        prefill_rulings=load_prefill_rulings(args.prefill),
        sheet_sources=load_sheet_sources(
            data_dir=args.data_dir, manifest_path=args.intake_manifest
        ),
        answer_config=config,
        pairing_note=load_pairing_note(args.pairing_note),
        harness_config_sha256=config.content_sha256(),
        suspect_cap=args.suspect_cap,
        new_gold_cap=args.new_gold_cap,
        improvements=improvements,
        improvement_cap=args.improvement_cap,
        folded_decisions=folded_decisions,
    )
    written = write_packet_v2(packet, out_dir)
    print(json.dumps(dict(packet.counts), indent=2, sort_keys=True))
    print(f"overflow by reason: {json.dumps(dict(packet.overflow), sort_keys=True)}")
    print(
        "rows per section: "
        + json.dumps({name: len(packet.section(name)) for name in SECTIONS_V2}, sort_keys=True)
    )
    for name, path in written.items():
        print(f"{name}: {path} ({path.stat().st_size} bytes)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - I/O orchestration
    """Generate the audit-gate packet from the real baseline, or fold a decisions file."""
    import argparse

    from .answer_rule import DEFAULT_CONFIG_FILENAME, load_config, rank_candidates
    from .clusters import collect_raw_candidates, surface_strings
    from .packet_render import write_packet
    from .report import DEFAULT_ITEM_REPORT_DIR
    from .resolve_labels import index_from_folio
    from .score import PipelineAdapter, build_folio_provider, build_pipeline
    from .selftest import OntologyPinError, assert_ontology_pin, ensure_hash_seed
    from .splits import (
        DEFAULT_GOLD_DIR,
        DEFAULT_SPLIT_MANIFEST,
        FROZEN_SLICE,
        SLICE_NAMES,
        load_gold,
        load_split_manifest,
    )

    parser = argparse.ArgumentParser(
        prog="python -m folio_eval.audit",
        description="U5 audit gate: assemble the decision packet, or fold Damien's decisions.",
    )
    parser.add_argument(
        "--mode", choices=("packet", "fold", "packet-v2", "fold-v2"), default="packet"
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR / "gold_v1.jsonl")
    parser.add_argument(
        "--current-gold",
        type=Path,
        default=None,
        help=(
            "packet-v2 only: the live gold version every reference/grading panel reads from. "
            "Defaults to the highest gold_vN.jsonl beside --gold, so a plain re-run after a fold "
            "picks up the new truth automatically."
        ),
    )
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--config", type=Path, default=DEFAULT_GOLD_DIR / DEFAULT_CONFIG_FILENAME)
    parser.add_argument("--suspects", type=Path, default=DEFAULT_GOLD_DIR / "suspects_v1.jsonl")
    parser.add_argument(
        "--resolution-batch", type=Path, default=DEFAULT_GOLD_DIR / "resolution_batch_v1.jsonl"
    )
    parser.add_argument(
        "--clusters", type=Path, default=DEFAULT_ITEM_REPORT_DIR / "clusters_v1.jsonl"
    )
    parser.add_argument("--item-report-dir", type=Path, default=DEFAULT_ITEM_REPORT_DIR)
    parser.add_argument("--item-csv-label", default="baseline-v1")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="packet directory (defaults to audit_packet_v1 or audit_packet_v2 by mode)",
    )
    parser.add_argument("--decision-log", type=Path, default=DEFAULT_DECISION_LOG)
    parser.add_argument("--decisions", type=Path, default=None, help="decisions file (--mode fold)")
    parser.add_argument("--gold-out", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--label-search-limit", type=int, default=10)
    parser.add_argument(
        "--suspect-cap",
        type=int,
        default=SUSPECT_ROW_CAP,
        help="detailed suspect rows in this batch (KTD9 default 50; the rest report as counts)",
    )
    parser.add_argument("--new-gold-cap", type=int, default=NEW_GOLD_CAP)
    parser.add_argument(
        "--improvement-cap",
        type=int,
        default=IMPROVEMENT_CAP,
        help="section-F pilot size (cells); 0 with no --improvement-stratum-id disables it",
    )
    parser.add_argument(
        "--improvement-stratum-id",
        action="append",
        default=[],
        help=(
            "stratum id (hashed, KTD1-safe) whose un-reviewed cells the section-F pilot runs "
            "over; repeatable. Omitted = no pilot."
        ),
    )
    parser.add_argument(
        "--folded",
        type=Path,
        default=None,
        help=(
            "JSON of {decision_id: {summary, note, gold_version, gold_id}} already folded into a "
            "later gold version; those rows render locked instead of being asked again"
        ),
    )
    parser.add_argument("--progress-every", type=int, default=200)
    parser.add_argument(
        "--refresh-predictions",
        action="store_true",
        help="re-run the pipeline even when the packet directory holds a usable prediction cache",
    )
    parser.add_argument("--allow-ontology-bump", action="store_true")
    parser.add_argument(
        "--pairing", type=Path, default=DEFAULT_GOLD_DIR / "pairing_ambiguous_v2.jsonl"
    )
    parser.add_argument(
        "--inconsistent", type=Path, default=DEFAULT_GOLD_DIR / "gold_inconsistent_v2.jsonl"
    )
    parser.add_argument("--prefill", type=Path, default=DEFAULT_PREFILL_PATH)
    parser.add_argument("--pairing-note", type=Path, default=DEFAULT_PAIRING_NOTE_PATH)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--intake-manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--baseline-v1", type=Path, default=_EVAL_ROOT / "reports" / "baseline-v1.json"
    )
    parser.add_argument(
        "--baseline-v2", type=Path, default=_EVAL_ROOT / "reports" / "baseline-v2.json"
    )
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = DEFAULT_PACKET_DIR_V2 if args.mode.endswith("-v2") else DEFAULT_PACKET_DIR

    import sys

    ensure_hash_seed()
    if args.mode in ("packet-v2", "fold-v2"):
        return _main_v2(args, parser)
    gold_set = load_gold(args.gold)
    try:
        pin = assert_ontology_pin(gold_set.ontology_cache_sha256)
    except OntologyPinError as error:
        if not args.allow_ontology_bump:
            print(f"ABORT: {error}", file=sys.stderr)
            return 2
        print(f"WARNING (--allow-ontology-bump): {error}", file=sys.stderr)
        pin = assert_ontology_pin("")
    split_manifest = load_split_manifest(args.split_manifest, gold_set)
    rows = load_gold_rows(args.gold)
    config = load_config(args.config)
    slice_by_item = {
        item_id: name for name in SLICE_NAMES for item_id in split_manifest.slices[name]
    }
    surfaces = surface_strings(gold_set)

    packet_path = Path(args.out) / "packet.json"

    if args.mode == "fold":
        if args.decisions is None:
            print("--decisions is required in fold mode", file=sys.stderr)
            return 2
        packet = _packet_from_json(json.loads(packet_path.read_text(encoding="utf-8")))
        decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
        result = fold_decisions(rows, decisions, packet=packet, ontology_sha256=pin.sha256)
        written = write_gold_version(result, Path(args.gold_out))
        append_decisions(Path(args.decision_log), result.records, surfaces=surfaces)
        print(json.dumps(dict(result.manifest), indent=2, sort_keys=True))
        print(f"wrote {written['gold']} and {written['manifest']}")
        return 0

    # -- packet mode --------------------------------------------------------------------
    suspects = [
        json.loads(line)
        for line in args.suspects.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    resolution_batch = [
        json.loads(line)
        for line in args.resolution_batch.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cluster_rows = (
        [
            json.loads(line)
            for line in args.clusters.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if args.clusters.exists()
        else []
    )

    from folio import FOLIO

    print("loading FOLIO…", file=sys.stderr)
    folio = FOLIO()
    provider = build_folio_provider(folio)
    index = index_from_folio(folio)
    pipeline = build_pipeline(provider, label_search_limit=args.label_search_limit)
    adapter = PipelineAdapter(pipeline)

    by_id = {row.item_id: row for row in rows}
    eligible = _strata_with_gold(rows)

    # items whose predictions the packet needs: every suspect, plus every eligible blank row
    needed: list[str] = sorted(
        {str(entry.get("item_id", "")) for entry in suspects}
        | {str(entry["item_id"]) for entry in _score_driven_suspects(cluster_rows, by_id)}
        | {row.item_id for row in rows if row.blank and row.stratum_id in eligible}
    )
    needed = [item_id for item_id in needed if item_id in by_id]
    gold_records = {record.item_id: record for record in gold_set.items}
    cache_path = Path(args.out) / "predictions.json"
    cache = _load_prediction_cache(cache_path, needed) if not args.refresh_predictions else None
    if cache is None:
        print(f"matching {len(needed)} items (suspects + eligible blank rows)…", file=sys.stderr)
        cache = collect_raw_candidates(
            [gold_records[item_id] for item_id in needed if item_id in gold_records],
            adapter,
            label="audit",
            progress_every=args.progress_every,
        )
        _write_prediction_cache(cache_path, cache)
    else:
        print(f"replaying {len(cache)} cached prediction lists from {cache_path}", file=sys.stderr)
    predictions = {
        item_id: tuple(rank_candidates(candidates, config)) for item_id, candidates in cache.items()
    }

    # definitions for every IRI the packet will show
    wanted: set[str] = set()
    for entry in suspects:
        wanted.update(str(iri) for iri in _items(entry.get("gold_iris")))
    for ranked in predictions.values():
        wanted.update(candidate.iri for candidate in ranked[:1])
    for row in rows:
        if row.item_id in predictions:
            wanted.update(row.gold_iris)
    definitions: dict[str, str] = {}
    for iri in sorted(wanted):
        concept = provider.get_concept(iri)
        if concept is not None and concept.definition:
            definitions[iri] = concept.definition

    # resolution-batch enrichment
    search = _folio_search(folio, index)
    labels = sorted(
        {str(entry.get("normalized") or entry.get("raw") or "") for entry in resolution_batch}
    )
    proposals = {
        label: propose_for_label(label, index=index, search=search) for label in labels if label
    }

    # variant replay from the committed tune predictions
    tune_csv = (
        Path(args.item_report_dir) / f"items-{gold_set.gold_id}-tune-{args.item_csv_label}.csv"
    )
    replay: dict[str, dict[str, object]] = {}
    if tune_csv.exists():
        committed = load_item_predictions(tune_csv)
        tune_rows = [row for row in rows if row.item_id in committed]
        for variant, counts in replay_variants(committed, tune_rows).items():
            replay[variant] = counts.to_json()
    else:
        print(f"no per-item CSV at {tune_csv} — variant replay skipped", file=sys.stderr)

    split_facts = SplitFacts(
        seed=split_manifest.seed,
        tune=len(split_manifest.slices["tune"]),
        frozen=len(split_manifest.slices[FROZEN_SLICE]),
        firm2=len(split_manifest.slices["firm2"]),
        excluded_surface_duplicates=len(split_manifest.excluded_surface_duplicates),
        realized_frozen_fraction=_realized_fraction(split_manifest),
        small_strata=len(split_manifest.small_strata),
        manifest_sha256=split_manifest.content_sha256,
    )

    packet = build_packet(
        gold_rows=rows,
        suspects=suspects,
        resolution_batch=resolution_batch,
        cluster_rows=cluster_rows,
        predictions=predictions,
        definitions=definitions,
        label_proposals=proposals,
        frozen_ids=split_manifest.slices[FROZEN_SLICE],
        rejected=rejection_memory(
            load_decisions(Path(args.decision_log)), ontology_sha256=pin.sha256
        ),
        eligible_strata=eligible,
        slice_by_item=slice_by_item,
        split=split_facts,
        replay=replay,
        ontology_sha256=pin.sha256,
        gold_version=gold_set.gold_version,
        gold_id=gold_set.gold_id,
        harness_config_sha256=config.content_sha256(),
        suspect_cap=args.suspect_cap,
        new_gold_cap=args.new_gold_cap,
        extra_meta={"tune_item_csv": tune_csv.name if tune_csv.exists() else ""},
    )

    written = write_packet(packet, Path(args.out))
    print(render_variant_table(packet.variants, packet.replay))
    print()
    print(json.dumps(dict(packet.counts), indent=2, sort_keys=True))
    print(f"overflow by reason: {json.dumps(dict(packet.overflow), sort_keys=True)}")
    for name, path in written.items():
        print(f"{name}: {path} ({path.stat().st_size} bytes)")
    return 0


def _read_prediction_cache(
    path: Path,
) -> dict[str, tuple[RawCandidate, ...]]:  # pragma: no cover - CLI helper
    """Whatever the cache holds, however partial — the caller tops up what is missing."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {
        str(item_id): tuple(
            RawCandidate(
                iri=str(entry["iri"]),
                label=str(entry.get("label", "")),
                score=float(entry.get("score", 0.0)),
                extraction_path=str(entry.get("extraction_path", "")),
                gated=bool(entry.get("gated", False)),
            )
            for entry in entries
        )
        for item_id, entries in payload.items()
    }


def _load_prediction_cache(
    path: Path, needed: Sequence[str]
) -> dict[str, tuple[RawCandidate, ...]] | None:  # pragma: no cover - CLI helper
    """The cached raw candidate lists, or ``None`` when the cache cannot serve every needed item."""
    cache = _read_prediction_cache(path)
    if not cache or not set(needed) <= set(cache):
        return None
    return cache


def _write_prediction_cache(
    path: Path, cache: Mapping[str, Sequence[RawCandidate]]
) -> None:  # pragma: no cover - CLI helper
    """Cache the pipeline pass so the packet can be regenerated (new cap, new filter) for free."""
    payload = {
        item_id: [
            {
                "iri": candidate.iri,
                "label": candidate.label,
                "score": candidate.score,
                "extraction_path": candidate.extraction_path,
                "gated": candidate.gated,
            }
            for candidate in candidates
        ]
        for item_id, candidates in sorted(cache.items())
    }
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def latest_gold_path(base: Path) -> Path:
    """The highest-numbered ``gold_vN.jsonl`` beside ``base`` (``base`` itself if none is higher).

    The packet's own sections are asked against whatever ``--gold`` names (the base a fold started
    from), but every reference/grading panel must read the *live* gold state -- the actual latest
    version on disk, which already carries every decision folded so far (Damien, 2026-07-28: a
    folded row's panel kept showing the pre-fold snapshot). Auto-detecting the highest version
    beside ``--gold`` means a plain re-run after a fold picks up the new truth with no extra flag.
    """
    match = re.match(r"gold_v(\d+)\.jsonl$", base.name)
    best_version = int(match.group(1)) if match else 0
    best = base
    directory = base.parent
    if directory.exists():
        for candidate in directory.glob("gold_v*.jsonl"):
            found = re.match(r"gold_v(\d+)\.jsonl$", candidate.name)
            if found and int(found.group(1)) > best_version:
                best_version = int(found.group(1))
                best = candidate
    return best


def _realized_fraction(manifest: Any) -> float:  # pragma: no cover - CLI helper
    frozen = len(manifest.slices["frozen"])
    tune = len(manifest.slices["tune"])
    total = frozen + tune
    return frozen / total if total else 0.0


def _entries(raw: object) -> tuple[Mapping[str, object], ...]:
    return tuple(dict(entry) for entry in _items(raw) if isinstance(entry, Mapping))


def packet_v2_from_json(payload: Mapping[str, object]) -> Packet:
    """Rehydrate a v2 packet, keeping the per-concept gold/pipeline blocks the fold grades."""
    rows = tuple(
        PacketRow(
            decision_id=str(entry["decision_id"]),
            section=str(entry["section"]),
            item_id=str(entry.get("item_id", "")),
            firm=str(entry.get("firm", "")),
            stratum=str(entry.get("stratum", "")),
            stratum_id=str(entry.get("stratum_id", "")),
            ancestor_path=tuple(str(part) for part in _items(entry.get("ancestor_path"))),
            surface_label=str(entry.get("surface_label", "")),
            input_text=str(entry.get("input_text", "")),
            slice_name=str(entry.get("slice", "")),
            reason_class=str(entry.get("reason_class", "")),
            suggested_action=str(entry.get("suggested_action", "")),
            gold=_entries(entry.get("gold")),
            pipeline=_entries(entry.get("pipeline")),
            proposed_iris=tuple(str(iri) for iri in _items(entry.get("proposed_iris"))),
            extra=dict(entry.get("extra") or {}) if isinstance(entry.get("extra"), Mapping) else {},
        )
        for entry in _items(payload.get("rows"))
        if isinstance(entry, Mapping)
    )
    meta_raw = payload.get("meta") or {}
    counts_raw = payload.get("counts") or {}
    return Packet(
        rows=rows,
        variants=(),
        replay={},
        split=None,
        counts={
            str(key): _as_int(value)
            for key, value in (counts_raw.items() if isinstance(counts_raw, Mapping) else ())
        },
        overflow={},
        meta=dict(meta_raw) if isinstance(meta_raw, Mapping) else {},
    )


def _packet_from_json(payload: Mapping[str, object]) -> Packet:
    """Rehydrate a packet written by :func:`packet_render.write_packet` (fold mode)."""
    rows_raw = payload.get("rows") or []
    assert isinstance(rows_raw, list)
    rows = tuple(
        PacketRow(
            decision_id=str(entry["decision_id"]),
            section=str(entry["section"]),
            item_id=str(entry.get("item_id", "")),
            firm=str(entry.get("firm", "")),
            stratum=str(entry.get("stratum", "")),
            stratum_id=str(entry.get("stratum_id", "")),
            ancestor_path=tuple(str(part) for part in _items(entry.get("ancestor_path"))),
            surface_label=str(entry.get("surface_label", "")),
            input_text=str(entry.get("input_text", "")),
            slice_name=str(entry.get("slice", "")),
            reason_class=str(entry.get("reason_class", "")),
            suggested_action=str(entry.get("suggested_action", "")),
            proposed_iris=tuple(str(iri) for iri in _items(entry.get("proposed_iris"))),
            extra=dict(entry.get("extra") or {}) if isinstance(entry.get("extra"), Mapping) else {},
        )
        for entry in rows_raw
    )
    meta_raw = payload.get("meta") or {}
    assert isinstance(meta_raw, Mapping)
    counts_raw = payload.get("counts") or {}
    assert isinstance(counts_raw, Mapping)
    return Packet(
        rows=rows,
        variants=(),
        replay={},
        split=None,
        counts={str(key): int(value) for key, value in counts_raw.items()},
        overflow={},
        meta=dict(meta_raw),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
