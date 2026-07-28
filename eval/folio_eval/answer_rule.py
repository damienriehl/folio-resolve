"""The committed answer rule (U3, KTD2).

The pipeline returns *every* survivor above ``score_floor=45.0``. Scoring that raw list as the
answer makes precision near zero by construction, so KTD2 defines the **committed answer set**:

    candidates whose calibrated probability is >= ``threshold``, hard-capped at the top ``k``.

Three properties this module exists to guarantee:

1. **Gold-count-blind.** Nothing here ever sees the gold set, its size, or any per-item target
   count. :func:`commit_answers` takes candidates and a config; that is the whole input surface.
   Feeding the same candidates through the same config always commits the same set, whatever the
   item's gold happens to be.
2. **Deterministic ordering.** Candidates sort by ``(score desc, iri asc)`` *before* any cutoff
   (KTD7), so ties never depend on the pipeline's internal ordering or on hash randomization.
3. **Versioned and hashed.** :class:`AnswerRuleConfig` serializes to
   ``eval/data/gold/harness_config_v1.json`` and carries its own SHA-256, which every report
   cites. Changing threshold or k is a named iteration change under R9 -- never a silent tweak.

The defaults here are **uncalibrated placeholders** (``threshold=0.5``, ``k=5``,
``calibrated=False``). U4 fits the real threshold/k on tune-slice items only and rewrites the
config; until then every report carries ``calibrated: false`` so no score is mistaken for a
calibrated baseline.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from folio_resolve.calibration import ScoreCalibration

#: Bumped when the *shape* of the config changes (new fields, new semantics).
HARNESS_CONFIG_VERSION = 1

#: Placeholder answer rule. U4 replaces these with values fitted on the tune slice.
UNCALIBRATED_THRESHOLD = 0.5
UNCALIBRATED_TOP_K = 5
UNCALIBRATED_RATIONALE = (
    "placeholder — not fitted. U4 calibrates threshold/k on tune-slice items only "
    "(a candidate is correct iff its IRI is in the item's gold set) and rewrites this file."
)


def _as_float(value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float, str)):
        return float(value)
    raise ValueError(f"expected a number, got {value!r}")


def _as_int(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (int, str)):
        return int(value)
    raise ValueError(f"expected an integer, got {value!r}")


class CandidateLike(Protocol):
    """The slice of ``folio_resolve.pipeline.MatchCandidate`` the answer rule reads."""

    iri: str
    label: str
    score: float
    extraction_path: str
    gated: bool


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """One candidate after deterministic ranking, carrying its calibrated probability."""

    iri: str
    label: str
    score: float
    probability: float
    rank: int
    extraction_path: str = ""
    gated: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "iri": self.iri,
            "label": self.label,
            "score": self.score,
            "probability": self.probability,
            "rank": self.rank,
            "extraction_path": self.extraction_path,
            "gated": self.gated,
        }


@dataclass(frozen=True, slots=True)
class AnswerRuleConfig:
    """The versioned harness config (KTD2): what turns a ranked list into a committed set."""

    threshold: float = UNCALIBRATED_THRESHOLD
    top_k: int = UNCALIBRATED_TOP_K
    #: False until U4 fits it. Reports print this so an uncalibrated number is never read as one.
    calibrated: bool = False
    #: Isotonic ``(raw_score, P(correct))`` steps from ``folio_resolve.calibration``. Empty means
    #: the library's ``score / 100`` fallback prior -- explicitly a placeholder, not a fit.
    calibration_steps: tuple[tuple[float, float], ...] = ()
    rationale: str = UNCALIBRATED_RATIONALE
    config_version: int = HARNESS_CONFIG_VERSION

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be a probability in [0, 1]: {self.threshold}")
        if self.top_k < 1:
            raise ValueError(f"top_k must be >= 1: {self.top_k}")

    # -- serialization ---------------------------------------------------

    def to_json(self) -> dict[str, object]:
        return {
            "calibrated": self.calibrated,
            "calibration_steps": [[x, y] for x, y in self.calibration_steps],
            "config_version": self.config_version,
            "rationale": self.rationale,
            "threshold": self.threshold,
            "top_k": self.top_k,
        }

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> AnswerRuleConfig:
        steps_raw = payload.get("calibration_steps") or []
        if not isinstance(steps_raw, list):
            raise ValueError("calibration_steps must be a list of [score, probability] pairs")
        steps: list[tuple[float, float]] = []
        for entry in steps_raw:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise ValueError(f"malformed calibration step: {entry!r}")
            steps.append((float(entry[0]), float(entry[1])))
        return cls(
            threshold=_as_float(payload.get("threshold"), UNCALIBRATED_THRESHOLD),
            top_k=_as_int(payload.get("top_k"), UNCALIBRATED_TOP_K),
            calibrated=bool(payload.get("calibrated", False)),
            calibration_steps=tuple(steps),
            rationale=str(payload.get("rationale", "")),
            config_version=_as_int(payload.get("config_version"), HARNESS_CONFIG_VERSION),
        )

    def canonical_json(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def content_sha256(self) -> str:
        """The hash every report cites; changing any field changes it."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    # -- behavior --------------------------------------------------------

    def calibration(self) -> ScoreCalibration:
        """The library calibration this config pins (empty steps = the ``score/100`` prior)."""
        return ScoreCalibration(list(self.calibration_steps) or None)


