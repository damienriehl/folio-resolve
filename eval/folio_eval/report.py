"""Run reports and bootstrap confidence intervals (U3; R5, R6, R7; KTD1, KTD2, KTD7).

Two output surfaces, split exactly along KTD1's line:

* **``eval/reports/`` (committed)** — the run summary JSON: counts, metrics, recall@k, near-miss
  buckets, and the identity hashes (gold id + content hash, ontology cache hash, harness config
  hash, split manifest hash). Aggregates keyed by ``stratum_id`` only. No surface strings, no
  labels, no per-row anything. This is what a future session re-reads to compare runs.
* **``eval/data/reports/`` (gitignored)** — the per-item CSV. Row-level output carries firm
  surface strings and gold IRIs, so it never leaves the machine; it exists so any aggregation
  above can be recomputed offline and so U4's failure clustering has its input.

The bootstrap helpers feed the AE4 tripwire: a percentile bootstrap over *items* (fixed seed,
so a rerun reproduces the interval bit-for-bit) for the frozen-slice F1 delta and for the Firm-2
changed-item count. R6 forbids reporting Firm 2 as a bare F1 delta, so the Firm-2 statistic is a
signed changed-item count with its interval.
"""

from __future__ import annotations

import csv
import json
import os
import random
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .answer_rule import AnswerRuleConfig
from .intake import DEFAULT_DATA_DIR
from .score import RECALL_AT_K, ItemScore, ScoreRun
from .splits import GoldSet, SplitManifest

# eval/folio_eval/report.py -> eval/
_EVAL_ROOT = Path(__file__).resolve().parent.parent

#: Committed: ID-keyed aggregates only (KTD1).
DEFAULT_SUMMARY_DIR = _EVAL_ROOT / "reports"
#: Gitignored: row-level output (KTD1).
DEFAULT_ITEM_REPORT_DIR = DEFAULT_DATA_DIR / "reports"

DEFAULT_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_BOOTSTRAP_SEED = 20260727


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


# --------------------------------------------------------------------------------------
# Run summary (committed)
# --------------------------------------------------------------------------------------


