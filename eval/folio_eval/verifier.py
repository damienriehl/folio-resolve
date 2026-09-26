"""Replay verifier decisions without model calls or held-out threshold leakage.

Collections pin their provenance and every passage's shortlist. Failures remain in
scoring and pairing, but have no invented probabilities. Cross-fitting optimizes
training-fold micro-F1 using scoreable TP/FP/FN plus one FP per IRI emitted on a
training-fold no-match control (controls add no TP/FN). Both slices use the same
fold assignment; held-out rows never tune their own thresholds. Reported strict
P/R/F1 remains scoreable-only; control FP rate is passages emitting / controls.

The fixed grid is 0, .05, ..., 1 for admission and abstention, with None meaning
never abstain (even at probability 1). Ties prefer never abstaining, then the
highest no-match threshold, then the highest admission threshold. This order
applies only at equal control-aware objective values: abstention wins when it
improves that objective, but never-abstain still wins a tie. Fold assignment
is SHA-256 of ``20260727:item_id``, interpreted as a big-endian integer modulo 5.
A baseline encodes its committed answers as p=1 and other shortlist entries as
p=0, with no_match_p=0; replay it at Thresholds(.5, None), never cross-fit it.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from folio_resolve.pipeline import MatchCandidate

from .answer_rule import AnswerRuleConfig, CandidateLike
from .report import BootstrapCI, f1_delta_ci, pair_items
from .score import ScoreRun, score_items
from .splits import GoldItemRecord
from .synthesize import LoadedCorpus

FOLD_SEED = 20260727
FOLD_COUNT = 5
THRESHOLD_GRID = tuple(i / 20 for i in range(20, -1, -1))


def _probability(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context}: probability must be numeric")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{context}: probability must be in [0, 1]")
    return float(value)


def _object(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context}: expected object")
    return value


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: expected nonempty string")
    return value


def _list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context}: expected list")
    return value


@dataclass(frozen=True, slots=True)
class CandidateProbability:
    iri: str
    p: float


@dataclass(frozen=True, slots=True)
class PassageDecision:
    item_id: str
    kind: str
    shortlist: tuple[str, ...]
    no_match_p: float | None = None
    candidates: tuple[CandidateProbability, ...] = ()
    state: str = "decided"

    def to_json(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "state": self.state,
            "shortlist": list(self.shortlist),
            "no_match_p": self.no_match_p,
            "candidates": [{"iri": c.iri, "p": c.p} for c in self.candidates],
        }

    @classmethod
    def from_json(cls, value: object) -> PassageDecision:
        row = _object(value, "decision")
        item_id = _text(row.get("item_id"), "item_id")
        candidates = []
        for raw in _list(row.get("candidates", []), item_id):
            candidate = _object(raw, item_id)
            candidates.append(
                CandidateProbability(
                    _text(candidate.get("iri"), item_id),
                    _probability(candidate.get("p"), item_id),
                )
            )
        return cls(
            item_id=item_id,
            kind=_text(row.get("kind"), item_id),
            state=_text(row.get("state"), item_id),
            shortlist=tuple(_text(iri, item_id) for iri in _list(row.get("shortlist"), item_id)),
            no_match_p=(
                None if row.get("no_match_p") is None else _probability(row["no_match_p"], item_id)
            ),
            candidates=tuple(candidates),
        )


@dataclass(frozen=True, slots=True)
class DecisionCollection:
    arm_name: str
    model_id: str
    prompt_template_sha256: str
    corpus_content_sha256: str
    nomatch_content_sha256: str
    adapter_source: str
    adapter_sha256: str
    shortlist_depth: int
    decisions: tuple[PassageDecision, ...]
    schema_version: int = 1
    lever_scope: str = "adapter_only"

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "lever_scope": self.lever_scope,
            "arm_name": self.arm_name,
            "model_id": self.model_id,
            "prompt_template_sha256": self.prompt_template_sha256,
            "corpus_content_sha256": self.corpus_content_sha256,
            "nomatch_content_sha256": self.nomatch_content_sha256,
            "adapter_source": self.adapter_source,
            "adapter_sha256": self.adapter_sha256,
            "shortlist_depth": self.shortlist_depth,
            "decisions": [d.to_json() for d in self.decisions],
        }

    @classmethod
    def from_json(cls, value: object, corpus: LoadedCorpus) -> DecisionCollection:
        row = _object(value, "collection")
        depth = row.get("shortlist_depth")
        if type(depth) is not int:
            raise ValueError("shortlist_depth must be an integer")
        if type(row.get("schema_version")) is not int or row["schema_version"] != 1:
            raise ValueError("unsupported schema_version")
        result = cls(
            arm_name=_text(row.get("arm_name"), "arm_name"),
            model_id=_text(row.get("model_id"), "model_id"),
            prompt_template_sha256=_text(row.get("prompt_template_sha256"), "prompt hash"),
            corpus_content_sha256=_text(row.get("corpus_content_sha256"), "corpus hash"),
            nomatch_content_sha256=_text(row.get("nomatch_content_sha256"), "nomatch hash"),
            adapter_source=_text(row.get("adapter_source"), "adapter_source"),
            adapter_sha256=_text(row.get("adapter_sha256"), "adapter hash"),
            shortlist_depth=depth,
            decisions=tuple(
                PassageDecision.from_json(d) for d in _list(row.get("decisions"), "decisions")
            ),
            lever_scope=_text(row.get("lever_scope"), "lever_scope"),
        )
        result.validate(corpus)
        return result

    def validate(self, corpus: LoadedCorpus) -> None:
        if self.schema_version != 1 or self.lever_scope != "adapter_only":
            raise ValueError("unsupported collection schema or lever_scope")
        for name in ("arm_name", "model_id", "adapter_source"):
            _text(getattr(self, name), name)
        for name in (
            "prompt_template_sha256",
            "corpus_content_sha256",
            "nomatch_content_sha256",
            "adapter_sha256",
        ):
            digest = _text(getattr(self, name), name)
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"{name}: expected lowercase SHA-256")
        if (
            self.corpus_content_sha256 != corpus.manifest.content_sha256
            or self.nomatch_content_sha256 != corpus.manifest.nomatch_content_sha256
        ):
            raise ValueError("collection corpus content hash mismatch")
        if type(self.shortlist_depth) is not int or self.shortlist_depth < 1:
            raise ValueError("shortlist_depth must be a positive integer")
        expected = {item.item_id: "scoreable" for item in corpus.scoreable_items}
        expected.update({item.item_id: "nomatch" for item in corpus.nomatch_items})
        if len(expected) != len(corpus.scoreable_items) + len(corpus.nomatch_items):
            raise ValueError("duplicate corpus item_id")
        ids = [d.item_id for d in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate decision item_id")
        if set(ids) != set(expected):
            raise ValueError(
                f"item-id coverage: missing={sorted(set(expected) - set(ids))}, "
                f"extra={sorted(set(ids) - set(expected))}"
            )
        for d in self.decisions:
            if d.kind != expected[d.item_id]:
                raise ValueError(f"{d.item_id}: incorrect item kind")
            if len(d.shortlist) > self.shortlist_depth or len(set(d.shortlist)) != len(d.shortlist):
                raise ValueError(f"{d.item_id}: invalid shortlist length or duplicates")
            for iri in d.shortlist:
                _text(iri, d.item_id)
            if d.state == "failed":
                if d.no_match_p is not None or d.candidates:
                    raise ValueError(f"{d.item_id}: failed record must not carry probabilities")
                continue
            if d.state != "decided":
                raise ValueError(f"{d.item_id}: unsupported decision state")
            _probability(d.no_match_p, d.item_id)
            iris = [candidate.iri for candidate in d.candidates]
            if len(iris) != len(set(iris)) or set(iris) != set(d.shortlist):
                raise ValueError(f"{d.item_id}: candidates must cover exactly its shortlist")
            for candidate in d.candidates:
                _probability(candidate.p, d.item_id)


def load_collection(path: Path, corpus: LoadedCorpus) -> DecisionCollection:
    return DecisionCollection.from_json(json.loads(path.read_text(encoding="utf-8")), corpus)


@dataclass(frozen=True, slots=True)
class Thresholds:
    admit: float
    nomatch: float | None

    def __post_init__(self) -> None:
        _probability(self.admit, "admission threshold")
        if self.nomatch is not None:
            _probability(self.nomatch, "no-match threshold")


def emit(decision: PassageDecision, thresholds: Thresholds) -> tuple[str, ...]:
    """Gold-blind, inclusive probability admission, without a rank budget."""
    if decision.state == "failed":
        return ()
    if decision.state != "decided" or decision.no_match_p is None:
        raise ValueError(f"{decision.item_id}: invalid decision")
    if thresholds.nomatch is not None and decision.no_match_p >= thresholds.nomatch:
        return ()
    return tuple(c.iri for c in decision.candidates if c.p >= thresholds.admit)


def fold_for_item(item_id: str) -> int:
    digest = hashlib.sha256(f"{FOLD_SEED}:{item_id}".encode()).digest()
    return int.from_bytes(digest, "big") % FOLD_COUNT


def select_thresholds(
    collection: DecisionCollection, corpus: LoadedCorpus
) -> tuple[Thresholds, ...]:
    """Maximize 2*TP/(2*TP+FP+FN) on the other four folds only.

    TP/FP/FN come from scoreable rows, with one additional FP per IRI emitted
    on a training-fold control; controls add no TP/FN. Controls use fold_for_item
    too, so their own fold cannot tune their thresholds. Reported metrics remain
    unchanged. Equal objectives prefer never abstaining, then highest no-match
    threshold, then highest admission threshold (first grid entry wins).
    """
    collection.validate(corpus)
    decisions = {d.item_id: d for d in collection.decisions}
    grid = tuple(
        Thresholds(admit, nomatch)
        for nomatch in (None, *THRESHOLD_GRID)
        for admit in THRESHOLD_GRID
    )
    selected = []
    for fold in range(FOLD_COUNT):
        training = [item for item in corpus.scoreable_items if fold_for_item(item.item_id) != fold]
        controls = [item for item in corpus.nomatch_items if fold_for_item(item.item_id) != fold]
        best, best_f1 = grid[0], -1.0
        for thresholds in grid:
            tp = fp = fn = 0
            for item in training:
                prediction = set(emit(decisions[item.item_id], thresholds))
                tp += len(prediction & item.gold_iris)
                fp += len(prediction - item.gold_iris)
                fn += len(item.gold_iris - prediction)
            fp += sum(len(emit(decisions[item.item_id], thresholds)) for item in controls)
            denominator = 2 * tp + fp + fn
            f1 = 2 * tp / denominator if denominator else 0.0
            if f1 > best_f1:
                best, best_f1 = thresholds, f1
        selected.append(best)
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class CalibrationMetrics:
    count: int
    brier: float | None
    ece: float | None


def calibration_metrics(observations: Iterable[tuple[float, bool]]) -> CalibrationMetrics:
    """Equal-width bins [0,.1), ..., [.9,1]; empty populations are undefined."""
    pairs = tuple(observations)
    if not pairs:
        return CalibrationMetrics(0, None, None)
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(10)]
    for p, outcome in pairs:
        _probability(p, "calibration")
        bins[min(int(p * 10), 9)].append((p, outcome))
    brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    ece = sum(abs(sum(p - y for p, y in bucket)) for bucket in bins) / len(pairs)
    return CalibrationMetrics(len(pairs), brier, ece)


@dataclass(frozen=True, slots=True)
class VerifierScoreResult:
    run: ScoreRun
    nomatch_fp_rate: float
    failed_count: int
    candidate_calibration: CalibrationMetrics
    nomatch_calibration: CalibrationMetrics
    thresholds_by_fold: tuple[Thresholds, ...]


def score_collection(
    collection: DecisionCollection,
    corpus: LoadedCorpus,
    *,
    thresholds: Thresholds | None = None,
) -> VerifierScoreResult:
    """Score held-out emissions, or explicitly replay a fixed baseline answer set.

    Only admitted candidates enter score_items. Its answer rule has no additional
    effective cutoff or cap; ranked diagnostics therefore describe emissions,
    not original retrieval recall. Calibration includes successful decisions on
    both slices, with no-match targets 1 on controls and 0 on scoreable passages.
    """
    collection.validate(corpus)
    chosen = (
        (thresholds,) * FOLD_COUNT
        if thresholds is not None
        else select_thresholds(collection, corpus)
    )
    predictions = {
        d.item_id: emit(d, chosen[fold_for_item(d.item_id)]) for d in collection.decisions
    }

    def predict(item: GoldItemRecord) -> Sequence[CandidateLike]:
        return [
            MatchCandidate(iri=iri, label="", score=100.0, extraction_path="verifier")
            for iri in predictions[item.item_id]
        ]

    config = AnswerRuleConfig(
        threshold=0.0,
        top_k=max(1, collection.shortlist_depth),
        rationale="verifier admissions already applied; no further cap",
    )
    run = score_items(
        corpus.gold_item_records(),
        predict,
        config=config,
        slice_name="synthetic",
        keep_ranked=collection.shortlist_depth,
    )
    nomatch_fp = sum(bool(predictions[item.item_id]) for item in corpus.nomatch_items)
    gold = {item.item_id: item.gold_iris for item in corpus.scoreable_items}
    successful = [d for d in collection.decisions if d.state == "decided"]
    return VerifierScoreResult(
        run=run,
        nomatch_fp_rate=(nomatch_fp / len(corpus.nomatch_items) if corpus.nomatch_items else 0.0),
        failed_count=sum(d.state == "failed" for d in collection.decisions),
        candidate_calibration=calibration_metrics(
            (c.p, c.iri in gold.get(d.item_id, frozenset()))
            for d in successful
            for c in d.candidates
        ),
        nomatch_calibration=calibration_metrics(
            (d.no_match_p, d.kind == "nomatch") for d in successful if d.no_match_p is not None
        ),
        thresholds_by_fold=chosen,
    )


@dataclass(frozen=True, slots=True)
class PairedVerifierResult:
    candidate: VerifierScoreResult
    baseline: VerifierScoreResult
    delta: BootstrapCI


BASELINE_THRESHOLDS = Thresholds(0.5, None)


def compare_collections(
    collection: DecisionCollection,
    baseline: DecisionCollection,
    corpus: LoadedCorpus,
    *,
    thresholds: Thresholds | None = None,
    baseline_thresholds: Thresholds = BASELINE_THRESHOLDS,
) -> PairedVerifierResult:
    """Paired strict F1 on scoreable items; verify full coverage including controls."""
    if {d.item_id for d in collection.decisions} != {d.item_id for d in baseline.decisions}:
        raise ValueError("paired collection item-id sets differ")
    for field in (
        "shortlist_depth",
        "adapter_source",
        "adapter_sha256",
        "corpus_content_sha256",
        "nomatch_content_sha256",
    ):
        if getattr(collection, field) != getattr(baseline, field):
            raise ValueError(f"paired collection {field} differs")
    baseline_shortlists = {d.item_id: d.shortlist for d in baseline.decisions}
    for decision in collection.decisions:
        if decision.shortlist != baseline_shortlists[decision.item_id]:
            raise ValueError(f"paired collection item {decision.item_id}: shortlist differs")
    after = score_collection(collection, corpus, thresholds=thresholds)
    before = score_collection(baseline, corpus, thresholds=baseline_thresholds)
    before_ids = {s.item_id for s in before.run.item_scores}
    after_ids = {s.item_id for s in after.run.item_scores}
    if before_ids != after_ids:
        raise ValueError("paired scoreable item-id sets differ")
    paired = pair_items(before.run.item_scores, after.run.item_scores)
    if len(paired) != len(before.run.item_scores):
        raise ValueError("pair_items dropped scoreable items")
    delta = f1_delta_ci(
        before.run.item_scores, after.run.item_scores, n_resamples=2000, seed=20260727
    )
    return PairedVerifierResult(after, before, delta)
