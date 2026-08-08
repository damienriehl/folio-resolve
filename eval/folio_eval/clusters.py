"""Baseline run, answer-rule calibration, and cause-clustered failure analysis (U4; R8; KTD2, KTD7).

U3 left two things undone: the answer rule was an *uncalibrated placeholder* (``threshold=0.5``,
``k=5``, ``calibrated=false``), and nothing explained *why* the pipeline missed. This module
closes both, in one pipeline pass, and emits the baseline the whole improvement loop is measured
against.

Three pieces:

1. **One pipeline pass, replayed many times.** :func:`collect_raw_candidates` runs
   ``MatchPipeline.match`` once per item and caches the raw candidate lists. Calibration fitting,
   the threshold x k grid search, the final scoring pass, and the cluster analysis then all run
   off that cache -- so the expensive part happens exactly once and every derived number is
   computed from identical inputs.

2. **Calibration + grid search on the TUNE slice only (KTD2).** Every ranked candidate of every
   tune item is one ``(raw score, correct?)`` pair, ``correct`` iff its IRI is in that item's gold
   set. :func:`fit_calibration` fits ``folio_resolve.calibration.ScoreCalibration``; then
   :func:`grid_search` sweeps thresholds x k and :func:`select_point` picks the micro-F1 maximum.
   The *rule* stays gold-count-blind (KTD2): the fit chooses two global constants, and nothing at
   scoring time ever reads a gold count.

3. **Cause clusters for every miss (R8).** The decisive question for iteration 1 is whether a
   missing gold IRI was *reachable and truncated* or *genuinely out of reach*, because the cheap
   fix -- ``FolioPythonProvider.search_by_label`` drops its ``limit`` argument, so folio-python's
   default of 10 silently caps every candidate list -- must be taken or ruled out before a recall
   engine is credited. So each miss is probed against a *direct* ``folio.search_by_label(query,
   limit=200)`` (bypassing the provider), cached per unique query string.

Cluster taxonomy
----------------

Two views of the same classification are reported, because they answer different questions.

``reachability`` -- the **decisive 3-way axis**, and the table that picks iteration 1:

* ``ranked_below_cutoff`` -- the gold IRI *is* in the pipeline's raw ranked list, just not in the
  committed answer set. Fixable by threshold/k, not by recall.
* ``candidate_gap_truncated`` -- absent from the ranked list, but a direct search at limit=200
  returns it for the leaf or one of the pipeline's own generated search terms. Reachable today;
  the limit bug (or the candidate flow) is what dropped it.
* ``candidate_gap_unreachable`` -- absent even at limit=200 for every query variant. Needs new
  recall strategies (prefix / definition / multi-term) or is a synonymy gap.

``cluster`` -- the **7-way refinement**. Its first two values are exactly the first two
reachability values; the remaining five all live *inside* ``candidate_gap_unreachable`` and say
which flavour of unreachable it is: ``normalization`` (the label is literally in the ontology
modulo NFKC/dash/whitespace/case/plural), ``homonym_or_wrong_sense`` (the committed set contains a
different concept whose label matches the surface exactly), ``hierarchy_near_miss`` (a 1-hop
parent or child of the gold IRI did surface), ``synonymy`` (zero normalized-token overlap with
every label of the gold concept), and ``candidate_gap_unreachable`` as the residual (partial token
overlap, nothing else fired -- the lexical-recall gap).

False positives get the two buckets R8 asks for, derived from :func:`score.near_miss_bucket`:
``fp_near_miss`` (direct parent or child of a gold concept, 1 hop) and ``fp_unrelated``.

Every boolean signal is recorded on every detail row regardless of which cluster won, so U5's
audit packet and U7's iteration targeting can re-slice without re-running the pipeline.

Output surfaces follow KTD1 exactly: ``eval/reports/baseline-v1.json`` is committed and carries
counts, metrics, and hashes only -- it is leak-scanned against every gold surface string before it
is written -- while row-level detail (``eval/data/reports/clusters_v1.jsonl``, the per-item CSVs)
stays gitignored.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from folio_resolve.calibration import CalibrationSample, ScoreCalibration
from folio_resolve.decompose import decompose

from .answer_rule import AnswerRuleConfig, RankedCandidate, commit_from_ranked, rank_candidates
from .normalize import label_key, normalize_label, plural_variants
from .score import Hierarchy, ItemScore, ScoreRun, near_miss_bucket
from .splits import GoldItemRecord, GoldSet

# --------------------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------------------

#: The 7-way miss taxonomy, in the priority order :func:`classify_miss` applies.
MISS_CLUSTERS: tuple[str, ...] = (
    "ranked_below_cutoff",
    "candidate_gap_truncated",
    "normalization",
    "homonym_or_wrong_sense",
    "hierarchy_near_miss",
    "synonymy",
    "candidate_gap_unreachable",
)

#: The decisive 3-way axis. Clusters 3-6 all roll up into ``candidate_gap_unreachable``.
REACHABILITY_CLASSES: tuple[str, ...] = (
    "ranked_below_cutoff",
    "candidate_gap_truncated",
    "candidate_gap_unreachable",
)

#: False-positive buckets (R8).
FP_CLUSTERS: tuple[str, ...] = ("fp_near_miss", "fp_unrelated")

#: ``near_miss_bucket`` values that count as a 1-hop near miss.
_NEAR_MISS_1HOP = frozenset({"parent_1hop", "child_1hop"})

#: Function words are dropped before the token-overlap test so that "Rules of X" vs "Rules of Y"
#: does not read as overlap purely through *of*. Keeps the synonymy heuristic honest.
TOKEN_STOPWORDS = frozenset(
    {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
)

_TOKEN_RE = re.compile(r"[^0-9a-z]+")

#: The direct-diagnostic search limit. 200 >> folio-python's default of 10.
DIRECT_SEARCH_LIMIT = 200


def tokens(text: str) -> frozenset[str]:
    """Normalized content tokens of a label or surface string."""
    normalized = label_key(text)
    return frozenset(part for part in _TOKEN_RE.split(normalized) if part and part not in TOKEN_STOPWORDS)


def token_jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """|A n B| / |A u B|; 0.0 when either side is empty."""
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


# --------------------------------------------------------------------------------------
# Signals and classification
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MissSignals:
    """Everything :func:`classify_miss` is allowed to look at, for one (item, gold IRI) miss."""

    in_ranked: bool
    reachable_at_limit: bool
    exact_label_match: bool = False
    homonym_collision: bool = False
    hierarchy_neighbor_seen: bool = False
    max_token_jaccard: float = 0.0

    def to_json(self) -> dict[str, object]:
        return {
            "in_ranked": self.in_ranked,
            "reachable_at_limit": self.reachable_at_limit,
            "exact_label_match": self.exact_label_match,
            "homonym_collision": self.homonym_collision,
            "hierarchy_neighbor_seen": self.hierarchy_neighbor_seen,
            "max_token_jaccard": round(self.max_token_jaccard, 6),
        }


def classify_miss(signals: MissSignals) -> str:
    """Assign one miss to exactly one of :data:`MISS_CLUSTERS`.

    Reachability dominates: a miss that the pipeline *did* rank, or that a limit-200 search *does*
    return, is classified by that fact alone, whatever else is true of it. That ordering is the
    whole point of the unit -- the cheap fixes (threshold/k, the dropped ``limit``) must be
    attributed before any unreachability story is told.
    """
    if signals.in_ranked:
        return "ranked_below_cutoff"
    if signals.reachable_at_limit:
        return "candidate_gap_truncated"
    if signals.exact_label_match:
        return "normalization"
    if signals.homonym_collision:
        return "homonym_or_wrong_sense"
    if signals.hierarchy_neighbor_seen:
        return "hierarchy_near_miss"
    if signals.max_token_jaccard <= 0.0:
        return "synonymy"
    return "candidate_gap_unreachable"


def reachability_of(cluster: str) -> str:
    """Roll a 7-way cluster up to the decisive 3-way axis."""
    if cluster not in MISS_CLUSTERS:
        raise ValueError(f"unknown miss cluster: {cluster!r}")
    if cluster in ("ranked_below_cutoff", "candidate_gap_truncated"):
        return cluster
    return "candidate_gap_unreachable"


def classify_false_positive(bucket: str) -> str:
    """``fp_near_miss`` for a direct parent/child of a gold concept, else ``fp_unrelated``."""
    return "fp_near_miss" if bucket in _NEAR_MISS_1HOP else "fp_unrelated"


# --------------------------------------------------------------------------------------
# The direct-search diagnostic
# --------------------------------------------------------------------------------------

#: ``query -> the IRIs a limit-200 label search returns``.
DirectSearch = Callable[[str], frozenset[str]]


def query_variants(leaf: str) -> tuple[str, ...]:
    """The query strings the diagnostic probes for one item: the leaf and the pipeline's own terms.

    ``MatchPipeline._filter`` searches the surface term; ``_expand`` searches every
    ``decompose()`` part after the first. Probing exactly that set keeps the diagnostic honest --
    a gold IRI counted as *reachable* is reachable through a query the pipeline already issues,
    only truncated by the limit.
    """
    ordered: list[str] = []
    for candidate in (leaf, normalize_label(leaf), *decompose(leaf)[1:]):
        text = candidate.strip()
        if text and text not in ordered:
            ordered.append(text)
    return tuple(ordered)


@dataclass
class CachedDirectSearch:
    """A ``DirectSearch`` memoized per unique query string, with call counters.

    The tune slice is 1217 items and most of them miss, so an uncached probe would issue thousands
    of duplicate ontology searches. Duplicate leaf labels alone number 203 in Firm 1.
    """

    search: DirectSearch
    limit: int = DIRECT_SEARCH_LIMIT
    _cache: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)
    lookups: int = 0
    misses: int = 0

    def __call__(self, query: str) -> frozenset[str]:
        self.lookups += 1
        cached = self._cache.get(query)
        if cached is None:
            self.misses += 1
            cached = frozenset(self.search(query))
            self._cache[query] = cached
        return cached

    def probe(self, queries: Iterable[str]) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
        """Union of every variant's results, plus the per-variant map (which query found what)."""
        per_query = {query: self(query) for query in queries}
        union: frozenset[str] = frozenset()
        for result in per_query.values():
            union |= result
        return union, per_query

    @property
    def unique_queries(self) -> int:
        return len(self._cache)

    def to_json(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "lookups": self.lookups,
            "unique_queries": self.unique_queries,
        }


