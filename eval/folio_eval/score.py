"""Strict exact-IRI set scoring (U3; R5, R7; KTD2, KTD3, KTD7).

The headline metric is KD1's **strict exact-IRI set F1**. For one scoring item:

* the pipeline is called as ``match(surface_term=leaf, heading_terms=<ancestor path>)`` (KTD3's
  adapter contract -- ancestor context reaches the gates, not candidate generation, at baseline);
* the ranked candidates go through the committed answer rule (:mod:`.answer_rule`, KTD2);
* TP / FP / FN are counted at the **(item, IRI)** level against the item's gold set, exactly --
  a parent, a child, or a sibling of a gold concept scores **zero** (AE5);
* micro-averaged P/R/F1 are reported overall, per firm, and per stratum;
* ``recall@k`` for k in 1/3/5/10 is computed from the **raw ranked list**, before the answer rule,
  as the threshold-free diagnostic KTD2 requires;
* every false positive is bucketed against the FOLIO hierarchy -- direct parent, direct child,
  2-hop ancestor, 2-hop descendant, sibling, or unrelated -- as the near-miss diagnostic.

Blank gold rows never enter the denominator (KD7 / AE2); the slice manifest already excludes
them, and :func:`score_items` refuses them outright so no future caller can smuggle one in.

Determinism (KTD7): candidates are ordered ``(score desc, IRI asc)`` before any cutoff, every
aggregate iterates sorted keys, and every emitted structure is canonical JSON. The determinism
self-test in :mod:`.selftest` re-runs a scoring pass under a different ``PYTHONHASHSEED`` and
compares content hashes.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from folio_resolve.entity_ruler import FOLIOEntityRuler
from folio_resolve.ontology import Concept, FolioPythonProvider, OntologyProvider, RecallOntology
from folio_resolve.pipeline import MatchPipeline
from folio_resolve.recall import MultiStrategyRecall

from .answer_rule import (
    AnswerRuleConfig,
    CandidateLike,
    RankedCandidate,
    commit_from_ranked,
    rank_candidates,
)
from .splits import GoldItemRecord

#: The k values the threshold-free recall diagnostic reports (KTD2).
RECALL_AT_K = (1, 3, 5, 10)

#: Near-miss buckets, in the priority order a false positive is classified by (AE5 / KTD3).
NEAR_MISS_BUCKETS = (
    "parent_1hop",
    "child_1hop",
    "ancestor_2hop",
    "descendant_2hop",
    "sibling",
    "unrelated",
)


class ScoringError(RuntimeError):
    """Raised when an item cannot be scored (a blank row, an absent gold set)."""


# --------------------------------------------------------------------------------------
# Hierarchy (near-miss diagnostics)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hierarchy:
    """Direct parent/child edges over concept IRIs, built once and reused.

    folio-python exposes ``get_parents``/``get_children`` as recursive subgraph walks; doing the
    walk per (prediction, gold) pair would be both slow and depth-fuzzy. The edge map is derived
    once from every class's ``sub_class_of`` and answers 1-hop, 2-hop, and sibling questions
    exactly.
    """

    parents: Mapping[str, frozenset[str]]
    children: Mapping[str, frozenset[str]]

    @classmethod
    def from_parent_map(cls, mapping: Mapping[str, Iterable[str]]) -> Hierarchy:
        parents: dict[str, frozenset[str]] = {}
        children: dict[str, set[str]] = {}
        for iri, parent_iris in mapping.items():
            resolved = frozenset(parent_iris)
            parents[iri] = resolved
            for parent in resolved:
                children.setdefault(parent, set()).add(iri)
        return cls(
            parents=parents,
            children={key: frozenset(value) for key, value in children.items()},
        )

    @classmethod
    def from_concepts(cls, concepts: Iterable[Concept]) -> Hierarchy:
        return cls.from_parent_map({c.iri: c.parent_iris for c in concepts})

    @classmethod
    def from_folio(cls, folio: Any) -> Hierarchy:
        """Build from a constructed ``folio.FOLIO`` graph, offline, without a network call."""
        mapping: dict[str, Iterable[str]] = {}
        for owl in getattr(folio, "classes", []):
            iri = getattr(owl, "iri", "") or ""
            if not iri:
                continue
            raw = getattr(owl, "sub_class_of", None) or ()
            mapping[iri] = tuple(parent for parent in raw if isinstance(parent, str))
        return cls.from_parent_map(mapping)

    def parents_of(self, iri: str) -> frozenset[str]:
        return self.parents.get(iri, frozenset())

    def children_of(self, iri: str) -> frozenset[str]:
        return self.children.get(iri, frozenset())

    def grandparents_of(self, iri: str) -> frozenset[str]:
        return frozenset(
            grandparent
            for parent in self.parents_of(iri)
            for grandparent in self.parents_of(parent)
        )

    def grandchildren_of(self, iri: str) -> frozenset[str]:
        return frozenset(
            grandchild for child in self.children_of(iri) for grandchild in self.children_of(child)
        )

    def siblings_of(self, iri: str) -> frozenset[str]:
        return frozenset(
            sibling
            for parent in self.parents_of(iri)
            for sibling in self.children_of(parent)
            if sibling != iri
        )


EMPTY_HIERARCHY = Hierarchy(parents={}, children={})


def near_miss_bucket(predicted: str, gold_iris: Iterable[str], hierarchy: Hierarchy) -> str:
    """Classify one false positive against the gold set. First bucket in priority order wins."""
    gold = set(gold_iris)
    if not gold:
        return "unrelated"
    if gold & hierarchy.children_of(predicted):
        # predicted is the direct PARENT of a gold concept (AE5's case).
        return "parent_1hop"
    if gold & hierarchy.parents_of(predicted):
        return "child_1hop"
    if gold & hierarchy.grandchildren_of(predicted):
        return "ancestor_2hop"
    if gold & hierarchy.grandparents_of(predicted):
        return "descendant_2hop"
    if gold & hierarchy.siblings_of(predicted):
        return "sibling"
    return "unrelated"


# --------------------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------------------


@dataclass
class MicroCounts:
    """Micro-averaged (item, IRI)-level counts for one aggregation key."""

    items: int = 0
    gold: int = 0
    predicted: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    exact_items: int = 0
    empty_prediction_items: int = 0

    @property
    def precision(self) -> float:
        denominator = self.tp + self.fp
        return self.tp / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.tp + self.fn
        return self.tp / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        return 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    def to_json(self) -> dict[str, object]:
        return {
            "items": self.items,
            "gold": self.gold,
            "predicted": self.predicted,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "exact_items": self.exact_items,
            "empty_prediction_items": self.empty_prediction_items,
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "f1": round(self.f1, 6),
        }


@dataclass(frozen=True, slots=True)
class ItemScore:
    """One item's outcome: the committed set, the raw ranked list, and their diagnostics."""

    item_id: str
    firm: str
    stratum_id: str
    slice_name: str
    input_text: str
    leaf: str
    gold_iris: tuple[str, ...]
    committed_iris: tuple[str, ...]
    ranked_iris: tuple[str, ...]
    tp: int
    fp: int
    fn: int
    hits_at_k: Mapping[int, int]
    near_miss: Mapping[str, int]
    ranked: tuple[RankedCandidate, ...] = ()
    elapsed_ms: float = 0.0

    @property
    def exact(self) -> bool:
        return set(self.committed_iris) == set(self.gold_iris)

    def to_json(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "firm": self.firm,
            "stratum_id": self.stratum_id,
            "slice": self.slice_name,
            "gold_iris": list(self.gold_iris),
            "committed_iris": list(self.committed_iris),
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "exact": self.exact,
            "hits_at_k": {str(k): self.hits_at_k[k] for k in sorted(self.hits_at_k)},
            "near_miss": {key: self.near_miss[key] for key in sorted(self.near_miss)},
        }


