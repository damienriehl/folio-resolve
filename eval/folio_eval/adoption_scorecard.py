"""Offline, descriptive adoption prerequisites; never an adoption decision.

Only generated fixtures or explicitly supplied bound evidence are read. Existing
R11/R17 decisions, consumers, and frozen evidence validators are not modified.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .recall_consumers import REQUIRED_PUBLISHED_ARMS

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