def folio_direct_search(folio: Any, *, limit: int = DIRECT_SEARCH_LIMIT) -> DirectSearch:
    """Bypass ``FolioPythonProvider`` and call folio-python's search with an explicit limit.

    ``FolioPythonProvider.search_by_label`` calls ``folio.search_by_label(query)`` without
    forwarding ``limit`` (``src/folio_resolve/ontology.py``), so every provider call is capped at
    folio-python's default of 10 before the provider's own ``[:limit]`` ever applies. The
    diagnostic must not inherit that cap, so it talks to the graph directly. Fixing the provider
    is U7's business, not this unit's.
    """

    def search(query: str) -> frozenset[str]:
        results = folio.search_by_label(query, limit=limit)
        out: set[str] = set()
        for item in results:
            owl = item[0] if isinstance(item, tuple) else item
            iri = getattr(owl, "iri", "") or ""
            if iri:
                out.add(iri)
        return frozenset(out)

    return search


def folio_label_map(folio: Any) -> dict[str, tuple[str, ...]]:
    """``IRI -> every label the concept answers to`` (preferred first, then alternatives)."""
    out: dict[str, tuple[str, ...]] = {}
    for owl in getattr(folio, "classes", []):
        iri = getattr(owl, "iri", "") or ""
        if not iri:
            continue
        labels: list[str] = []
        for attr in ("preferred_label", "label"):
            value = getattr(owl, attr, "") or ""
            if isinstance(value, str) and value and value not in labels:
                labels.append(value)
        for alt in getattr(owl, "alternative_labels", None) or ():
            if isinstance(alt, str) and alt and alt not in labels:
                labels.append(alt)
        out[iri] = tuple(labels)
    return out