@dataclass
class ScoreRun:
    """Everything one scoring pass produced, before it becomes a report."""

    slice_name: str
    config: AnswerRuleConfig
    ranked_limit: int = 0
    item_scores: list[ItemScore] = field(default_factory=list)
    overall: MicroCounts = field(default_factory=MicroCounts)
    by_firm: dict[str, MicroCounts] = field(default_factory=dict)
    by_stratum: dict[str, MicroCounts] = field(default_factory=dict)
    hits_at_k: dict[int, int] = field(default_factory=dict)
    near_miss: dict[str, int] = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def recall_at_k(self) -> dict[int, float]:
        gold = self.overall.gold
        return {k: (self.hits_at_k.get(k, 0) / gold if gold else 0.0) for k in RECALL_AT_K}


Predictor = Callable[[GoldItemRecord], Sequence[CandidateLike]]


def score_items(
    items: Sequence[GoldItemRecord],
    predict: Predictor,
    *,
    config: AnswerRuleConfig,
    hierarchy: Hierarchy = EMPTY_HIERARCHY,
    slice_name: str = "",
    keep_ranked: int = 20,
) -> ScoreRun:
    """Run the pipeline over ``items`` and count strict exact-IRI set TP/FP/FN."""
    run = ScoreRun(slice_name=slice_name, config=config, ranked_limit=keep_ranked)
    run.hits_at_k = dict.fromkeys(RECALL_AT_K, 0)
    run.near_miss = dict.fromkeys(NEAR_MISS_BUCKETS, 0)
    started = time.perf_counter()

    for item in sorted(items, key=lambda record: record.item_id):
        if item.blank:
            raise ScoringError(
                f"blank gold row reached the scorer: {item.item_id} — blanks are coverage (KD7)"
            )
        if not item.gold_iris:
            raise ScoringError(f"scored item with an empty gold set: {item.item_id}")

        item_started = time.perf_counter()
        ranked = rank_candidates(predict(item), config)
        committed = commit_from_ranked(ranked, config)
        elapsed_ms = (time.perf_counter() - item_started) * 1000.0

        gold = set(item.gold_iris)
        predicted = [candidate.iri for candidate in committed]
        predicted_set = set(predicted)
        true_positive = sorted(predicted_set & gold)
        false_positive = sorted(predicted_set - gold)
        false_negative = sorted(gold - predicted_set)

        ranked_iris = [candidate.iri for candidate in ranked]
        hits_at_k = {k: len(gold & set(ranked_iris[:k])) for k in RECALL_AT_K}
        near_miss = dict.fromkeys(NEAR_MISS_BUCKETS, 0)
        for iri in false_positive:
            near_miss[near_miss_bucket(iri, gold, hierarchy)] += 1

        score = ItemScore(
            item_id=item.item_id,
            firm=item.firm,
            stratum_id=item.stratum_id,
            slice_name=slice_name,
            input_text=item.input_text,
            leaf=item.leaf,
            gold_iris=tuple(sorted(gold)),
            committed_iris=tuple(predicted),
            ranked_iris=tuple(ranked_iris[:keep_ranked]),
            tp=len(true_positive),
            fp=len(false_positive),
            fn=len(false_negative),
            hits_at_k=hits_at_k,
            near_miss=near_miss,
            ranked=tuple(ranked[:keep_ranked]),
            elapsed_ms=elapsed_ms,
        )
        run.item_scores.append(score)

        for bucket in (
            run.overall,
            run.by_firm.setdefault(item.firm, MicroCounts()),
            run.by_stratum.setdefault(item.stratum_id, MicroCounts()),
        ):
            bucket.items += 1
            bucket.gold += len(gold)
            bucket.predicted += len(predicted_set)
            bucket.tp += score.tp
            bucket.fp += score.fp
            bucket.fn += score.fn
            bucket.exact_items += 1 if score.exact else 0
            bucket.empty_prediction_items += 0 if predicted_set else 1

        for k in RECALL_AT_K:
            run.hits_at_k[k] += hits_at_k[k]
        for key, value in near_miss.items():
            run.near_miss[key] += value

    run.elapsed_s = time.perf_counter() - started
    run.by_firm = dict(sorted(run.by_firm.items()))
    run.by_stratum = dict(sorted(run.by_stratum.items()))
    return run


