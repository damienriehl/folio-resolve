"""Offline Stage 1 ceiling report and mechanical go/no-go.

Plan Success Criteria, "Abstention works: the no-match false-positive rate on
controls falls well below the current 30 of 30", is read as strictly below the
paired baseline rate and at most 0.5. Positive paired strict-F1 significance and
nondecreasing strict recall are also required. Laya retention is a later gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from .experiment import (
    DEFAULT_PENDING_PATH,
    DEFAULT_SYNTHETIC_EXPERIMENTS_LOG,
    ExperimentRecord,
    ItemOutcome,
    SliceOutcome,
    finish_attempt,
    start_attempt,
)
from .leakcheck import Manifest, load_manifest, scan_json_value, scan_text
from .selftest import ensure_hash_seed
from .synthesize import LoadedCorpus, load_corpus
from .verifier import (
    DecisionCollection,
    PairedVerifierResult,
    VerifierScoreResult,
    compare_collections,
    load_collection,
)

ROOT = Path(__file__).resolve().parents[2]
RULE = (
    'Plan Success Criteria — "Abstention works": "well below the current 30 of 30" '
    "is operationalized as a no-match FP rate strictly below baseline and at most 0.5. "
    "Go also requires the paired strict-F1 95% interval entirely above zero and "
    "strict recall at least baseline recall. Laya retention is a later-stage criterion."
)
CAVEAT = (
    "The grader mix below is computed from corpus provenance. A Codex verifier may "
    "agree with gold partly through shared model tendencies and overstate the ceiling."
)
EXPANDED = "Unavailable: the U2 scorer supplies no owner-approved expanded metric."
HYPOTHESIS = "Measure the calibrated verifier ceiling against the paired baseline."


def evaluate_verdict(result: PairedVerifierResult) -> dict[str, Any]:
    """Return all deciding values at full precision, including every failed gate."""
    arm, baseline = result.candidate, result.baseline
    failures = []
    if not result.delta.low > 0:
        failures.append("significant_f1_lift")
    if not (arm.nomatch_fp_rate < baseline.nomatch_fp_rate and arm.nomatch_fp_rate <= 0.5):
        failures.append("abstention_works")
    if not arm.run.overall.recall >= baseline.run.overall.recall:
        failures.append("recall_holds")
    return {
        "verdict": "no-go" if failures else "go",
        "failing_criteria": failures,
        "paired_delta": asdict(result.delta),
        "arm_strict_f1": arm.run.overall.f1,
        "baseline_strict_f1": baseline.run.overall.f1,
        "arm_strict_recall": arm.run.overall.recall,
        "baseline_strict_recall": baseline.run.overall.recall,
        "arm_nomatch_fp_rate": arm.nomatch_fp_rate,
        "baseline_nomatch_fp_rate": baseline.nomatch_fp_rate,
        "nomatch_fp_rate_cap": 0.5,
    }


def _score_payload(score: VerifierScoreResult) -> dict[str, Any]:
    counts = score.run.overall
    return {
        "strict": {
            "precision": counts.precision,
            "recall": counts.recall,
            "f1": counts.f1,
            "tp": counts.tp,
            "fp": counts.fp,
            "fn": counts.fn,
        },
        "nomatch_fp_rate": score.nomatch_fp_rate,
        "failed_item_count": score.failed_count,
        "cuts_by_fold": [
            {"fold": fold, "admission_cut": cut.admit, "nomatch_cut": cut.nomatch}
            for fold, cut in enumerate(score.thresholds_by_fold)
        ],
        "candidate_calibration": asdict(score.candidate_calibration),
        "nomatch_calibration": asdict(score.nomatch_calibration),
        "expanded_metric": EXPANDED,
    }


def grader_mix(corpus: LoadedCorpus) -> dict[str, Any]:
    """Count scored-cohort votes; report missing votes without exposing corpus text.

    Passage count includes scored items without votes, but mix patterns do not.
    The corpus-wide missing count excludes the separate no-match control slice.
    """
    totals: Counter[str] = Counter()
    patterns: Counter[str] = Counter()
    scored_items = corpus.scoreable_items
    scored_without_votes = 0
    for item in scored_items:
        votes = cast(Sequence[Mapping[str, Any]], item.provenance.get("grader_votes") or ())
        if not votes:
            scored_without_votes += 1
            continue
        counts = Counter(str(vote["model_family"]) for vote in votes)
        totals.update(counts)
        patterns[", ".join(f"{family}: {count}" for family, count in sorted(counts.items()))] += 1
    return {
        "passage_count": len(scored_items),
        "votes_by_family": dict(sorted(totals.items())),
        "passages_by_mix": dict(sorted(patterns.items())),
        "corpus_rows_without_votes": sum(
            not item.provenance.get("grader_votes") for item in corpus.corpus_items
        ),
        "scored_items_without_votes": scored_without_votes,
    }


def validate_depth_report(
    depth: Mapping[str, Any], corpus: LoadedCorpus, baseline: DecisionCollection
) -> None:
    """Bind the retrieval curve to the loaded corpus and deterministic baseline."""
    corpus_fields = {
        "corpus_content_sha256": corpus.manifest.content_sha256,
        "nomatch_content_sha256": corpus.manifest.nomatch_content_sha256,
        "ontology_cache_sha256": corpus.manifest.ontology_cache_sha256,
        "answer_rule_config_sha256": corpus.manifest.answer_rule_config_sha256,
    }
    baseline_fields = {
        "chosen_n": baseline.shortlist_depth,
        "corpus_content_sha256": baseline.corpus_content_sha256,
        "nomatch_content_sha256": baseline.nomatch_content_sha256,
        "adapter_source": baseline.adapter_source,
        "adapter_sha256": baseline.adapter_sha256,
        # The depth writer uses the answer-rule config hash as the baseline prompt hash.
        "answer_rule_config_sha256": baseline.prompt_template_sha256,
    }
    for source, fields in (("corpus", corpus_fields), ("baseline", baseline_fields)):
        for field, expected in fields.items():
            if depth.get(field) != expected:
                raise ValueError(f"depth report {field} differs from {source}")


def build_report(
    result: PairedVerifierResult,
    depth: Mapping[str, Any],
    mix: Mapping[str, Any],
) -> dict[str, Any]:
    chosen = depth["chosen_n"]
    return {
        "arm": _score_payload(result.candidate),
        "baseline": _score_payload(result.baseline),
        "decision": evaluate_verdict(result),
        "chosen_n": chosen,
        "unreachable_gold_count_at_chosen_n": depth["curve"][str(chosen)]["unreachable_gold_count"],
        "unreachable_gold_count_at_200": depth["unreachable_gold_count_at_200"],
        "grader_mix": dict(mix),
        "grader_caveat": CAVEAT,
        "verdict_rule": RULE,
    }


def render_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Verifier ceiling",
        "",
        f"Verdict: **{payload['decision']['verdict']}**.",
        "",
        RULE,
        "",
        CAVEAT,
        "",
        "Grader mix: " + json.dumps(payload["grader_mix"], sort_keys=True),
        "",
        "| Arm | Strict P | Strict R | Strict F1 | TP | FP | FN | No-match FP rate | Failed items |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("arm", "baseline"):
        score = payload[name]
        strict = score["strict"]
        lines.append(
            f"| {name} | {strict['precision']:.6f} | {strict['recall']:.6f} | "
            f"{strict['f1']:.6f} | {strict['tp']} | {strict['fp']} | {strict['fn']} | "
            f"{score['nomatch_fp_rate']:.6f} | {score['failed_item_count']} |"
        )
    lines.extend(
        [
            "",
            "Full precision evidence, fold cuts, calibration and retrieval ceiling:",
            "",
            "```json",
            json.dumps(payload, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _check_outputs(payload: Mapping[str, Any], manifest: Manifest, salt: bytes) -> str:
    markdown = render_markdown(payload)
    if (
        "thresholds" in json.dumps(payload).lower()
        or "thresholds" in markdown.lower()
        or scan_json_value(payload, manifest, salt)
        or scan_text(markdown, manifest, salt)
    ):
        raise ValueError("leak check failed for ceiling report")
    return markdown


def preflight(manifest: Manifest, salt: bytes) -> None:
    """Render placeholders through the real writer before any corpus work or scoring."""
    score = {
        "strict": dict.fromkeys(("precision", "recall", "f1", "tp", "fp", "fn"), 0),
        "nomatch_fp_rate": 0,
        "failed_item_count": 0,
        "cuts_by_fold": [{"fold": 0, "admission_cut": 0, "nomatch_cut": None}],
        "candidate_calibration": {"count": 0, "brier": None, "ece": None},
        "nomatch_calibration": {"count": 0, "brier": None, "ece": None},
        "expanded_metric": EXPANDED,
    }
    decision = {
        "verdict": "no-go",
        "failing_criteria": ["significant_f1_lift", "abstention_works", "recall_holds"],
        "paired_delta": dict.fromkeys(
            ("point", "low", "high", "n_units", "n_resamples", "seed", "alpha"), 0
        ),
        **dict.fromkeys(
            (
                "arm_strict_f1",
                "baseline_strict_f1",
                "arm_strict_recall",
                "baseline_strict_recall",
                "arm_nomatch_fp_rate",
                "baseline_nomatch_fp_rate",
                "nomatch_fp_rate_cap",
            ),
            0,
        ),
    }
    for verdict in ("go", "no-go"):
        decision["verdict"] = verdict
        _check_outputs(
            {
                "arm": score,
                "baseline": score,
                "decision": decision,
                "chosen_n": 0,
                "unreachable_gold_count_at_chosen_n": 0,
                "unreachable_gold_count_at_200": 0,
                "grader_mix": {
                    "passage_count": 0,
                    "votes_by_family": {"codex": 0, "claude": 0},
                    "passages_by_mix": {"": 0},
                    "corpus_rows_without_votes": 0,
                    "scored_items_without_votes": 0,
                },
                "grader_caveat": CAVEAT,
                "verdict_rule": RULE,
            },
            manifest,
            salt,
        )
    if scan_text(HYPOTHESIS, manifest, salt):
        raise ValueError("leak check failed for experiment prose")


def write_reports(
    payload: Mapping[str, Any],
    output_dir: Path,
    manifest: Manifest,
    salt: bytes,
) -> None:
    markdown = _check_outputs(payload, manifest, salt)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "verifier-ceiling.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "verifier-ceiling.md").write_text(markdown, encoding="utf-8")


def _slice(score: VerifierScoreResult) -> dict[str, SliceOutcome]:
    return {
        "synthetic": SliceOutcome(
            "synthetic",
            tuple(ItemOutcome.from_item_score(item) for item in score.run.item_scores),
            score.run.overall.to_json(),
        )
    }


def record_experiment(
    result: PairedVerifierResult,
    verdict: Mapping[str, Any],
    corpus: LoadedCorpus,
    manifest: Manifest,
    salt: bytes,
    *,
    experiments_log: Path = DEFAULT_SYNTHETIC_EXPERIMENTS_LOG,
    pending_path: Path = DEFAULT_PENDING_PATH,
) -> ExperimentRecord:
    """Use the existing synthetic ledger, retaining paired per-item outcomes."""
    metadata = corpus.manifest
    version = f"synthetic-v{metadata.version}"
    reason = json.dumps(verdict, sort_keys=True)
    if scan_text(reason, manifest, salt):
        raise ValueError("leak check failed for experiment reason")
    start_attempt(
        hypothesis=HYPOTHESIS,
        cluster_targeted="verifier",
        cluster_size=len(corpus.scoreable_items),
        gold_version=0,
        ontology_hash=metadata.ontology_cache_sha256,
        config_hash=metadata.answer_rule_config_sha256,
        surfaces=(),
        manifest_checker=(manifest, salt),
        prior_scores=_slice(result.baseline),
        lever_scope="adapter_only",
        corpus_version=version,
        answer_rule_config_sha256=metadata.answer_rule_config_sha256,
        experiments_log=experiments_log,
        pending_path=pending_path,
    )
    return finish_attempt(
        decision="park",
        reason=reason,
        surfaces=(),
        manifest_checker=(manifest, salt),
        after_scores=_slice(result.candidate),
        corpus_version=version,
        answer_rule_config_sha256=metadata.answer_rule_config_sha256,
        experiments_log=experiments_log,
        pending_path=pending_path,
        ci_resamples=2000,
        ci_seed=20260727,
    )


def require_pristine(root: Path) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
    )
    if status.stdout:
        raise ValueError("experiment recording requires a pristine tree, including untracked files")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "eval/synthetic/verifier/baseline-collection-v1.json",
    )
    parser.add_argument(
        "--depth", type=Path, default=ROOT / "docs/benchmarks/verifier-shortlist-depth.json"
    )
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument(
        "--corpus-manifest", type=Path, default=ROOT / "eval/synthetic/corpus_v1.manifest.json"
    )
    parser.add_argument(
        "--leak-manifest", type=Path, default=ROOT / "eval/synthetic/firm-surface-manifest-v1.json"
    )
    parser.add_argument("--record-experiment", action="store_true")
    args = parser.parse_args(argv)
    ensure_hash_seed()
    manifest, salt = load_manifest(args.leak_manifest), args.salt_file.read_bytes()
    preflight(manifest, salt)
    if args.record_experiment:
        require_pristine(ROOT)
    corpus = load_corpus(args.corpus_manifest)
    arm = load_collection(args.arm, corpus)
    baseline = load_collection(args.baseline, corpus)
    depth = json.loads(args.depth.read_text(encoding="utf-8"))
    validate_depth_report(depth, corpus, baseline)
    if arm.shortlist_depth != depth["chosen_n"] or baseline.shortlist_depth != depth["chosen_n"]:
        raise ValueError("collection depth differs from chosen N")
    result = compare_collections(arm, baseline, corpus)
    payload = build_report(result, depth, grader_mix(corpus))
    _check_outputs(payload, manifest, salt)
    if args.record_experiment:
        record_experiment(result, payload["decision"], corpus, manifest, salt)
    write_reports(payload, ROOT / "docs/benchmarks", manifest, salt)
    return 0