# --------------------------------------------------------------------------------------
# Detail rows
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MissRow:
    """One (item, gold IRI) miss with its cluster and every signal behind it. Gitignored output."""

    item_id: str
    firm: str
    stratum_id: str
    slice_name: str
    leaf: str
    input_text: str
    gold_iri: str
    gold_labels: tuple[str, ...]
    cluster: str
    reachability: str
    signals: MissSignals
    reachable_queries: tuple[str, ...] = ()
    probed_queries: tuple[str, ...] = ()
    gold_rank: int = 0
    top_candidate_iri: str = ""
    top_candidate_label: str = ""
    top_candidate_score: float = 0.0

    def to_json(self) -> dict[str, object]:
        return {
            "kind": "fn",
            "item_id": self.item_id,
            "firm": self.firm,
            "stratum_id": self.stratum_id,
            "slice": self.slice_name,
            "leaf": self.leaf,
            "input_text": self.input_text,
            "gold_iri": self.gold_iri,
            "gold_labels": list(self.gold_labels),
            "cluster": self.cluster,
            "reachability": self.reachability,
            "signals": self.signals.to_json(),
            "reachable_queries": list(self.reachable_queries),
            "probed_queries": list(self.probed_queries),
            "gold_rank": self.gold_rank,
            "top_candidate_iri": self.top_candidate_iri,
            "top_candidate_label": self.top_candidate_label,
            "top_candidate_score": self.top_candidate_score,
        }


@dataclass(frozen=True, slots=True)
class FalsePositiveRow:
    """One committed-but-wrong IRI with its hierarchy bucket. Gitignored output."""

    item_id: str
    firm: str
    stratum_id: str
    slice_name: str
    leaf: str
    predicted_iri: str
    predicted_label: str
    score: float
    probability: float
    rank: int
    near_miss: str
    cluster: str

    def to_json(self) -> dict[str, object]:
        return {
            "kind": "fp",
            "item_id": self.item_id,
            "firm": self.firm,
            "stratum_id": self.stratum_id,
            "slice": self.slice_name,
            "leaf": self.leaf,
            "predicted_iri": self.predicted_iri,
            "predicted_label": self.predicted_label,
            "score": self.score,
            "probability": round(self.probability, 6),
            "rank": self.rank,
            "near_miss": self.near_miss,
            "cluster": self.cluster,
        }


# --------------------------------------------------------------------------------------
# The clusterer
# --------------------------------------------------------------------------------------


@dataclass
class MissClassifier:
    """Turns one scored item into cluster-assigned miss and false-positive rows."""

    hierarchy: Hierarchy
    labels_for: Callable[[str], tuple[str, ...]]
    direct: CachedDirectSearch
    items_probed: int = 0

    def classify_item(
        self,
        score: ItemScore,
        *,
        ranked: Sequence[RankedCandidate],
    ) -> tuple[list[MissRow], list[FalsePositiveRow]]:
        """Classify every FN and every FP of one item.

        ``ranked`` is the item's **full** raw ranked list (``ItemScore.ranked`` is truncated for
        the CSV), so ``in_ranked`` is judged against everything the pipeline actually produced.
        """
        gold = set(score.gold_iris)
        committed = {candidate.iri for candidate in ranked if candidate.iri in set(score.committed_iris)}
        ranked_iris = [candidate.iri for candidate in ranked]
        ranked_set = set(ranked_iris)
        missing = sorted(gold - set(score.committed_iris))

        leaf_tokens = tokens(score.leaf)
        leaf_key = label_key(score.leaf)
        surface_keys = {leaf_key} | {label_key(variant) for variant in plural_variants(score.leaf)}
        surface_keys.discard("")

        # A homonym trap: the committed set contains a *different* concept whose label is exactly
        # the surface string. The pipeline was confident and took the wrong sense.
        homonym = any(
            label_key(candidate.label) in surface_keys and candidate.iri not in gold
            for candidate in ranked
            if candidate.iri in committed
        )

        # Probe the direct search only when something is actually absent from the ranked list --
        # a gold IRI the pipeline already ranked needs no reachability evidence.
        needs_probe = [iri for iri in missing if iri not in ranked_set]
        reachable: frozenset[str] = frozenset()
        per_query: dict[str, frozenset[str]] = {}
        variants: tuple[str, ...] = ()
        if needs_probe:
            variants = query_variants(score.leaf)
            reachable, per_query = self.direct.probe(variants)
            self.items_probed += 1

        top = ranked[0] if ranked else None
        miss_rows: list[MissRow] = []
        for gold_iri in missing:
            gold_labels = tuple(self.labels_for(gold_iri))
            in_ranked = gold_iri in ranked_set
            signals = MissSignals(
                in_ranked=in_ranked,
                reachable_at_limit=gold_iri in reachable,
                exact_label_match=any(label_key(label) in surface_keys for label in gold_labels),
                homonym_collision=homonym,
                hierarchy_neighbor_seen=bool(
                    (self.hierarchy.parents_of(gold_iri) | self.hierarchy.children_of(gold_iri))
                    & ranked_set
                ),
                max_token_jaccard=max(
                    (token_jaccard(leaf_tokens, tokens(label)) for label in gold_labels),
                    default=0.0,
                ),
            )
            cluster = classify_miss(signals)
            miss_rows.append(
                MissRow(
                    item_id=score.item_id,
                    firm=score.firm,
                    stratum_id=score.stratum_id,
                    slice_name=score.slice_name,
                    leaf=score.leaf,
                    input_text=score.input_text,
                    gold_iri=gold_iri,
                    gold_labels=gold_labels,
                    cluster=cluster,
                    reachability=reachability_of(cluster),
                    signals=signals,
                    reachable_queries=tuple(
                        sorted(query for query, hits in per_query.items() if gold_iri in hits)
                    ),
                    probed_queries=variants,
                    gold_rank=ranked_iris.index(gold_iri) + 1 if in_ranked else 0,
                    top_candidate_iri=top.iri if top else "",
                    top_candidate_label=top.label if top else "",
                    top_candidate_score=top.score if top else 0.0,
                )
            )

        fp_rows: list[FalsePositiveRow] = []
        for candidate in ranked:
            if candidate.iri not in committed or candidate.iri in gold:
                continue
            bucket = near_miss_bucket(candidate.iri, gold, self.hierarchy)
            fp_rows.append(
                FalsePositiveRow(
                    item_id=score.item_id,
                    firm=score.firm,
                    stratum_id=score.stratum_id,
                    slice_name=score.slice_name,
                    leaf=score.leaf,
                    predicted_iri=candidate.iri,
                    predicted_label=candidate.label,
                    score=candidate.score,
                    probability=candidate.probability,
                    rank=candidate.rank,
                    near_miss=bucket,
                    cluster=classify_false_positive(bucket),
                )
            )
        return miss_rows, sorted(fp_rows, key=lambda row: (row.item_id, row.predicted_iri))


