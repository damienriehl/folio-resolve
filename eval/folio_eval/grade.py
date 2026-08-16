"""Independent synthetic grading, close-call routing, and audit sampling (U7).

``DEFAULT_FLOOR`` is PROVISIONAL until adjudicated examples calibrate it.  The fixed
disagreement vocabulary is: ``set_mismatch`` (disjoint valid proposals), ``partial_overlap``
(different valid sets sharing an IRI), ``sub_floor_confidence`` (an otherwise agreeing proposal
has fewer than two above-floor voters), ``ambiguous_label`` / ``unresolved_label`` (U5 resolver
quarantine), and ``empty_proposal`` (a grader supplied no concepts).

Gate 1b policy is deliberately a dispatch concern: synthetic sittings may be dispatched only
while the firm sheet is empty.  :func:`folio_eval.packet_render.write_sitting_v2` exposes the
``firm_sheet_empty`` assertion at the writer seam.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .audit import Packet, PacketRow
from .normalize import label_key
from .resolve_labels import LabelIndex, resolve_gold_value
from .synthesize import SyntheticItem

DEFAULT_FLOOR = 0.6  # PROVISIONAL: replace from calibrate_floor on Damien-adjudicated examples.
DISAGREEMENT_CLASSES = frozenset(
    {
        "set_mismatch",
        "sub_floor_confidence",
        "ambiguous_label",
        "unresolved_label",
        "partial_overlap",
        "empty_proposal",
    }
)
VOTE_SCHEMA_VERSION = 1
VOTE_FILE_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["version", "grader_id", "model_family", "votes"],
    "properties": {
        "version": {"const": VOTE_SCHEMA_VERSION},
        "grader_id": {"type": "string", "minLength": 1},
        "model_family": {"type": "string", "minLength": 1},
        "votes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["item_id", "generator_id_claimed", "concepts"],
                "properties": {
                    "item_id": {"type": "string", "minLength": 1},
                    "generator_id_claimed": {"type": "string"},
                    "concepts": {
                        "type": "object",
                        "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


class GradeError(ValueError):
    """A malformed or unsafe vote batch."""


@dataclass(frozen=True, slots=True)
class GraderVote:
    item_id: str
    grader_id: str
    model_family: str
    concepts: Mapping[str, float]
    generator_id_claimed: str

    def to_json(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "grader_id": self.grader_id,
            "model_family": self.model_family,
            "concepts": dict(sorted(self.concepts.items())),
            "generator_id_claimed": self.generator_id_claimed,
        }


@dataclass(frozen=True, slots=True)
class CloseCall:
    item: SyntheticItem
    disagreement_class: str
    votes: tuple[GraderVote, ...]

    def packet_row(self) -> PacketRow:
        return PacketRow(
            decision_id=f"synthetic:{self.item.item_id}",
            section="suspect",
            item_id=self.item.item_id,
            firm="synthetic",
            stratum=self.item.doc_type,
            stratum_id=self.item.doc_type,
            ancestor_path=(),
            surface_label=self.item.item_id,
            input_text=self.item.text,
            slice_name="synthetic",
            reason_class=self.disagreement_class,
            suggested_action="review",
            pipeline=tuple(
                {
                    "grader_id": vote.grader_id,
                    "model_family": vote.model_family,
                    "labels": dict(sorted(vote.concepts.items())),
                }
                for vote in self.votes
            ),
            extra={"verification": self.item.verification},
        )


@dataclass(frozen=True, slots=True)
class GradeOutcome:
    items: tuple[SyntheticItem, ...]
    close_calls: tuple[CloseCall, ...]
    skipped_ratified: tuple[str, ...]

    @property
    def machine_agreed_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.items if item.verification == "deterministic")

    def close_call_packet(self) -> Packet:
        rows = tuple(call.packet_row() for call in self.close_calls)
        return Packet(
            rows=rows,
            variants=(),
            replay={},
            split=None,
            counts={"close_calls": len(rows)},
            overflow={},
            meta={"lane": "synthetic"},
        )


def _generator_identity(item: SyntheticItem) -> tuple[str, str]:
    provenance = item.provenance
    generator_id = str(provenance.get("generator_id", provenance.get("generator", "")))
    model_family = str(provenance.get("model_family", provenance.get("generator_model_family", "")))
    return generator_id, model_family


def _dictionary_keys(dictionary: LabelIndex) -> frozenset[str]:
    return frozenset(dictionary.norm_preferred) | frozenset(dictionary.norm_alternative)


def load_vote_file(
    path: Path, items: Iterable[SyntheticItem], *, dictionary: LabelIndex
) -> tuple[GraderVote, ...]:
    """Load one version-1, one-grader vote file and reject unsafe identities or labels."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != VOTE_SCHEMA_VERSION:
        raise GradeError("vote file must be a version-1 object")
    grader_id = payload.get("grader_id")
    model_family = payload.get("model_family")
    raw_votes = payload.get("votes")
    if not isinstance(grader_id, str) or not grader_id or not isinstance(model_family, str) or not model_family:
        raise GradeError("vote file requires grader_id and model_family")
    if not isinstance(raw_votes, list):
        raise GradeError("vote file requires a votes list")
    by_id = {item.item_id: item for item in items}
    known_labels = _dictionary_keys(dictionary)
    result: list[GraderVote] = []
    seen: set[str] = set()
    for raw in raw_votes:
        if not isinstance(raw, dict) or not isinstance(raw.get("concepts"), dict):
            raise GradeError("malformed vote row")
        item_id = raw.get("item_id")
        claimed = raw.get("generator_id_claimed")
        if not isinstance(item_id, str) or item_id not in by_id or item_id in seen:
            raise GradeError(f"unknown or duplicate vote item_id: {item_id!r}")
        if not isinstance(claimed, str):
            raise GradeError(f"vote {item_id!r} lacks generator_id_claimed")
        generator_id, generator_family = _generator_identity(by_id[item_id])
        if claimed != generator_id:
            raise GradeError(f"vote {item_id!r} generator claim does not match item provenance")
        if grader_id == generator_id or (generator_family and model_family == generator_family):
            raise GradeError(f"generator may not grade its own item: {item_id}")
        concepts: dict[str, float] = {}
        for label, confidence in raw["concepts"].items():
            if not isinstance(label, str) or label_key(label) not in known_labels:
                raise GradeError(f"vote {item_id!r} names an off-dictionary label")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise GradeError(f"vote {item_id!r} has malformed confidence")
            value = float(confidence)
            if not 0.0 <= value <= 1.0:
                raise GradeError(f"vote {item_id!r} confidence is outside 0..1")
            concepts[label] = value
        seen.add(item_id)
        result.append(GraderVote(item_id, grader_id, model_family, concepts, claimed))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class _ResolvedVote:
    vote: GraderVote
    iris: frozenset[str]
    mean: float
    issue: str | None = None