def build_summary(
    run: ScoreRun,
    *,
    gold: GoldSet,
    config: AnswerRuleConfig,
    split_manifest: SplitManifest | None = None,
    ontology_cache_sha256: str = "",
    folio_python_version: str = "",
    label: str = "",
    sampled: bool = False,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The committed-eligible aggregate. Everything here is a count, a metric, or a hash."""
    summary: dict[str, object] = {
        "label": label,
        "slice": run.slice_name,
        "sampled": sampled,
        "gold_id": gold.gold_id,
        "gold_version": gold.gold_version,
        "gold_content_sha256": gold.content_sha256,
        "ontology_cache_sha256": ontology_cache_sha256 or gold.ontology_cache_sha256,
        "folio_python_version": folio_python_version,
        "harness_config_sha256": config.content_sha256(),
        "harness_config": config.to_json(),
        "split_manifest_sha256": split_manifest.content_sha256 if split_manifest else "",
        "split_seed": split_manifest.seed if split_manifest else None,
        "overall": run.overall.to_json(),
        "by_firm": {firm: counts.to_json() for firm, counts in sorted(run.by_firm.items())},
        "by_stratum": {
            stratum_id: counts.to_json() for stratum_id, counts in sorted(run.by_stratum.items())
        },
        "recall_at_k": {str(k): round(run.recall_at_k[k], 6) for k in RECALL_AT_K},
        "hits_at_k": {str(k): run.hits_at_k.get(k, 0) for k in RECALL_AT_K},
        "near_miss": {key: run.near_miss[key] for key in sorted(run.near_miss)},
        "timing": {
            "elapsed_s": round(run.elapsed_s, 3),
            "items": run.overall.items,
            "ms_per_item": round(run.elapsed_s * 1000.0 / run.overall.items, 2)
            if run.overall.items
            else 0.0,
        },
    }
    if extra:
        summary["extra"] = dict(sorted(extra.items()))
    return summary


def canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_summary(summary: Mapping[str, object], path: Path) -> Path:
    _atomic_write_text(path, canonical_json(summary))
    return path


# --------------------------------------------------------------------------------------
# Per-item CSV (gitignored)
# --------------------------------------------------------------------------------------

ITEM_CSV_FIELDS = (
    "item_id",
    "firm",
    "stratum_id",
    "slice",
    "input_text",
    "leaf",
    "gold_count",
    "gold_iris",
    "committed_count",
    "committed_iris",
    "tp",
    "fp",
    "fn",
    "exact",
    "ranked_top10",
    "ranked_top10_scores",
    "hits_at_1",
    "hits_at_3",
    "hits_at_5",
    "hits_at_10",
    "near_miss_parent_1hop",
    "near_miss_child_1hop",
    "near_miss_ancestor_2hop",
    "near_miss_descendant_2hop",
    "near_miss_sibling",
    "near_miss_unrelated",
    "elapsed_ms",
)


def write_item_csv(item_scores: Sequence[ItemScore], path: Path) -> Path:
    """Row-level output, gitignored (KTD1). Ordered by ``item_id`` so reruns diff cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(ITEM_CSV_FIELDS))
            writer.writeheader()
            for score in sorted(item_scores, key=lambda entry: entry.item_id):
                writer.writerow(
                    {
                        "item_id": score.item_id,
                        "firm": score.firm,
                        "stratum_id": score.stratum_id,
                        "slice": score.slice_name,
                        "input_text": score.input_text,
                        "leaf": score.leaf,
                        "gold_count": len(score.gold_iris),
                        "gold_iris": " ".join(score.gold_iris),
                        "committed_count": len(score.committed_iris),
                        "committed_iris": " ".join(score.committed_iris),
                        "tp": score.tp,
                        "fp": score.fp,
                        "fn": score.fn,
                        "exact": int(score.exact),
                        "ranked_top10": " ".join(score.ranked_iris[:10]),
                        "ranked_top10_scores": " ".join(
                            f"{candidate.score:g}" for candidate in score.ranked[:10]
                        ),
                        "hits_at_1": score.hits_at_k.get(1, 0),
                        "hits_at_3": score.hits_at_k.get(3, 0),
                        "hits_at_5": score.hits_at_k.get(5, 0),
                        "hits_at_10": score.hits_at_k.get(10, 0),
                        "near_miss_parent_1hop": score.near_miss.get("parent_1hop", 0),
                        "near_miss_child_1hop": score.near_miss.get("child_1hop", 0),
                        "near_miss_ancestor_2hop": score.near_miss.get("ancestor_2hop", 0),
                        "near_miss_descendant_2hop": score.near_miss.get("descendant_2hop", 0),
                        "near_miss_sibling": score.near_miss.get("sibling", 0),
                        "near_miss_unrelated": score.near_miss.get("unrelated", 0),
                        "elapsed_ms": round(score.elapsed_ms, 3),
                    }
                )
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path


# --------------------------------------------------------------------------------------
# Bootstrap confidence intervals (AE4 tripwire inputs)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BootstrapCI:
    """A percentile bootstrap interval, reproducible from ``seed`` + ``n_resamples``."""

    point: float
    low: float
    high: float
    n_units: int
    n_resamples: int
    seed: int
    alpha: float

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0.0 or self.high < 0.0

    def to_json(self) -> dict[str, object]:
        return {
            "point": round(self.point, 6),
            "low": round(self.low, 6),
            "high": round(self.high, 6),
            "excludes_zero": self.excludes_zero,
            "n_units": self.n_units,
            "n_resamples": self.n_resamples,
            "seed": self.seed,
            "alpha": self.alpha,
        }


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = round(fraction * (len(sorted_values) - 1))
    return sorted_values[max(0, min(index, len(sorted_values) - 1))]


_Unit = TypeVar("_Unit")