DEFAULT_CONFIG_FILENAME = f"harness_config_v{HARNESS_CONFIG_VERSION}.json"


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomic write, mirroring ``src/folio_resolve/annotate/feedback_store.py``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def write_config(config: AnswerRuleConfig, path: Path) -> str:
    """Serialize the config deterministically; returns its SHA-256."""
    _atomic_write_text(path, config.canonical_json())
    return config.content_sha256()


def load_config(path: Path) -> AnswerRuleConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"harness config is not a JSON object: {path}")
    return AnswerRuleConfig.from_json(payload)


def load_or_create_config(path: Path) -> AnswerRuleConfig:
    """Read the pinned config, writing the uncalibrated placeholder if none exists yet."""
    if path.exists():
        return load_config(path)
    config = AnswerRuleConfig()
    write_config(config, path)
    return config


# --------------------------------------------------------------------------------------
# Ranking and committing
# --------------------------------------------------------------------------------------


def rank_candidates(
    candidates: Iterable[CandidateLike], config: AnswerRuleConfig
) -> list[RankedCandidate]:
    """Dedupe by IRI (best score wins), sort by ``(score desc, IRI asc)``, attach probabilities.

    This is the *threshold-free* ranked list: recall@k and the PR curve are computed from it, so
    it must never be filtered by the answer rule.
    """
    best: dict[str, CandidateLike] = {}
    for candidate in candidates:
        current = best.get(candidate.iri)
        if current is None or candidate.score > current.score:
            best[candidate.iri] = candidate
    calibration = config.calibration()
    ordered = sorted(best.values(), key=lambda c: (-c.score, c.iri))
    return [
        RankedCandidate(
            iri=candidate.iri,
            label=candidate.label,
            score=candidate.score,
            probability=calibration.probability(candidate.score),
            rank=position,
            extraction_path=getattr(candidate, "extraction_path", ""),
            gated=bool(getattr(candidate, "gated", False)),
        )
        for position, candidate in enumerate(ordered, start=1)
    ]


def commit_from_ranked(
    ranked: Sequence[RankedCandidate], config: AnswerRuleConfig
) -> list[RankedCandidate]:
    """Apply the KTD2 rule to an already-ranked list: probability bar, then the hard top-k cap."""
    kept = [candidate for candidate in ranked if candidate.probability >= config.threshold]
    return kept[: config.top_k]


def commit_answers(
    candidates: Iterable[CandidateLike], config: AnswerRuleConfig
) -> list[RankedCandidate]:
    """The committed answer set for one item. Sees candidates and config -- never gold."""
    return commit_from_ranked(rank_candidates(candidates, config), config)
