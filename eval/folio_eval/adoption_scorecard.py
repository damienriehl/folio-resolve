"""Offline, descriptive adoption prerequisites; never an adoption decision.

Only generated fixtures or explicitly supplied bound evidence are read. Existing
R11/R17 decisions, consumers, and frozen evidence validators are not modified.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .recall_consumers import REQUIRED_PUBLISHED_ARMS
from .report import PairedItem, bootstrap_ci, f1_delta

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ItemKey = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Count = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
PROJECTION = "semantic-result-v1"
ARMS = tuple(sorted(REQUIRED_PUBLISHED_ARMS, key=lambda arm: (arm[0], arm[1] or "")))


class EvidenceError(ValueError):
    """A fixed, content-free error for untrusted evidence."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Binding(StrictModel):
    corpus_sha256: Digest
    gold_sha256: Digest
    ontology_sha256: Digest
    scoring_sha256: Digest


class VerifierIdentity(StrictModel):
    verifier_sha256: Digest
    configuration_sha256: Digest


class GoldRow(StrictModel):
    item_key: ItemKey
    gold_count: Positive


class Cohort(StrictModel):
    scoreable_count: Positive
    no_match_count: Count
    scoreable: list[GoldRow]
    no_match: list[ItemKey]


class ScoreRow(GoldRow):
    tp: Count
    fp: Count
    fn: Count
    predicted_count: Count

    @model_validator(mode="after")
    def arithmetic(self) -> Self:
        if self.tp + self.fn != self.gold_count or self.tp + self.fp != self.predicted_count:
            raise ValueError("arithmetic")
        return self


class ControlRow(StrictModel):
    item_key: ItemKey
    predicted_count: Count


class Run(StrictModel):
    source_sha256: Digest
    scoreable: list[ScoreRow]
    no_match: list[ControlRow]


class Baseline(Run):
    r14_reference_sha256: Digest


class BoundSection(StrictModel):
    binding: Binding
    lever_sha256: Digest


class SourcePair(BoundSection):
    baseline_source_sha256: Digest
    candidate_source_sha256: Digest


class Recall(SourcePair):
    identity: VerifierIdentity
    gold_count: Positive
    baseline_hits: Count
    candidate_hits: Count
    baseline_depth: Positive
    candidate_depth: Positive


class Verifier(BoundSection):
    identity: VerifierIdentity
    baseline: Run
    candidate: Run


class Observation(StrictModel):
    item_key: ItemKey
    output_sha256: Digest


class Parity(SourcePair):
    projection_version: Literal["semantic-result-v1"]
    flag_enabled: bool
    baseline: list[Observation]
    candidate: list[Observation]


class Arm(BoundSection):
    consumer: Literal["folio-enrich", "folio-mapper"]
    model: Literal["gemini-3-flash-preview", "gpt-6-luna"] | None
    baseline: Baseline | None
    candidate: Run | None
    parity: Parity | None


def _unique_keys(keys: list[str], expected: set[str]) -> None:
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("cohort")


class Bundle(StrictModel):
    schema_version: int
    evidence_kind: Literal["generated_fixture", "recorded_input"]
    binding: Binding
    lever_sha256: Digest
    verifier_identity: VerifierIdentity
    cohort: Cohort
    recall: Recall | None
    verifier: Verifier | None
    arms: list[Arm]

    @model_validator(mode="after")
    def agreement(self) -> Self:
        if self.schema_version != 1:
            raise ValueError("version")
        cohort = self.cohort
        gold = {row.item_key: row.gold_count for row in cohort.scoreable}
        controls = set(cohort.no_match)
        if (
            len(gold) != cohort.scoreable_count
            or len(cohort.scoreable) != len(gold)
            or len(controls) != cohort.no_match_count
            or len(cohort.no_match) != len(controls)
            or set(gold) & controls
        ):
            raise ValueError("population")

        def bound(section: BoundSection) -> None:
            if section.binding != self.binding or section.lever_sha256 != self.lever_sha256:
                raise ValueError("binding")

        def run(value: Run | None) -> None:
            if value is None:
                return
            _unique_keys([row.item_key for row in value.scoreable], set(gold))
            _unique_keys([row.item_key for row in value.no_match], controls)
            if any(row.gold_count != gold[row.item_key] for row in value.scoreable):
                raise ValueError("gold")

        def distinct(before: Run | None, after: Run | None) -> None:
            if (
                before is not None
                and after is not None
                and before.source_sha256 == after.source_sha256
            ):
                raise ValueError("source")

        if self.verifier is not None:
            verifier = self.verifier
            bound(verifier)
            if verifier.identity != self.verifier_identity:
                raise ValueError("configuration")
            run(verifier.baseline)
            run(verifier.candidate)
            distinct(verifier.baseline, verifier.candidate)
        if self.recall is not None:
            recall = self.recall
            bound(recall)
            if (
                recall.identity != self.verifier_identity
                or recall.gold_count != sum(gold.values())
                or max(recall.baseline_hits, recall.candidate_hits) > recall.gold_count
                or recall.baseline_source_sha256 == recall.candidate_source_sha256
            ):
                raise ValueError("recall")
            if self.verifier is not None and (
                recall.baseline_source_sha256 != self.verifier.baseline.source_sha256
                or recall.candidate_source_sha256 != self.verifier.candidate.source_sha256
            ):
                raise ValueError("source")
        identities = [(arm.consumer, arm.model) for arm in self.arms]
        if len(identities) != len(set(identities)) or not set(identities) <= set(ARMS):
            raise ValueError("arms")
        for arm in self.arms:
            bound(arm)
            run(arm.baseline)
            run(arm.candidate)
            distinct(arm.baseline, arm.candidate)
            if arm.parity is not None:
                parity = arm.parity
                bound(parity)
                if (
                    parity.flag_enabled is not False
                    or arm.baseline is None
                    or arm.candidate is None
                    or parity.baseline_source_sha256 != arm.baseline.source_sha256
                    or parity.candidate_source_sha256 != arm.candidate.source_sha256
                ):
                    raise ValueError("parity_identity")
                for observations in (parity.baseline, parity.candidate):
                    _unique_keys([row.item_key for row in observations], set(gold) | controls)
        return self