# --------------------------------------------------------------------------------------
# Pipeline adapter (KTD3)
# --------------------------------------------------------------------------------------


@dataclass
class PipelineAdapter:
    """KTD3's adapter contract: leaf label as ``surface_term``, ancestors as ``heading_terms``.

    ``MatchPipeline._rank`` compares ``candidate.label.lower()`` against ``heading_terms``, so the
    ancestor path is lowercased here. Today that context only reaches the place-name gate, not
    candidate generation -- an explicitly recorded baseline property (KTD3), not an assumption.
    """

    pipeline: MatchPipeline

    def __call__(self, item: GoldItemRecord) -> Sequence[CandidateLike]:
        return self.pipeline.match(
            surface_term=item.leaf,
            heading_terms={part.lower() for part in item.ancestor_path},
            full_text=item.input_text,
        )


def build_pipeline(
    provider: OntologyProvider,
    *,
    with_entity_ruler: bool = True,
    label_search_limit: int = 10,
    with_multi_strategy_recall: bool = False,
) -> MatchPipeline:
    """Construct the pipeline under test over an already-built ontology provider.

    The provider is *injected*: the caller resolves and hashes the pinned FOLIO cache first
    (KTD7), so the pipeline can never trigger a network fetch of a different snapshot.
    """
    ruler: FOLIOEntityRuler | None = None
    if with_entity_ruler:
        ruler = FOLIOEntityRuler()
        ruler.load_patterns(provider.all_labels())
    recall_engine: MultiStrategyRecall | None = None
    if with_multi_strategy_recall:
        if not isinstance(provider, RecallOntology):
            raise TypeError("multi-strategy recall requires a RecallOntology provider")
        recall_engine = MultiStrategyRecall(provider)
    return MatchPipeline(
        ontology=provider,
        entity_ruler=ruler,
        label_search_limit=label_search_limit,
        recall_engine=recall_engine,
    )