@dataclass
class ClusterAnalysis:
    """Cluster rows plus the committed-eligible aggregate over them."""

    slice_name: str
    miss_rows: list[MissRow] = field(default_factory=list)
    fp_rows: list[FalsePositiveRow] = field(default_factory=list)
    items_probed: int = 0
    direct: Mapping[str, object] = field(default_factory=dict)
    elapsed_s: float = 0.0

    def cluster_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(MISS_CLUSTERS, 0)
        for row in self.miss_rows:
            counts[row.cluster] += 1
        return counts

    def reachability_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(REACHABILITY_CLASSES, 0)
        for row in self.miss_rows:
            counts[row.reachability] += 1
        return counts

    def fp_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(FP_CLUSTERS, 0)
        for row in self.fp_rows:
            counts[row.cluster] += 1
        return counts

    def clusters_by_firm(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for row in self.miss_rows:
            out.setdefault(row.firm, dict.fromkeys(MISS_CLUSTERS, 0))[row.cluster] += 1
        return dict(sorted(out.items()))

    def signal_counts(self) -> dict[str, int]:
        return {
            "exact_label_match": sum(1 for r in self.miss_rows if r.signals.exact_label_match),
            "homonym_collision": sum(1 for r in self.miss_rows if r.signals.homonym_collision),
            "hierarchy_neighbor_seen": sum(
                1 for r in self.miss_rows if r.signals.hierarchy_neighbor_seen
            ),
            "in_ranked": sum(1 for r in self.miss_rows if r.signals.in_ranked),
            "reachable_at_limit": sum(1 for r in self.miss_rows if r.signals.reachable_at_limit),
            "zero_token_overlap": sum(
                1 for r in self.miss_rows if r.signals.max_token_jaccard <= 0.0
            ),
        }

    def to_json(self) -> dict[str, object]:
        """Committed-eligible: counts and hashes only, never a surface string (KTD1)."""
        return {
            "slice": self.slice_name,
            "misses": len(self.miss_rows),
            "false_positives": len(self.fp_rows),
            "clusters": self.cluster_counts(),
            "reachability": self.reachability_counts(),
            "clusters_by_firm": self.clusters_by_firm(),
            "fp_clusters": self.fp_counts(),
            "signals": self.signal_counts(),
            "direct_search": dict(sorted(self.direct.items())),
            "items_probed": self.items_probed,
            "detail": "eval/data/reports/clusters_v1.jsonl (gitignored, KTD1)",
        }


def cluster_run(
    run: ScoreRun,
    *,
    classifier: MissClassifier,
    ranked_by_item: Mapping[str, Sequence[RankedCandidate]],
    progress_every: int = 100,
    stream: Any = None,
) -> ClusterAnalysis:
    """Cluster every miss and every false positive of one scoring run."""
    out = ClusterAnalysis(slice_name=run.slice_name)
    started = time.perf_counter()
    handle = stream if stream is not None else sys.stderr
    # The query cache is deliberately shared across slices, so this slice's cost is a delta.
    before = (classifier.direct.lookups, classifier.direct.unique_queries, classifier.items_probed)
    for index, score in enumerate(sorted(run.item_scores, key=lambda entry: entry.item_id), start=1):
        misses, fps = classifier.classify_item(score, ranked=ranked_by_item.get(score.item_id, ()))
        out.miss_rows.extend(misses)
        out.fp_rows.extend(fps)
        if progress_every and index % progress_every == 0:
            print(
                f"  cluster {run.slice_name}: {index}/{len(run.item_scores)} items "
                f"({len(out.miss_rows)} misses, {classifier.direct.unique_queries} unique queries)",
                file=handle,
                flush=True,
            )
    out.items_probed = classifier.items_probed - before[2]
    out.direct = {
        "limit": classifier.direct.limit,
        "lookups": classifier.direct.lookups - before[0],
        "new_unique_queries": classifier.direct.unique_queries - before[1],
        "cache_unique_queries_total": classifier.direct.unique_queries,
    }
    out.elapsed_s = time.perf_counter() - started
    return out


# --------------------------------------------------------------------------------------
# Calibration and the threshold x k grid (KTD2)
# --------------------------------------------------------------------------------------

#: KTD2's grid: thresholds 0.00-0.90 in 0.05 steps, k 1-10.
#:
#: The k range is exhaustive at baseline -- ``label_search_limit=10`` caps the ranked list at 10,
#: so k=10 is "no cap". The threshold range deliberately starts at **0.00** rather than at the
#: 0.30 the plan sketched: an isotonic fit on this data is a coarse step function whose highest
#: pool sits far below 0.9, so a 0.30-0.90 grid can only express two or three distinct score cuts
#: and the F1 optimum would be an artifact of where the grid happened to stop. 0.00 (commit the
#: whole ranked list, capped at k) is a meaningful endpoint and makes the frontier readable.
DEFAULT_THRESHOLD_GRID: tuple[float, ...] = tuple(round(0.05 * step, 2) for step in range(19))
DEFAULT_K_GRID: tuple[int, ...] = tuple(range(1, 11))

#: Probability precision the fitted steps are rounded to before serialization.
CALIBRATION_PRECISION = 6


def calibration_samples(
    ranked_by_item: Mapping[str, Sequence[RankedCandidate]],
    gold_by_item: Mapping[str, frozenset[str]],
) -> list[CalibrationSample]:
    """``(raw score, correct?)`` pairs from the raw ranked lists -- KTD2's fitting dataset.

    Sorted by ``(score asc, target desc)`` on purpose. ``ScoreCalibration.fit`` sorts by score
    with a *stable* sort and then runs pool-adjacent-violators, so the order of tied scores
    decides the fit: ``[0, 1]`` at one score does not violate monotonicity and survives as two
    separate steps (the later one wins in ``probability()``), whereas ``[1, 0]`` pools to the
    correct mean. Feeding descending targets within each tied score therefore makes the fit both
    *correct* (every tied block collapses to its true mean) and *deterministic* (independent of
    dict iteration order, per KTD7).
    """
    samples: list[tuple[float, float, str]] = []
    for item_id in sorted(ranked_by_item):
        gold = gold_by_item.get(item_id, frozenset())
        for candidate in ranked_by_item[item_id]:
            verdict = "correct" if candidate.iri in gold else "wrong"
            samples.append((candidate.score, 1.0 if verdict == "correct" else 0.0, verdict))
    samples.sort(key=lambda entry: (entry[0], -entry[1]))
    return [CalibrationSample(score=score, verdict=verdict) for score, _, verdict in samples]


def compress_steps(
    steps: Sequence[tuple[float, float]], *, precision: int = CALIBRATION_PRECISION
) -> tuple[tuple[float, float], ...]:
    """Round probabilities and drop steps that repeat the previous probability.

    Lossless: ``ScoreCalibration.probability`` returns the *last* step whose x is <= the score, so
    a run of steps sharing one probability is indistinguishable from its first member. Keeps the
    serialized config small enough to read without changing a single answer.
    """
    out: list[tuple[float, float]] = []
    for x, probability in steps:
        rounded = round(probability, precision)
        if out and out[-1][1] == rounded:
            continue
        out.append((float(x), rounded))
    return tuple(out)


def fit_calibration(samples: Sequence[CalibrationSample]) -> tuple[tuple[float, float], ...]:
    """Fit ``ScoreCalibration`` on the tune slice and return its compressed steps."""
    calibration = ScoreCalibration.fit(samples)
    raw = list(getattr(calibration, "_steps", []) or [])
    return compress_steps(raw)


@dataclass(frozen=True, slots=True)
class GridPoint:
    """Micro-averaged counts for one ``(threshold, k)`` cell of the sweep."""

    threshold: float
    top_k: int
    tp: int
    fp: int
    fn: int

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
            "threshold": self.threshold,
            "top_k": self.top_k,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "f1": round(self.f1, 6),
        }