def _resolve_vote(vote: GraderVote, dictionary: LabelIndex) -> _ResolvedVote:
    iris: set[str] = set()
    issue: str | None = None
    for label in vote.concepts:
        resolution = resolve_gold_value(label, dictionary)
        if resolution.ambiguous:
            issue = "ambiguous_label"
        elif not resolution.resolved:
            issue = issue or "unresolved_label"
        elif resolution.iri is not None:
            iris.add(resolution.iri)
    mean = sum(vote.concepts.values()) / len(vote.concepts) if vote.concepts else 0.0
    return _ResolvedVote(vote, frozenset(iris), mean, issue)


def _reason(resolved: Sequence[_ResolvedVote], floor: float) -> str:
    issues = [entry.issue for entry in resolved if entry.issue]
    if "ambiguous_label" in issues:
        return "ambiguous_label"
    if "unresolved_label" in issues:
        return "unresolved_label"
    if any(not entry.iris for entry in resolved):
        return "empty_proposal"
    sets = {entry.iris for entry in resolved}
    if len(sets) > 1:
        common = set.intersection(*(set(value) for value in sets))
        return "partial_overlap" if common else "set_mismatch"
    if sum(entry.mean >= floor for entry in resolved) < 2:
        return "sub_floor_confidence"
    return "set_mismatch"