def build_folio_provider(folio: Any) -> FolioPythonProvider:
    """Wrap an already-constructed ``folio.FOLIO`` graph (never one this call would fetch)."""
    return FolioPythonProvider(_folio=folio)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Score one slice end-to-end, offline, with every KTD7 gate in front of it."""
    import argparse
    import json

    from .answer_rule import DEFAULT_CONFIG_FILENAME, load_or_create_config
    from .report import (
        DEFAULT_ITEM_REPORT_DIR,
        DEFAULT_SUMMARY_DIR,
        build_summary,
        canonical_json,
        write_item_csv,
        write_summary,
    )
    from .resolve_labels import folio_python_version
    from .selftest import (
        DEFAULT_SELFTEST_TARGET,
        OntologyPinError,
        assert_ontology_pin,
        ensure_hash_seed,
        run_determinism_selftest,
    )
    from .splits import (
        DEFAULT_GOLD_DIR,
        DEFAULT_SPLIT_MANIFEST,
        FROZEN_SLICE,
        SLICE_NAMES,
        build_splits,
        load_gold,
        load_split_manifest,
        write_split_manifest,
    )

    parser = argparse.ArgumentParser(
        prog="python -m folio_eval.score",
        description="Strict exact-IRI set-F1 scoring over a gold slice (U3).",
    )
    parser.add_argument("--slice", choices=SLICE_NAMES, default="tune")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR / "gold_v1.jsonl")
    parser.add_argument("--gold-manifest", type=Path, default=None)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--config", type=Path, default=DEFAULT_GOLD_DIR / DEFAULT_CONFIG_FILENAME)
    parser.add_argument("--summary-dir", type=Path, default=DEFAULT_SUMMARY_DIR)
    parser.add_argument("--item-report-dir", type=Path, default=DEFAULT_ITEM_REPORT_DIR)
    parser.add_argument("--label", default="", help="run label recorded in the summary filename")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="score only the first N items of the slice by item_id (smoke runs; marks the report sampled)",
    )
    parser.add_argument("--label-search-limit", type=int, default=10)
    parser.add_argument("--multi-strategy-recall", action="store_true")
    parser.add_argument("--no-entity-ruler", action="store_true")
    parser.add_argument("--determinism-target", default=DEFAULT_SELFTEST_TARGET)
    parser.add_argument(
        "--frozen-final",
        action="store_true",
        help="required to score the frozen slice; frozen scores are computed at baseline and "
        "final report only (frozen-discipline gate)",
    )
    parser.add_argument(
        "--allow-ontology-bump",
        action="store_true",
        help="proceed when the FOLIO cache hash differs from the gold manifest (explicit re-baseline)",
    )
    parser.add_argument(
        "--build-splits",
        action="store_true",
        help="(re)draw the split manifest from gold before scoring",
    )
    args = parser.parse_args(argv)

    ensure_hash_seed()

    if args.slice == FROZEN_SLICE and not args.frozen_final:
        parser.error(
            "refusing to score the frozen slice without --frozen-final: the holdout is scored "
            "at baseline and final report only (KTD4 / frozen-discipline gate)"
        )
    if args.slice == FROZEN_SLICE:
        print(
            "WARNING: scoring the FROZEN slice. Every look costs holdout integrity — this must be "
            "a baseline or a final report, never an iteration measurement (KTD4).",
            file=sys.stderr,
        )

    gold = load_gold(args.gold, manifest_path=args.gold_manifest)
    try:
        pin = assert_ontology_pin(gold.ontology_cache_sha256)
    except OntologyPinError as error:
        if not args.allow_ontology_bump:
            print(f"ABORT: {error}", file=sys.stderr)
            return 2
        print(f"WARNING (--allow-ontology-bump): {error}", file=sys.stderr)
        pin = assert_ontology_pin("")
    gold = load_gold(args.gold, manifest_path=args.gold_manifest, ontology_sha256=gold.ontology_cache_sha256)

    selftest = run_determinism_selftest(args.determinism_target)
    print(f"determinism self-test OK ({selftest.first_sha256[:12]}, target={selftest.target})")

    if args.build_splits or not args.split_manifest.exists():
        plan = build_splits(gold.scored())
        write_split_manifest(plan, gold, args.split_manifest)
    split = load_split_manifest(args.split_manifest, gold)

    items = split.slice_items(args.slice, gold)
    if args.limit:
        items = sorted(items, key=lambda record: record.item_id)[: args.limit]
    if not items:
        print(f"slice {args.slice!r} is empty — nothing to score", file=sys.stderr)
        return 1

    config = load_or_create_config(args.config)
    if not config.calibrated:
        print(
            "NOTE: the answer rule is UNCALIBRATED "
            f"(threshold={config.threshold}, k={config.top_k}) — U4 fits it on tune items only.",
            file=sys.stderr,
        )

    from folio import FOLIO

    folio = FOLIO()
    provider = build_folio_provider(folio)
    pipeline = build_pipeline(
        provider,
        with_entity_ruler=not args.no_entity_ruler,
        label_search_limit=args.label_search_limit,
        with_multi_strategy_recall=args.multi_strategy_recall,
    )
    hierarchy = Hierarchy.from_folio(folio)

    run = score_items(
        items,
        PipelineAdapter(pipeline),
        config=config,
        hierarchy=hierarchy,
        slice_name=args.slice,
    )

    summary = build_summary(
        run,
        gold=gold,
        config=config,
        split_manifest=split,
        ontology_cache_sha256=pin.sha256,
        folio_python_version=folio_python_version(),
        label=args.label,
        sampled=bool(args.limit),
        extra={
            "determinism_selftest": selftest.to_json(),
            "label_search_limit": args.label_search_limit,
            "entity_ruler": not args.no_entity_ruler,
            "multi_strategy_recall": args.multi_strategy_recall,
        },
    )
    stem = "-".join(part for part in (gold.gold_id, args.slice, args.label or None) if part)
    summary_path = write_summary(summary, args.summary_dir / f"score-{stem}.json")
    csv_path = write_item_csv(run.item_scores, args.item_report_dir / f"items-{stem}.csv")

    print(canonical_json({key: summary[key] for key in ("overall", "recall_at_k", "near_miss", "timing")}))
    print(f"summary (committed dir): {summary_path}")
    print(f"per-item CSV (gitignored): {csv_path}")
    print(json.dumps({"gold_id": gold.gold_id, "config_sha256": config.content_sha256()}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