def validate_bundle(value: object) -> Bundle:
    """Validate the entire bundle before calculating or publishing partial results."""
    try:
        return Bundle.model_validate(value)
    except ValueError:
        raise EvidenceError("invalid_evidence") from None


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("invalid_evidence")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise EvidenceError("invalid_evidence")


def load_bundle(path: Path, expected_sha256: str) -> Bundle:
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise EvidenceError("invalid_evidence")
        return validate_bundle(
            json.loads(raw, object_pairs_hook=_object, parse_constant=_nonfinite)
        )
    except (OSError, ValueError, RecursionError):
        raise EvidenceError("invalid_evidence") from None


def semantic_digest(result: object) -> str:
    """Hash the complete returned result; sort object keys, retain array order.

    Producers supply the complete structured result including scores, abstentions,
    and spans. Timing/execution metadata belongs outside it; there are no field
    exclusions. A digest cannot prove that a producer followed this contract.
    """
    raw = json.dumps(
        {"projection_version": PROJECTION, "result": result},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# Report fields are constructed explicitly; no evidence rows or arbitrary strings escape.
LIMITS = (
    "Mechanical prerequisites only; adoption is not authorized.",
    "Generated fixtures demonstrate gate behavior, not measured improvement.",
    "Recorded input and digests do not authenticate an R14 source or establish reviewed integration.",
    "Parity covers declared semantic-result-v1 observations, not every application behavior.",
    "No-match rates are descriptive; no additional rejection threshold is applied.",
)


def _metrics(run: Run | None) -> dict[str, Any] | None:
    if run is None:
        return None
    tp, fp, fn = (sum(getattr(row, name) for row in run.scoreable) for name in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "items": len(run.scoreable),
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "no_match": {
            "false_positive_items": sum(row.predicted_count > 0 for row in run.no_match),
            "items": len(run.no_match),
            "rate": sum(row.predicted_count > 0 for row in run.no_match) / len(run.no_match)
            if run.no_match
            else None,
        },
    }


def _interval(before: Run | None, after: Run | None) -> dict[str, Any] | None:
    if before is None or after is None:
        return None
    # Full cohorts were validated. Do not use report.pair_items' intersection join.
    pairs = [
        PairedItem(
            a.item_key, a.tp, a.fp, a.fn, b.tp, b.fp, b.fn, a.fp == a.fn == 0, b.fp == b.fn == 0
        )
        for a, b in zip(
            sorted(before.scoreable, key=lambda r: r.item_key),
            sorted(after.scoreable, key=lambda r: r.item_key),
            strict=True,
        )
    ]
    interval = bootstrap_ci(pairs, f1_delta, alpha=0.05)
    return dict(
        asdict(interval),
        statistic="paired_micro_f1_gain",
        method="item_percentile_bootstrap",
        confidence=0.95,
        sidedness="two-sided",
    )


def _gate(condition: bool | None, reason: str) -> dict[str, Any]:
    return {
        "status": "missing" if condition is None else "pass" if condition else "fail",
        "reasons": [] if condition else [reason],
    }


def evaluate(bundle: Bundle, input_sha256: str) -> dict[str, Any]:
    """Pure, deterministic four-arm assessment of already validated evidence."""
    # Revalidate to defend callers that mutate a model after parsing.
    bundle = validate_bundle(bundle.model_dump())
    if len(input_sha256) != 64 or any(c not in "0123456789abcdef" for c in input_sha256):
        raise EvidenceError("invalid_evidence")
    recall, verifier = bundle.recall, bundle.verifier
    verifier_before = _metrics(verifier.baseline if verifier else None)
    verifier_after = _metrics(verifier.candidate if verifier else None)
    verifier_ci = _interval(verifier.baseline, verifier.candidate) if verifier else None
    shared_gates = {
        "recall_gain": _gate(
            recall.candidate_hits > recall.baseline_hits if recall else None,
            "recall_not_increased" if recall else "missing_recall",
        ),
        "shortlist_depth": _gate(
            recall.baseline_depth == recall.candidate_depth == 100 if recall else None,
            "shortlist_not_100" if recall else "missing_recall",
        ),
        "verifier_gain": _gate(
            verifier_ci["low"] > 0 if verifier_ci else None,
            "verifier_interval_not_positive" if verifier_ci else "missing_verifier",
        ),
        "verifier_precision": _gate(
            verifier_after["precision"] >= 0.033 if verifier_after else None,
            "verifier_precision_below_floor" if verifier_after else "missing_verifier",
        ),
    }
    recall_metrics = (
        None
        if recall is None
        else {
            "gold_count": recall.gold_count,
            "baseline_hits": recall.baseline_hits,
            "candidate_hits": recall.candidate_hits,
            "baseline_recall": recall.baseline_hits / recall.gold_count,
            "candidate_recall": recall.candidate_hits / recall.gold_count,
            "baseline_depth": recall.baseline_depth,
            "candidate_depth": recall.candidate_depth,
        }
    )
    by_arm: dict[tuple[str, str | None], Arm] = {
        (arm.consumer, arm.model): arm for arm in bundle.arms
    }
    cards = []
    for identity in ARMS:
        arm = by_arm.get(identity)
        before, after = (arm.baseline, arm.candidate) if arm else (None, None)
        consumer_ci = _interval(before, after)
        gates = dict(shared_gates)
        missing_pair = [
            reason
            for run, reason in ((before, "missing_baseline"), (after, "missing_candidate"))
            if run is None
        ]
        gates["consumer_gain"] = (
            {"status": "missing", "reasons": missing_pair}
            if missing_pair
            else _gate(
                consumer_ci is not None and consumer_ci["low"] > 0, "consumer_interval_not_positive"
            )
        )
        parity = arm.parity if arm else None
        parity_metrics = None
        if parity is not None:
            baseline_digests = {row.item_key: row.output_sha256 for row in parity.baseline}
            changed = sum(
                row.output_sha256 != baseline_digests[row.item_key] for row in parity.candidate
            )
            parity_metrics = {
                "observed_items": len(baseline_digests),
                "changed_observations": changed,
                "projection_version": PROJECTION,
                "flag_enabled": False,
            }
        gates["disabled_flag_parity"] = _gate(
            parity_metrics["changed_observations"] == 0 if parity_metrics else None,
            "disabled_outputs_changed" if parity_metrics else "missing_parity",
        )
        controls_available = (
            bundle.cohort.no_match_count > 0
            and before is not None
            and after is not None
            and verifier is not None
        )
        gates["no_match_metrics"] = _gate(
            True if controls_available else None, "missing_no_match_metrics"
        )
        states = [gate["status"] for gate in gates.values()]
        cards.append(
            {
                "consumer": identity[0],
                "model": identity[1],
                "status": "fail"
                if "fail" in states
                else "missing"
                if "missing" in states
                else "pass",
                "complete": "missing" not in states,
                "adoption_authorized": False,
                "baseline_source_sha256": before.source_sha256 if before else None,
                "candidate_source_sha256": after.source_sha256 if after else None,
                "r14_reference_sha256": before.r14_reference_sha256 if before else None,
                "baseline_metrics": _metrics(before),
                "candidate_metrics": _metrics(after),
                "consumer_f1_interval": consumer_ci,
                "parity": parity_metrics,
                "gates": gates,
            }
        )
    return {
        "schema_version": 1,
        "evidence_kind": bundle.evidence_kind,
        "input_sha256": input_sha256,
        "adoption_authorized": False,
        "limits": list(LIMITS),
        "binding": bundle.binding.model_dump(),
        "lever_sha256": bundle.lever_sha256,
        "shared": {
            "recall": recall_metrics,
            "verifier_baseline_metrics": verifier_before,
            "verifier_candidate_metrics": verifier_after,
            "verifier_f1_interval": verifier_ci,
            "verifier_identity": bundle.verifier_identity.model_dump(),
            "verifier_baseline_source_sha256": verifier.baseline.source_sha256
            if verifier
            else None,
            "verifier_candidate_source_sha256": verifier.candidate.source_sha256
            if verifier
            else None,
            "gates": shared_gates,
        },
        "scorecards": cards,
    }