def grid_search(
    ranked_by_item: Mapping[str, Sequence[RankedCandidate]],
    gold_by_item: Mapping[str, frozenset[str]],
    *,
    calibration_steps: Sequence[tuple[float, float]] = (),
    thresholds: Sequence[float] = DEFAULT_THRESHOLD_GRID,
    ks: Sequence[int] = DEFAULT_K_GRID,
) -> list[GridPoint]:
    """Sweep threshold x k over already-ranked candidates. **Tune slice only** (KTD2).

    The sweep chooses two global constants. It never makes the *rule* gold-aware: at scoring time
    :func:`answer_rule.commit_from_ranked` still sees only candidates and a config, so an item's
    committed set is independent of how large its gold set happens to be.
    """
    grid: list[GridPoint] = []
    item_ids = sorted(ranked_by_item)
    for threshold in thresholds:
        for top_k in ks:
            config = AnswerRuleConfig(
                threshold=threshold,
                top_k=top_k,
                calibrated=True,
                calibration_steps=tuple(calibration_steps),
            )
            tp = fp = fn = 0
            for item_id in item_ids:
                gold = gold_by_item.get(item_id, frozenset())
                committed = {
                    candidate.iri
                    for candidate in commit_from_ranked(ranked_by_item[item_id], config)
                }
                tp += len(committed & gold)
                fp += len(committed - gold)
                fn += len(gold - committed)
            grid.append(GridPoint(threshold=threshold, top_k=top_k, tp=tp, fp=fp, fn=fn))
    return grid


def select_point(grid: Sequence[GridPoint]) -> GridPoint:
    """Highest micro-F1; ties broken toward the *smaller* k, then the *higher* threshold.

    Both tie-breaks favour the more conservative rule, and both are total orders over the grid, so
    the selection is reproducible from the same inputs under any hash seed.
    """
    if not grid:
        raise ValueError("empty grid")
    return min(grid, key=lambda point: (-point.f1, point.top_k, -point.threshold))