def bootstrap_ci(
    units: Sequence[_Unit],
    statistic: Callable[[Sequence[_Unit]], float],
    *,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> BootstrapCI:
    """Percentile bootstrap over independent units (items), with a pinned seed (R7)."""
    if not units:
        return BootstrapCI(0.0, 0.0, 0.0, 0, n_resamples, seed, alpha)
    rng = random.Random(seed)
    size = len(units)
    draws: list[float] = []
    for _ in range(n_resamples):
        sample = [units[rng.randrange(size)] for _ in range(size)]
        draws.append(statistic(sample))
    draws.sort()
    return BootstrapCI(
        point=statistic(units),
        low=_percentile(draws, alpha / 2.0),
        high=_percentile(draws, 1.0 - alpha / 2.0),
        n_units=size,
        n_resamples=n_resamples,
        seed=seed,
        alpha=alpha,
    )


@dataclass(frozen=True, slots=True)
class PairedItem:
    """One item's before/after counts — the resampling unit for both tripwire statistics."""

    item_id: str
    before_tp: int
    before_fp: int
    before_fn: int
    after_tp: int
    after_fp: int
    after_fn: int
    before_exact: bool
    after_exact: bool

    @property
    def change(self) -> int:
        """+1 incorrect->correct, -1 correct->incorrect, 0 unchanged (AE4's unit)."""
        if self.after_exact and not self.before_exact:
            return 1
        if self.before_exact and not self.after_exact:
            return -1
        return 0


def pair_items(before: Iterable[ItemScore], after: Iterable[ItemScore]) -> list[PairedItem]:
    """Pair two runs by ``item_id``; items missing from either side are dropped deterministically."""
    before_by_id = {score.item_id: score for score in before}
    after_by_id = {score.item_id: score for score in after}
    return [
        PairedItem(
            item_id=item_id,
            before_tp=before_by_id[item_id].tp,
            before_fp=before_by_id[item_id].fp,
            before_fn=before_by_id[item_id].fn,
            after_tp=after_by_id[item_id].tp,
            after_fp=after_by_id[item_id].fp,
            after_fn=after_by_id[item_id].fn,
            before_exact=before_by_id[item_id].exact,
            after_exact=after_by_id[item_id].exact,
        )
        for item_id in sorted(set(before_by_id) & set(after_by_id))
    ]


def _micro_f1(tp: int, fp: int, fn: int) -> float:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def f1_delta(units: Sequence[PairedItem]) -> float:
    """Micro-F1(after) - micro-F1(before) over a (possibly resampled) set of items."""
    before = _micro_f1(
        sum(unit.before_tp for unit in units),
        sum(unit.before_fp for unit in units),
        sum(unit.before_fn for unit in units),
    )
    after = _micro_f1(
        sum(unit.after_tp for unit in units),
        sum(unit.after_fp for unit in units),
        sum(unit.after_fn for unit in units),
    )
    return after - before


def net_changed_items(units: Sequence[PairedItem]) -> float:
    """Signed changed-item count: improvements minus regressions (R6's Firm-2 statistic)."""
    return float(sum(unit.change for unit in units))


def f1_delta_ci(
    before: Iterable[ItemScore],
    after: Iterable[ItemScore],
    *,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> BootstrapCI:
    """CI for the frozen-slice F1 delta (Success Criteria: must exclude zero to count)."""
    return bootstrap_ci(
        pair_items(before, after), f1_delta, n_resamples=n_resamples, seed=seed, alpha=alpha
    )


def changed_item_ci(
    before: Iterable[ItemScore],
    after: Iterable[ItemScore],
    *,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> BootstrapCI:
    """CI for the Firm-2 signed changed-item count (never a bare F1 delta, per R6)."""
    return bootstrap_ci(
        pair_items(before, after), net_changed_items, n_resamples=n_resamples, seed=seed, alpha=alpha
    )


def changed_item_breakdown(units: Sequence[PairedItem]) -> dict[str, int]:
    """The raw AE4 inputs behind the interval: how many items moved, and which way."""
    return {
        "items": len(units),
        "improved": sum(1 for unit in units if unit.change > 0),
        "regressed": sum(1 for unit in units if unit.change < 0),
        "unchanged": sum(1 for unit in units if unit.change == 0),
        "net": int(net_changed_items(units)),
    }
