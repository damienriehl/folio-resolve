"""Aggregate-only boundary for the owner-authorized private recall experiment."""

from __future__ import annotations

import contextlib
import io
import json
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .clusters import assert_no_surfaces, surface_strings
from .experiment import (
    DEFAULT_EXPERIMENTS_LOG,
    DEFAULT_PENDING_PATH,
    RECORD_ATTEMPT,
    load_raw_records,
)
from .experiment import (
    main as experiment_main,
)
from .splits import DEFAULT_GOLD_DIR, GoldSet, SplitIntegrityError, load_gold, load_split_manifest

DEFAULT_PRIVATE_GOLD = DEFAULT_GOLD_DIR / "gold_v3.jsonl"


class PrivateRecallError(RuntimeError):
    """Fail-closed error whose message is never emitted by the private runner."""


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PrivateRecallError(f"missing aggregate mapping: {name}")
    return value


def _number(value: object, name: str) -> float:
    if not isinstance(value, int | float):
        raise PrivateRecallError(f"missing aggregate number: {name}")
    return float(value)


def _metrics(value: object, name: str) -> dict[str, float]:
    metrics = _mapping(value, name)
    return {
        key: _number(metrics.get(key), f"{name}.{key}") for key in ("precision", "recall", "f1")
    }


def aggregate_payload(
    record: Mapping[str, object], *, elapsed_seconds: float | None = None
) -> dict[str, object]:
    """Project an experiment record to the only schema allowed to leave this process."""
    before = _mapping(record.get("scores_before"), "scores_before")
    after = _mapping(record.get("scores_after"), "scores_after")
    tune_before = _metrics(before.get("tune"), "scores_before.tune")
    tune_after = _metrics(after.get("tune"), "scores_after.tune")
    firm2_before = _metrics(
        _mapping(before.get("firm2"), "scores_before.firm2").get("aggregate"),
        "scores_before.firm2.aggregate",
    )
    firm2_after = _metrics(
        _mapping(after.get("firm2"), "scores_after.firm2").get("aggregate"),
        "scores_after.firm2.aggregate",
    )
    tripwire = _mapping(record.get("tripwire"), "tripwire")
    changed = dict(_mapping(tripwire.get("breakdown"), "tripwire.breakdown"))
    ci = dict(_mapping(tripwire.get("ci"), "tripwire.ci"))

    payload: dict[str, object] = {
        "status": "measured",
        "attempt_id": record.get("attempt_id"),
        "commit_sha": record.get("commit_sha"),
        "gold_version": record.get("gold_version"),
        "ontology_hash": record.get("ontology_hash"),
        "config_hash": record.get("config_hash"),
        "tune": {
            "before": tune_before,
            "after": tune_after,
            "delta_f1": round(tune_after["f1"] - tune_before["f1"], 6),
        },
        "firm2": {
            "before": firm2_before,
            "after": firm2_after,
            "delta_f1": round(firm2_after["f1"] - firm2_before["f1"], 6),
            "changed": changed,
        },
        "ae4": {
            "flagged": tripwire.get("flagged"),
            "ci_negative": tripwire.get("ci_negative"),
            "any_regression": tripwire.get("any_regression"),
            "ci": ci,
        },
        "decision": record.get("decision"),
        "reason": record.get("reason"),
    }
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = round(elapsed_seconds, 3)
    return payload


def _captured_experiment(argv: Sequence[str]) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = experiment_main(argv)
    if status != 0:
        raise PrivateRecallError("experiment command failed")


def _matching_split_manifest(gold: GoldSet) -> Path:
    """Select a split that validates against this gold without exposing its path or contents."""
    matches: list[Path] = []
    for candidate in sorted(DEFAULT_GOLD_DIR.glob("split_manifest*.json")):
        try:
            load_split_manifest(candidate, gold)
        except SplitIntegrityError:
            continue
        matches.append(candidate)
    if not matches:
        raise PrivateRecallError("no split manifest matches the selected gold")
    return matches[-1]


def run_private_recall(*, gold: Path = DEFAULT_PRIVATE_GOLD) -> dict[str, object]:
    """Run before/after scoring and return only a leak-checked aggregate projection."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise PrivateRecallError("PYTHONHASHSEED must be 0")
    if DEFAULT_PENDING_PATH.exists():
        raise PrivateRecallError("an experiment is already pending")

    started = time.perf_counter()
    gold_set = load_gold(gold)
    split_manifest = _matching_split_manifest(gold_set)
    common = ["--gold", str(gold), "--split-manifest", str(split_manifest)]
    _captured_experiment(
        [
            "start",
            *common,
            "--hypothesis",
            (
                "Multi-strategy label, prefix, stem, definition, and ancestor recall will move "
                "candidate-gap-unreachable concepts into the committed answer set without a "
                "Firm-2 regression."
            ),
            "--cluster-targeted",
            "candidate_gap_unreachable",
            "--cluster-size",
            "337",
        ]
    )
    _captured_experiment(
        [
            "finish",
            *common,
            "--multi-strategy-recall",
            "--decision",
            "auto",
            "--reason",
            "automatic aggregate gate",
        ]
    )

    records = load_raw_records(DEFAULT_EXPERIMENTS_LOG)
    attempts = [record for record in records if record.get("record_type") == RECORD_ATTEMPT]
    if not attempts:
        raise PrivateRecallError("experiment record missing")
    payload = aggregate_payload(attempts[-1], elapsed_seconds=time.perf_counter() - started)
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert_no_surfaces(rendered, surface_strings(gold_set), what="private recall aggregate output")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Emit exactly one aggregate JSON object, or a content-free failure code."""
    del argv
    try:
        payload = run_private_recall()
    except BaseException as error:  # fail closed: never print a data-bearing exception message
        print(json.dumps({"status": "failed", "error_code": type(error).__name__}, sort_keys=True))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