def fold_votes(
    items: Iterable[SyntheticItem], votes: Iterable[GraderVote], *, floor: float, dictionary: LabelIndex
) -> GradeOutcome:
    """Fold independent votes; any three-grader dissent remains a close call for Damien."""
    if not 0.0 <= floor <= 1.0:
        raise ValueError("floor must be within 0..1")
    grouped: dict[str, list[GraderVote]] = {}
    for vote in votes:
        grouped.setdefault(vote.item_id, []).append(vote)
    folded: list[SyntheticItem] = []
    queue: list[CloseCall] = []
    skipped: list[str] = []
    for item in items:
        if item.verification == "human":
            folded.append(item)
            skipped.append(item.item_id)
            continue
        item_votes = tuple(grouped.get(item.item_id, ()))
        resolved = tuple(_resolve_vote(vote, dictionary) for vote in item_votes)
        set_counts = Counter(entry.iris for entry in resolved if entry.issue is None and entry.iris)
        winner = set_counts.most_common(1)[0][0] if set_counts else frozenset()
        agreeing = [entry for entry in resolved if entry.iris == winner and entry.mean >= floor]
        unanimous_sets = len({entry.iris for entry in resolved}) == 1
        if len(resolved) == 3 and unanimous_sets and len(agreeing) >= 2 and not any(entry.issue for entry in resolved):
            labels = tuple(sorted({label for entry in resolved for label in entry.vote.concepts}))
            provenance = dict(item.provenance)
            provenance["grader_votes"] = [entry.vote.to_json() for entry in resolved]
            folded.append(
                replace(item, gold_labels=labels, gold_iris=winner, verification="deterministic", provenance=provenance)
            )
        else:
            quarantined = replace(item, gold_iris=frozenset(), verification="needs_review")
            folded.append(quarantined)
            queue.append(CloseCall(quarantined, _reason(resolved, floor), item_votes))
    return GradeOutcome(tuple(folded), tuple(queue), tuple(skipped))


def calibrate_floor(adjudicated_sample: Iterable[Mapping[str, object]]) -> float:
    """Sweep observed confidence thresholds, maximizing binary agreement with Damien's rulings."""
    rows = tuple(adjudicated_sample)
    if not rows:
        return DEFAULT_FLOOR
    confidences: list[float] = []
    for row in rows:
        raw = row.get("confidence")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("adjudicated confidence must be numeric")
        confidence = float(raw)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("adjudicated confidence must be within 0..1")
        confidences.append(confidence)
    candidates = sorted({0.0, 1.0, *confidences})
    return max(
        candidates,
        key=lambda threshold: (
            sum(
                (confidence >= threshold) == bool(row.get("accepted"))
                for row, confidence in zip(rows, confidences, strict=True)
            ),
            -threshold,
        ),
    )


def audit_sample(outcome: GradeOutcome, *, size: int = 25, seed: int) -> list[str]:
    """Deterministically sample machine-agreed rows for a corpus-version audit sitting."""
    if size < 0:
        raise ValueError("audit sample size must be non-negative")
    ids = sorted(outcome.machine_agreed_ids)
    random.Random(seed).shuffle(ids)
    return ids[:size]


def audit_report(sample_rulings: Iterable[object]) -> float:
    """Return corrections / audited rows; booleans mean ``True`` = correction required."""
    rulings = tuple(sample_rulings)
    if not rulings:
        return 0.0
    corrections = sum(
        bool(row.get("corrected", row.get("correction", False))) if isinstance(row, Mapping) else bool(row)
        for row in rulings
    )
    return corrections / len(rulings)
