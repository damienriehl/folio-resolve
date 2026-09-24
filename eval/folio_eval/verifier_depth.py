"""Offline shortlist recall curve and the attempt-0004 paired baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence, Set
from fractions import Fraction
from pathlib import Path
from typing import Any

from .answer_rule import (
    AnswerRuleConfig,
    CandidateLike,
    commit_from_ranked,
    load_config,
    rank_candidates,
)
from .leakcheck import load_manifest, scan_json_value, scan_text
from .selftest import assert_ontology_pin, ensure_hash_seed
from .synthesize import LoadedCorpus, load_corpus
from .synthetic_checkpoint import build_checkpoint_fingerprint
from .synthetic_score import DEPTH_PROBE_MAX, DocumentAdapter, _assert_config
from .verifier import CandidateProbability, DecisionCollection, PassageDecision

DEPTHS = (6, 10, 20, 30, 50, 75, 100, 150, 200)
ROOT = Path(__file__).resolve().parents[2]


def depth_curve(
    survivors: Mapping[str, Sequence[CandidateLike]],
    gold: Mapping[str, Set[str]],
    *,
    depths: Sequence[int] = DEPTHS,
) -> dict[str, Any]:
    """Measure ordered gated survivors, without applying the answer rule.

    N is selected among the measured depths (including the depth-200 reference).
    Exact rational comparisons keep the inclusive one-percentage-point boundary.
    Empty populations return zero recall; scoreable rows must have nonempty gold.
    """
    if not depths or any(type(k) is not int or not 1 <= k <= DEPTH_PROBE_MAX for k in depths):
        raise ValueError(f"depths must be integers in [1, {DEPTH_PROBE_MAX}]")
    if any(not iris for iris in gold.values()):
        raise ValueError("scoreable passages must have nonempty gold")
    total = sum(len(iris) for iris in gold.values())
    curve: dict[str, dict[str, float | int]] = {}
    hits_by_depth: dict[int, int] = {}
    for depth in sorted(set(depths) | {DEPTH_PROBE_MAX}):
        hits = [len({c.iri for c in survivors[key][:depth]} & iris) for key, iris in gold.items()]
        retrieved = sum(hits)
        hits_by_depth[depth] = retrieved
        curve[str(depth)] = {
            "depth": depth,
            "micro_recall": retrieved / total if total else 0.0,
            "mean_item_recall": sum(
                hit / len(iris) for hit, iris in zip(hits, gold.values(), strict=True)
            )
            / len(gold)
            if gold
            else 0.0,
            "retrieved_gold_count": retrieved,
            "unreachable_gold_count": total - retrieved,
        }
    reference = hits_by_depth[DEPTH_PROBE_MAX]
    uncapped = min(
        k
        for k, hits in hits_by_depth.items()
        if Fraction(reference - hits, total or 1) <= Fraction(1, 100)
    )
    return {
        "curve": curve,
        "chosen_n": min(uncapped, 100),
        "uncapped_n": uncapped,
        "gold_relation_count": total,
        "scoreable_item_count": len(gold),
        "unreachable_gold_count_at_200": total - reference,
    }


def build_baseline_collection(
    corpus: LoadedCorpus,
    survivors: Mapping[str, Sequence[CandidateLike]],
    config: AnswerRuleConfig,
    *,
    shortlist_depth: int,
    adapter_source: str,
    adapter_sha256: str,
) -> DecisionCollection:
    """Encode the lane's exact committed answers as p=1, other survivors as p=0.

    The prompt hash is the canonical answer-rule hash: this deterministic arm has
    no model prompt. Replay with Thresholds(.5, None), never cross-fit this arm.
    """
    _assert_config(corpus, config)
    if config.threshold != 0.5 or config.top_k != 6:
        raise ValueError("baseline requires threshold=0.5 and top_k=6")
    if type(shortlist_depth) is not int or not 6 <= shortlist_depth <= 100:
        raise ValueError("baseline shortlist depth must be in [6, 100]")
    decisions = []
    for kind, items in (("scoreable", corpus.scoreable_items), ("nomatch", corpus.nomatch_items)):
        for item in sorted(items, key=lambda row: row.item_id):
            ordered = survivors[item.item_id]
            committed = {
                c.iri for c in commit_from_ranked(rank_candidates(ordered, config), config)
            }
            shortlist = tuple(c.iri for c in ordered[:shortlist_depth])
            if not committed.issubset(shortlist):
                raise ValueError(f"{item.item_id}: shortlist omits a committed baseline answer")
            decisions.append(
                PassageDecision(
                    item_id=item.item_id,
                    kind=kind,
                    shortlist=shortlist,
                    no_match_p=0.0,
                    candidates=tuple(
                        CandidateProbability(iri, float(iri in committed)) for iri in shortlist
                    ),
                )
            )
    collection = DecisionCollection(
        arm_name="baseline-attempt-0004",
        model_id="deterministic-answer-rule-v1",
        prompt_template_sha256=config.content_sha256(),
        corpus_content_sha256=corpus.manifest.content_sha256,
        nomatch_content_sha256=corpus.manifest.nomatch_content_sha256,
        adapter_source=adapter_source,
        adapter_sha256=adapter_sha256,
        shortlist_depth=shortlist_depth,
        decisions=tuple(decisions),
    )
    collection.validate(corpus)
    return collection


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Verifier shortlist depth",
        "",
        "| Depth | Micro gold recall | Mean per-item gold recall | Missing gold relations |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for row in report["curve"].values():
        lines.append(
            f"| {row['depth']} | {row['micro_recall']:.6f} | "
            f"{row['mean_item_recall']:.6f} | {row['unreachable_gold_count']} |"
        )
    lines.extend(
        [
            "",
            f"Chosen N: {report['chosen_n']} (uncapped: {report['uncapped_n']}).",
            "Smallest measured depth within 1 percentage point of depth-200 micro recall, "
            "capped at 100.",
            "",
            f"Unreachable gold relations at depth 200: {report['unreachable_gold_count_at_200']}.",
            "",
            "Baseline replay: Thresholds(0.5, None); no abstention.",
            "The deterministic baseline prompt hash identifies the canonical answer rule.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "eval/synthetic/answer_rule_config_synthetic_v1.json"
    )
    parser.add_argument("--leak-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    args = parser.parse_args(argv)
    ensure_hash_seed()
    corpus = load_corpus(args.corpus_manifest)
    config = load_config(args.config)
    _assert_config(corpus, config)
    if not corpus.manifest.scoreable:
        raise ValueError("corpus manifest is not scoreable")
    # Reuse the scorer's exact unfiltered pristine-tree check and provenance.
    fingerprint = build_checkpoint_fingerprint(corpus, config, repo_root=ROOT)
    pin = assert_ontology_pin(corpus.manifest.ontology_cache_sha256)
    manifest = load_manifest(args.leak_manifest)
    salt = args.salt_file.read_bytes()
    scan_text("", manifest, salt)  # Validate salt before expensive adapter work.
    from folio import FOLIO

    from folio_resolve.ontology import FolioPythonProvider

    adapter = DocumentAdapter(FolioPythonProvider(_folio=FOLIO()))
    survivors = {
        item.item_id: adapter.adapt(item.text).candidates
        for item in sorted(
            (*corpus.scoreable_items, *corpus.nomatch_items), key=lambda row: row.item_id
        )
    }
    report = depth_curve(
        survivors, {item.item_id: item.gold_iris for item in corpus.scoreable_items}
    )
    adapter_path = Path(__file__).with_name("synthetic_score.py")
    adapter_sha = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
    collection = build_baseline_collection(
        corpus,
        survivors,
        config,
        shortlist_depth=report["chosen_n"],
        adapter_source=fingerprint.git_head,
        adapter_sha256=adapter_sha,
    )
    report.update(
        corpus_content_sha256=corpus.manifest.content_sha256,
        nomatch_content_sha256=corpus.manifest.nomatch_content_sha256,
        adapter_source=fingerprint.git_head,
        adapter_sha256=adapter_sha,
        ontology_cache_sha256=pin.sha256,
        answer_rule_config_sha256=config.content_sha256(),
    )
    payload = collection.to_json()
    markdown = render_markdown(report)
    # Check every artifact before writing any of them; no metadata exemptions.
    for value in (report, payload):
        if scan_json_value(value, manifest, salt):
            raise ValueError("leak check failed for JSON output")
    if scan_text(markdown, manifest, salt):
        raise ValueError("leak check failed for Markdown output")
    outputs = {
        ROOT / "docs/benchmarks/verifier-shortlist-depth.json": json.dumps(
            report, indent=2, sort_keys=True
        )
        + "\n",
        ROOT / "docs/benchmarks/verifier-shortlist-depth.md": markdown,
        ROOT / "eval/synthetic/verifier/baseline-collection-v1.json": json.dumps(
            payload, indent=2, sort_keys=True
        )
        + "\n",
    }
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return 0
