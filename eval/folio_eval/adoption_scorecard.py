"""Offline, descriptive adoption prerequisites; never an adoption decision.

Only generated fixtures or explicitly supplied bound evidence are read. Existing
R11/R17 decisions, consumers, and frozen evidence validators are not modified.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .leakcheck import Manifest, _manifest_from_json, build_manifest, scan_json_value, scan_text
from .recall_consumers import REQUIRED_PUBLISHED_ARMS
from .report import PairedItem, bootstrap_ci, f1_delta
from .score import MicroCounts

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
            if self.verifier is not None and (
                sum(row.tp for row in self.verifier.baseline.scoreable) > recall.baseline_hits
                or sum(row.tp for row in self.verifier.candidate.scoreable) > recall.candidate_hits
            ):
                raise ValueError("recall_verifier_counts")
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
    counts = MicroCounts(tp=tp, fp=fp, fn=fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "items": len(run.scoreable),
        "precision": counts.precision,
        "recall": counts.recall,
        "f1": counts.f1,
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


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures/adoption-scorecard-v1.json"
FIXTURE_SHA256 = "e18574c0226ab5b946709e8c1a09cda348dd8d8b79f8d5e1f404de65063d1d12"


class OutputError(ValueError):
    """Fixed CLI/publication reason, without external exception details."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise OutputError("invalid_arguments")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Consumer adoption scorecards",
        "",
        f"Evidence: `{report['evidence_kind']}`.",
        f"Input SHA-256: `{report['input_sha256']}`.",
        "",
        *[f"- {line}" for line in report["limits"]],
        "",
    ]

    def metrics(before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
        lines.extend(
            [
                "| Run | Precision | Recall | Micro-F1 | No-match FP items / controls | No-match rate |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for name, value in [("Baseline", before), ("Candidate", after)]:
            if value is None:
                lines.append(f"| {name} | missing | missing | missing | missing | missing |")
            else:
                control = value["no_match"]
                rate = repr(control["rate"]) if control["rate"] is not None else "unavailable"
                lines.append(
                    f"| {name} | {value['precision']!r} | {value['recall']!r} | {value['f1']!r} | "
                    f"{control['false_positive_items']} / {control['items']} | {rate} |"
                )
        lines.append("")

    def interval(value: dict[str, Any] | None) -> None:
        if value is None:
            lines.extend(["Paired micro-F1 gain: missing.", ""])
        else:
            lines.extend(
                [
                    f"Paired micro-F1 gain: {value['point']!r}; two-sided 95% interval "
                    f"[{value['low']!r}, {value['high']!r}]. "
                    f"Item percentile bootstrap: {value['n_units']} units, {value['n_resamples']} "
                    f"resamples, seed {value['seed']}, alpha {value['alpha']}.",
                    "",
                ]
            )

    shared = report["shared"]
    lines.extend(["## Shared retrieval prerequisites", ""])
    recall = shared["recall"]
    if recall is None:
        lines.extend(["Shortlist recall and depth: missing.", ""])
    else:
        lines.extend(
            [
                f"Gold hits: {recall['baseline_hits']} → {recall['candidate_hits']} / {recall['gold_count']}. "
                f"Recall: {recall['baseline_recall']!r} → {recall['candidate_recall']!r}. "
                f"Shortlist depth: {recall['baseline_depth']} → {recall['candidate_depth']}.",
                "",
            ]
        )
    metrics(shared["verifier_baseline_metrics"], shared["verifier_candidate_metrics"])
    interval(shared["verifier_f1_interval"])
    for card in report["scorecards"]:
        lines.extend(
            [
                f"## {card['consumer']} / {card['model'] or 'deterministic'}",
                "",
                f"Mechanical result: **{card['status']}**. Evidence complete: **{str(card['complete']).lower()}**.",
                "",
            ]
        )
        metrics(card["baseline_metrics"], card["candidate_metrics"])
        interval(card["consumer_f1_interval"])
        if card["parity"]:
            lines.extend(
                [
                    f"Flag-off parity: {card['parity']['changed_observations']} changed / "
                    f"{card['parity']['observed_items']} observed results ({PROJECTION}).",
                    "",
                ]
            )
        lines.extend(["| Prerequisite | Result | Unmet reason |", "| --- | --- | --- |"])
        for name, gate in card["gates"].items():
            lines.append(f"| {name} | {gate['status']} | {', '.join(gate['reasons']) or '—'} |")
        lines.append("")
    return "\n".join(lines)


def _publish_directory(stage: Path, destination: Path) -> None:
    """Atomic Linux no-replace rename; fail closed when the primitive is unavailable.

    Plain POSIX rename can replace an empty directory created by another writer
    after preflight. RENAME_NOREPLACE preserves that evidence destination as well.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError("atomic publication unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1) != 0:
        raise OSError(ctypes.get_errno(), "atomic publication failed")


def publish_report(
    report: dict[str, Any], destination: Path, manifest: Manifest, salt: bytes
) -> None:
    """Scan both complete renderings before staging; preserve all prior artifacts."""
    if os.path.lexists(destination):
        raise OutputError("destination_occupied")
    json_text = (
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )
    markdown = render_markdown(report)
    if (
        scan_json_value(report, manifest, salt)
        or scan_text(json_text, manifest, salt)
        or scan_text(markdown, manifest, salt)
    ):
        raise OutputError("leak_check_failed")
    # Only stage in an existing parent on the same filesystem. A failed stage is
    # retained for inspection; no cleanup can delete pre-existing evidence.
    stage = Path(tempfile.mkdtemp(prefix=".adoption-scorecard-", dir=destination.parent))
    for name, content in [("scorecards.json", json_text), ("scorecards.md", markdown)]:
        with (stage / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    _publish_directory(stage, destination)


def main(argv: list[str] | None = None) -> int:
    parser = SafeParser(description="Offline descriptive adoption scorecards.", allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture-demo", action="store_true")
    mode.add_argument("--input", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--leak-manifest", type=Path)
    parser.add_argument("--salt-file", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        if args.fixture_demo:
            if any((args.expected_sha256, args.leak_manifest, args.salt_file)):
                raise OutputError("invalid_arguments")
            bundle = load_bundle(FIXTURE_PATH, FIXTURE_SHA256)
            if bundle.evidence_kind != "generated_fixture":
                raise EvidenceError("invalid_evidence")
            digest = FIXTURE_SHA256
            salt = b"public-generated-scorecard-demo-v1"
            manifest = build_manifest(
                ["generated private canary phrase"],
                salt,
                gold_version="generated",
                gold_content_sha256="0" * 64,
            )
        else:
            if not all((args.expected_sha256, args.leak_manifest, args.salt_file)):
                raise OutputError("invalid_arguments")
            digest = args.expected_sha256
            bundle = load_bundle(args.input, digest)
            # The ordinary manifest loader discovers local gold versions. This
            # explicit-input command uses only the supplied manifest's validator.
            manifest = _manifest_from_json(json.loads(args.leak_manifest.read_bytes()))
            salt = args.salt_file.read_bytes()
        if scan_text("\n".join(LIMITS), manifest, salt):
            raise OutputError("leak_check_failed")
        publish_report(evaluate(bundle, digest), args.out_dir, manifest, salt)
    except OutputError as exc:
        print(f"scorecard_error: {exc}", file=sys.stderr)
        return 2
    except EvidenceError:
        print("scorecard_error: invalid_evidence", file=sys.stderr)
        return 2
    except Exception:
        print("scorecard_error: operation_failed", file=sys.stderr)
        return 2
    print("scorecards_written")
    return 0