def frontier_around(
    grid: Sequence[GridPoint],
    chosen: GridPoint,
    *,
    thresholds: Sequence[float] = DEFAULT_THRESHOLD_GRID,
) -> list[GridPoint]:
    """The chosen cell and its immediate neighbours -- how flat or sharp the optimum is."""
    order = list(thresholds)
    try:
        centre = order.index(chosen.threshold)
    except ValueError:  # pragma: no cover - only if a caller passes a foreign grid
        centre = 0
    keep = {order[index] for index in range(max(0, centre - 1), min(len(order), centre + 2))}
    return [
        point
        for point in grid
        if point.threshold in keep and abs(point.top_k - chosen.top_k) <= 1
    ]


def calibration_rationale(
    *,
    chosen: GridPoint,
    samples: int,
    positives: int,
    items: int,
    steps: int,
    gold_id: str,
    ontology_sha256: str,
    thresholds: Sequence[float] = DEFAULT_THRESHOLD_GRID,
    ks: Sequence[int] = DEFAULT_K_GRID,
) -> str:
    """The one-line rationale U4 owes the pinned config. Counts and hashes only -- no surfaces."""
    return (
        f"U4 baseline fit: isotonic ScoreCalibration on the TUNE slice only "
        f"({items} items, {samples} (score, correct?) pairs, {positives} positive, "
        f"{steps} compressed steps); threshold x k grid-searched over "
        f"[{thresholds[0]}..{thresholds[-1]}] x [{ks[0]}..{ks[-1]}] maximizing micro-F1, ties to "
        f"the smaller k then the higher threshold; chosen threshold={chosen.threshold} "
        f"top_k={chosen.top_k} (tune micro-F1={chosen.f1:.6f}, P={chosen.precision:.6f}, "
        f"R={chosen.recall:.6f}); gold={gold_id}; ontology={ontology_sha256[:12]}. "
        f"Refitting is a named iteration change under R9."
    )


# --------------------------------------------------------------------------------------
# The pipeline pass (run once, replay many times)
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RawCandidate:
    """The slice of ``MatchCandidate`` the answer rule reads, detached from the pipeline."""

    iri: str
    label: str
    score: float
    extraction_path: str = ""
    gated: bool = False


def collect_raw_candidates(
    items: Sequence[GoldItemRecord],
    predict: Callable[[GoldItemRecord], Sequence[Any]],
    *,
    label: str = "",
    progress_every: int = 100,
    stream: Any = None,
) -> dict[str, tuple[RawCandidate, ...]]:
    """Run the pipeline once per item and cache its raw candidate list.

    Everything downstream -- the calibration fit, the grid sweep, the scored run, the clusters --
    replays this cache, so the baseline's numbers all describe one and the same pipeline pass.
    """
    handle = stream if stream is not None else sys.stderr
    cache: dict[str, tuple[RawCandidate, ...]] = {}
    started = time.perf_counter()
    ordered = sorted(items, key=lambda record: record.item_id)
    for index, item in enumerate(ordered, start=1):
        cache[item.item_id] = tuple(
            RawCandidate(
                iri=candidate.iri,
                label=candidate.label,
                score=float(candidate.score),
                extraction_path=getattr(candidate, "extraction_path", ""),
                gated=bool(getattr(candidate, "gated", False)),
            )
            for candidate in predict(item)
        )
        if progress_every and index % progress_every == 0:
            elapsed = time.perf_counter() - started
            print(
                f"  match {label or 'items'}: {index}/{len(ordered)} "
                f"({elapsed:.1f}s, {elapsed * 1000 / index:.0f} ms/item)",
                file=handle,
                flush=True,
            )
    return cache


@dataclass(frozen=True, slots=True)
class ReplayPredictor:
    """A ``score.Predictor`` that returns cached candidates instead of calling the pipeline."""

    cache: Mapping[str, tuple[RawCandidate, ...]]

    def __call__(self, item: GoldItemRecord) -> Sequence[RawCandidate]:
        return self.cache.get(item.item_id, ())


def rank_all(
    cache: Mapping[str, tuple[RawCandidate, ...]], config: AnswerRuleConfig
) -> dict[str, list[RankedCandidate]]:
    """Deterministically rank every cached candidate list under one config."""
    return {item_id: rank_candidates(cache[item_id], config) for item_id in sorted(cache)}


# --------------------------------------------------------------------------------------
# Leak scan (KTD1)
# --------------------------------------------------------------------------------------

#: Surfaces shorter than this are skipped -- three-letter fragments collide with ordinary words.
MIN_SURFACE_SCAN_LENGTH = 4


def surface_strings(gold: GoldSet, *, min_length: int = MIN_SURFACE_SCAN_LENGTH) -> tuple[str, ...]:
    """Every firm surface string a committed artefact must never contain (KTD1)."""
    out: set[str] = set()
    for item in gold.items:
        for value in (item.leaf, item.input_text, item.stratum, *item.ancestor_path):
            text = (value or "").strip()
            if len(text) >= min_length:
                out.add(text)
                normalized = normalize_label(text)
                if len(normalized) >= min_length:
                    out.add(normalized)
    return tuple(sorted(out))


def scan_for_surfaces(text: str, surfaces: Iterable[str]) -> list[str]:
    """Every gold surface string that literally occurs in ``text``. Empty list = clean."""
    return sorted({surface for surface in surfaces if surface in text})


class SurfaceLeakError(RuntimeError):
    """Raised when a committed-bound artefact contains a firm surface string (KTD1)."""


def assert_no_surfaces(text: str, surfaces: Iterable[str], *, what: str) -> int:
    """Abort rather than commit a leak. Returns the number of surfaces scanned."""
    scanned = tuple(surfaces)
    hits = scan_for_surfaces(text, scanned)
    if hits:
        raise SurfaceLeakError(
            f"{what} contains {len(hits)} firm surface string(s) — refusing to write a committed "
            f"artefact (KTD1). First offenders (hashed for this message): "
            f"{[len(hit) for hit in hits[:5]]} chars"
        )
    return len(scanned)


# --------------------------------------------------------------------------------------
# Row-level output (gitignored)
# --------------------------------------------------------------------------------------

DEFAULT_CLUSTER_DETAIL_NAME = "clusters_v1.jsonl"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def write_cluster_detail(analyses: Sequence[ClusterAnalysis], path: Path) -> Path:
    """One JSON object per miss and per false positive, ordered so reruns diff cleanly."""
    lines: list[str] = []
    for analysis in analyses:
        for row in sorted(analysis.miss_rows, key=lambda entry: (entry.item_id, entry.gold_iri)):
            lines.append(json.dumps(row.to_json(), ensure_ascii=False, sort_keys=True))
        for fp_row in sorted(
            analysis.fp_rows, key=lambda entry: (entry.item_id, entry.predicted_iri)
        ):
            lines.append(json.dumps(fp_row.to_json(), ensure_ascii=False, sort_keys=True))
    _atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return path


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Fit the answer rule on tune, score tune + firm2, cluster the misses, emit the baseline."""
    import argparse

    from .answer_rule import DEFAULT_CONFIG_FILENAME, write_config
    from .report import (
        DEFAULT_ITEM_REPORT_DIR,
        DEFAULT_SUMMARY_DIR,
        build_summary,
        canonical_json,
        write_item_csv,
    )
    from .resolve_labels import folio_python_version
    from .score import Hierarchy as _Hierarchy
    from .score import PipelineAdapter, build_folio_provider, build_pipeline, score_items
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
        SIGNAL_SLICE,
        TUNE_SLICE,
        load_gold,
        load_split_manifest,
    )

    parser = argparse.ArgumentParser(
        prog="python -m folio_eval.clusters",
        description="U4 baseline: calibrate on tune, score tune + firm2, cluster every miss.",
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR / "gold_v1.jsonl")
    parser.add_argument("--gold-manifest", type=Path, default=None)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--config", type=Path, default=DEFAULT_GOLD_DIR / DEFAULT_CONFIG_FILENAME)
    parser.add_argument("--out", type=Path, default=DEFAULT_SUMMARY_DIR / "baseline-v1.json")
    parser.add_argument("--item-report-dir", type=Path, default=DEFAULT_ITEM_REPORT_DIR)
    parser.add_argument(
        "--cluster-detail",
        type=Path,
        default=DEFAULT_ITEM_REPORT_DIR / DEFAULT_CLUSTER_DETAIL_NAME,
    )
    parser.add_argument("--label", default="baseline-v1")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="score only the first N items of each slice (determinism spot-checks; marks the report sampled)",
    )
    parser.add_argument("--label-search-limit", type=int, default=10)
    parser.add_argument("--direct-search-limit", type=int, default=DIRECT_SEARCH_LIMIT)
    parser.add_argument("--no-entity-ruler", action="store_true")
    parser.add_argument(
        "--no-write-config",
        action="store_true",
        help="fit and report the calibration without rewriting the pinned harness config",
    )
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--determinism-target", default=DEFAULT_SELFTEST_TARGET)
    parser.add_argument("--allow-ontology-bump", action="store_true")
    args = parser.parse_args(argv)

    ensure_hash_seed()
    started = time.perf_counter()

    gold = load_gold(args.gold, manifest_path=args.gold_manifest)
    try:
        pin = assert_ontology_pin(gold.ontology_cache_sha256)
    except OntologyPinError as error:
        if not args.allow_ontology_bump:
            print(f"ABORT: {error}", file=sys.stderr)
            return 2
        print(f"WARNING (--allow-ontology-bump): {error}", file=sys.stderr)
        pin = assert_ontology_pin("")
    gold = load_gold(
        args.gold, manifest_path=args.gold_manifest, ontology_sha256=gold.ontology_cache_sha256
    )

    selftest = run_determinism_selftest(args.determinism_target)
    print(
        f"determinism self-test OK ({selftest.first_sha256[:12]}, target={selftest.target})",
        file=sys.stderr,
    )
    print(f"ontology pin OK ({pin.sha256[:12]}) {pin.path}", file=sys.stderr)

    split = load_split_manifest(args.split_manifest, gold)

    def slice_items(name: str) -> list[GoldItemRecord]:
        picked = sorted(split.slice_items(name, gold), key=lambda record: record.item_id)
        return picked[: args.limit] if args.limit else picked

    tune_items = slice_items(TUNE_SLICE)
    firm2_items = slice_items(SIGNAL_SLICE)
    if not tune_items:
        print("tune slice is empty — nothing to baseline", file=sys.stderr)
        return 1

    from folio import FOLIO

    folio = FOLIO()
    provider = build_folio_provider(folio)
    pipeline = build_pipeline(
        provider,
        with_entity_ruler=not args.no_entity_ruler,
        label_search_limit=args.label_search_limit,
    )
    hierarchy: Hierarchy = _Hierarchy.from_folio(folio)
    label_map = folio_label_map(folio)
    adapter = PipelineAdapter(pipeline)

    print(f"matching tune ({len(tune_items)} items)…", file=sys.stderr)
    tune_cache = collect_raw_candidates(
        tune_items, adapter, label=TUNE_SLICE, progress_every=args.progress_every
    )
    print(f"matching firm2 ({len(firm2_items)} items)…", file=sys.stderr)
    firm2_cache = collect_raw_candidates(
        firm2_items, adapter, label=SIGNAL_SLICE, progress_every=args.progress_every
    )

    # -- calibration + grid search, TUNE ONLY (KTD2) ------------------------------------
    gold_by_item = {item.item_id: frozenset(item.gold_iris) for item in tune_items}
    uncalibrated = AnswerRuleConfig()
    tune_ranked_raw = rank_all(tune_cache, uncalibrated)
    samples = calibration_samples(tune_ranked_raw, gold_by_item)
    steps = fit_calibration(samples)
    positives = sum(1 for sample in samples if sample.verdict == "correct")

    tune_ranked = rank_all(
        tune_cache,
        AnswerRuleConfig(calibrated=True, calibration_steps=steps),
    )
    grid = grid_search(tune_ranked, gold_by_item, calibration_steps=steps)
    chosen = select_point(grid)
    frontier = frontier_around(grid, chosen)
    print(
        f"calibration: {len(samples)} pairs ({positives} positive) -> {len(steps)} steps; "
        f"chosen threshold={chosen.threshold} k={chosen.top_k} tune-F1={chosen.f1:.4f}",
        file=sys.stderr,
    )

    config = AnswerRuleConfig(
        threshold=chosen.threshold,
        top_k=chosen.top_k,
        calibrated=True,
        calibration_steps=steps,
        rationale=calibration_rationale(
            chosen=chosen,
            samples=len(samples),
            positives=positives,
            items=len(tune_items),
            steps=len(steps),
            gold_id=gold.gold_id,
            ontology_sha256=pin.sha256,
        ),
    )
    if not args.no_write_config:
        write_config(config, args.config)
        print(f"harness config written: {args.config} ({config.content_sha256()[:12]})", file=sys.stderr)

    # -- scored runs (replayed from the single pipeline pass) ----------------------------
    runs = {}
    caches = {TUNE_SLICE: tune_cache, SIGNAL_SLICE: firm2_cache}
    slices = {TUNE_SLICE: tune_items, SIGNAL_SLICE: firm2_items}
    for name in (TUNE_SLICE, SIGNAL_SLICE):
        if not slices[name]:
            continue
        runs[name] = score_items(
            slices[name],
            ReplayPredictor(caches[name]),
            config=config,
            hierarchy=hierarchy,
            slice_name=name,
        )

    # -- clustering ---------------------------------------------------------------------
    direct = CachedDirectSearch(
        folio_direct_search(folio, limit=args.direct_search_limit),
        limit=args.direct_search_limit,
    )
    classifier = MissClassifier(
        hierarchy=hierarchy,
        labels_for=lambda iri: label_map.get(iri, ()),
        direct=direct,
    )
    analyses: dict[str, ClusterAnalysis] = {}
    for name, run in runs.items():
        print(f"clustering {name} ({len(run.item_scores)} items)…", file=sys.stderr)
        analyses[name] = cluster_run(
            run,
            classifier=classifier,
            ranked_by_item=rank_all(caches[name], config),
            progress_every=args.progress_every,
        )

    # -- outputs ------------------------------------------------------------------------
    summaries = {
        name: build_summary(
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
                "entity_ruler": not args.no_entity_ruler,
                "label_search_limit": args.label_search_limit,
            },
        )
        for name, run in runs.items()
    }

    baseline: dict[str, object] = {
        "label": args.label,
        "kind": "baseline",
        "sampled": bool(args.limit),
        "gold_id": gold.gold_id,
        "gold_version": gold.gold_version,
        "gold_content_sha256": gold.content_sha256,
        "ontology_cache_sha256": pin.sha256,
        "folio_python_version": folio_python_version(),
        "harness_config_sha256": config.content_sha256(),
        "harness_config": config.to_json(),
        "split_manifest_sha256": split.content_sha256,
        "split_seed": split.seed,
        "determinism_selftest": selftest.to_json(),
        "calibration": {
            "fitted_on": TUNE_SLICE,
            "items": len(tune_items),
            "samples": len(samples),
            "positive_samples": positives,
            "steps": len(steps),
            "threshold_grid": list(DEFAULT_THRESHOLD_GRID),
            "k_grid": list(DEFAULT_K_GRID),
            "chosen": chosen.to_json(),
            "frontier": [point.to_json() for point in frontier],
            "grid_top10": [
                point.to_json()
                for point in sorted(grid, key=lambda p: (-p.f1, p.top_k, -p.threshold))[:10]
            ],
        },
        "slices": {name: summaries[name] for name in sorted(summaries)},
        "clusters": {name: analyses[name].to_json() for name in sorted(analyses)},
        "timing": {
            "elapsed_s": round(time.perf_counter() - started, 3),
            "items": sum(len(run.item_scores) for run in runs.values()),
        },
    }

    payload = canonical_json(baseline)
    scanned = assert_no_surfaces(payload, surface_strings(gold), what=str(args.out))
    _atomic_write_text(args.out, payload)
    detail_path = write_cluster_detail(
        [analyses[name] for name in sorted(analyses)], args.cluster_detail
    )
    csv_paths = [
        write_item_csv(
            run.item_scores,
            args.item_report_dir / f"items-{gold.gold_id}-{name}-{args.label}.csv",
        )
        for name, run in sorted(runs.items())
    ]

    print(
        canonical_json(
            {
                "calibration": baseline["calibration"],
                "clusters": baseline["clusters"],
                "overall": {name: summaries[name]["overall"] for name in sorted(summaries)},
                "recall_at_k": {name: summaries[name]["recall_at_k"] for name in sorted(summaries)},
            }
        )
    )
    print(f"baseline report (committed dir): {args.out}  [leak scan: {scanned} surfaces, 0 hits]")
    print(f"cluster detail (gitignored): {detail_path}")
    for path in csv_paths:
        print(f"per-item CSV (gitignored): {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
