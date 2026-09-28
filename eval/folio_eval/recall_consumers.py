"""Machine-local, resumable consumer arms with conservative prepaid spend accounting.

No raw provider responses or credentials are persisted. Token bounds must include
all pipeline calls, retries, and thinking tokens per item; absent usage telemetry,
we charge the entire bound, including failed or interrupted invocations.
"""

from __future__ import annotations

import fcntl
import json
import re
import subprocess
from collections.abc import Mapping, Sequence, Set
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .comparison import (
    ComparisonError,
    StackRun,
    _assert_consumer_config,
    _assert_consumer_llm_rows,
    _assert_consumer_rows,
    _atomic_write_text,
    _git_repository_state,
    run_consumer_stack,
)
from .downstream import FOLIO_RESOLVE_ROOT, ConsumerSpec
from .grade import DEFAULT_FLOOR
from .intake import sha256_text
from .recall_attribution import STAGES
from .score import MicroCounts

CAP_USD = Decimal("25")


class SpendLimitError(ComparisonError):
    """The next call or projected campaign exceeds the authorized total."""


class PriceUnavailable(ComparisonError):
    """A dated, sourced model price is missing."""


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: Decimal
    output_per_million: Decimal
    date: str
    source: str

    def __post_init__(self) -> None:
        if (
            not self.date
            or not self.source
            or any(
                not p.is_finite() or p < 0
                for p in (self.input_per_million, self.output_per_million)
            )
        ):
            raise PriceUnavailable("invalid dated model price")


# User-supplied pins; Gemini output pricing includes thinking tokens.
PINNED_PRICES: Mapping[str, ModelPrice | None] = {
    "gemini-3-flash-preview": ModelPrice(
        Decimal("0.50"),
        Decimal("3.00"),
        "2026-09-24",
        "https://ai.google.dev/gemini-api/docs/pricing",
    ),
    "gpt-6-luna": ModelPrice(
        Decimal("0.10"),
        Decimal("0.50"),
        "2026-09-27",
        "https://developers.openai.com/api/docs/models/gpt-6-luna",
    ),
}


def pinned_price(
    model: str,
    prices: Mapping[str, ModelPrice | None] = PINNED_PRICES,
) -> ModelPrice:
    """Return a dated price or fail before any paid invocation."""
    price = prices.get(model)
    if price is None:
        raise PriceUnavailable("missing pinned price for consumer model")
    return price


@dataclass(frozen=True)
class TokenBound:
    """Aggregate worst-case tokens per item across ALL calls and retries."""

    input_tokens: int
    output_tokens: int

    def cost(self, price: ModelPrice) -> Decimal:
        if any(type(n) is not int or n < 0 for n in (self.input_tokens, self.output_tokens)):
            raise ComparisonError("invalid conservative token bound")
        if self.input_tokens + self.output_tokens == 0:
            raise ComparisonError("paid arms require a positive conservative token bound")
        return (
            self.input_tokens * price.input_per_million
            + self.output_tokens * price.output_per_million
        ) / Decimal(1000000)


@dataclass(frozen=True)
class ConsumerArm:
    spec: ConsumerSpec
    commit: str
    provider: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if self.spec.name not in {"folio-enrich", "folio-mapper"} or not re.fullmatch(
            r"[0-9a-f]{7,40}", self.commit
        ):
            raise ComparisonError("invalid consumer or pinned commit")
        if (self.provider, self.model) not in {
            (None, None),
            ("google", "gemini-3-flash-preview"),
            ("openai", "gpt-6-luna"),
        }:
            raise ComparisonError("unsupported consumer arm")

    @property
    def key(self) -> str:
        return f"{self.spec.name}-{self.model or 'deterministic'}"


def consumer_arms(specs: Sequence[ConsumerSpec], *, mapper_commit: str) -> tuple[ConsumerArm, ...]:
    """U9's reviewed mapper commit is mandatory; never silently use its pilot pin."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", mapper_commit) or mapper_commit.startswith("626412b"):
        raise ComparisonError("mapper requires the reviewed U9 commit")
    arms = []
    for spec in specs:
        if spec.name not in {"folio-enrich", "folio-mapper"}:
            raise ComparisonError("unknown consumer")
        pin = "bb576ac" if spec.name == "folio-enrich" else mapper_commit
        for provider, model in (
            (None, None),
            ("google", "gemini-3-flash-preview"),
            ("openai", "gpt-6-luna"),
        ):
            arms.append(ConsumerArm(spec, pin, provider, model))
    return tuple(arms)


def assert_arm_checkout(arm: ConsumerArm) -> None:
    """Read-only pin, detached-HEAD, clean-tree, and interpreter checks."""
    state = _git_repository_state(arm.spec.repo_root)
    if not str(state["git_sha"]).startswith(arm.commit):
        raise ComparisonError("consumer checkout differs from pinned commit")
    result = subprocess.run(
        ["git", "-C", str(arm.spec.repo_root), "symbolic-ref", "-q", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 1:
        raise ComparisonError("consumer checkout must have detached HEAD")
    expected = arm.spec.repo_root / "backend/.venv/bin/python"
    if arm.spec.venv_python.absolute() != expected.absolute() or not expected.is_file():
        raise ComparisonError("consumer must use its own backend/.venv interpreter")


class SpendGuard:
    """Persistent cumulative reservations; failed calls never refund uncertain spend."""

    def __init__(self, path: Path):
        self.path = path

    @property
    def spent(self) -> Decimal:
        if not self.path.exists():
            return Decimal(0)
        value = Decimal(json.loads(self.path.read_text())["reserved_usd"])
        if not value.is_finite() or value < 0 or value > CAP_USD:
            raise ComparisonError("invalid spend ledger")
        return value

    def reserve(self, cost: Decimal) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not cost.is_finite() or cost < 0:
                raise ComparisonError("invalid batch cost")
            total = self.spent + cost
            if total > CAP_USD:
                raise SpendLimitError(f"next batch would reserve ${total}; cap is $25")
            _atomic_write_text(self.path, json.dumps({"reserved_usd": str(total)}))


def _serialize_run(run: StackRun) -> dict[str, object]:
    return {
        "stack": run.stack,
        "lane": run.lane,
        "folio_resolve_version": run.folio_resolve_version,
        "folio_python_version": run.folio_python_version,
        "config": dict(run.config),
        "rows": {k: sorted(v) for k, v in run.rows.items()},
        "stages": dict(run.stages),
        "stage_order": {k: list(v) for k, v in run.stages.items()},
        "repository": dict(run.repository),
    }


def _load_run(payload: dict[str, object]) -> StackRun:
    # JSON shape is checked again through the consumer validators before reuse.
    rows = payload["rows"]
    config, stages, repository = payload["config"], payload["stages"], payload["repository"]
    if not all(isinstance(v, dict) for v in (rows, config, stages, repository)):
        raise ComparisonError("malformed persisted consumer batch")
    assert isinstance(rows, dict) and isinstance(config, dict)
    assert isinstance(stages, dict) and isinstance(repository, dict)
    order = payload.get("stage_order")
    if order is not None:
        if not isinstance(order, dict) or set(order) != set(stages):
            raise ComparisonError("invalid persisted stage order")
        ordered = {}
        for item_id, names in order.items():
            if (
                not isinstance(names, list)
                or not all(isinstance(n, str) for n in names)
                or len(names) != len(set(names))
                or set(names) != set(stages[item_id])
            ):
                raise ComparisonError("invalid persisted stage order")
            ordered[item_id] = {name: stages[item_id][name] for name in names}
        stages = ordered
    elif payload["stack"] == "folio-enrich" and payload["lane"] == "llm-on":
        raise ComparisonError("persisted enrich LLM snapshot lacks runner stage order")
    return StackRun(
        stack=str(payload["stack"]),
        lane=str(payload["lane"]),
        folio_resolve_version=str(payload["folio_resolve_version"]),
        folio_python_version=str(payload["folio_python_version"]),
        config=config,
        stages=stages,
        repository=repository,
        rows={k: frozenset(v) for k, v in rows.items()},
    )


def run_consumer_campaign(
    *,
    arms: Sequence[ConsumerArm],
    items_path: Path,
    scoreable_ids: Sequence[str],
    local_dir: Path,
    bounds: Mapping[str, TokenBound],
    prices: Mapping[str, ModelPrice | None] = PINNED_PRICES,
    batch_size: int = 5,
    prepare: bool = False,
) -> dict[str, object]:
    """Canary all paid arms before full batches; return only portable fingerprints.

    Reuse the same machine-local directory for the entire authorized campaign,
    including restarts. A single-writer campaign lock also protects snapshot reuse.
    Inputs, prices, bounds, pins and batching are bound by a checkpoint fingerprint.
    """
    if batch_size < 1 or not arms or len({a.key for a in arms}) != len(arms):
        raise ComparisonError("invalid campaign arms or batch size")
    costs: dict[str, Decimal] = {}
    for arm in arms:
        if arm.provider:
            price = pinned_price(arm.model or "", prices)
            if arm.key not in bounds:
                raise ComparisonError("missing conservative per-item token bound")
            costs[arm.key] = bounds[arm.key].cost(price)
        else:
            costs[arm.key] = Decimal(0)
    for root in (FOLIO_RESOLVE_ROOT, *(a.spec.repo_root for a in arms)):
        if local_dir.resolve().is_relative_to(root.resolve()):
            raise ComparisonError("consumer snapshots must be machine-local outside repositories")
    items = [json.loads(line) for line in items_path.read_text().splitlines()]
    ids = [item["item_id"] for item in items]
    if not ids or len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        raise ComparisonError("invalid campaign item IDs")
    if len(set(scoreable_ids)) != len(scoreable_ids) or not set(scoreable_ids).issubset(ids):
        raise ComparisonError("invalid scoreable item IDs")
    if any(a.provider for a in arms) and len(scoreable_ids) < 5:
        raise ComparisonError("canary requires five scoreable passages")
    for arm in arms:
        assert_arm_checkout(arm)
    local_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (local_dir / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _run_locked(
            arms, items, scoreable_ids, local_dir, bounds, prices, costs, batch_size, prepare
        )


def _run_locked(
    arms: Sequence[ConsumerArm],
    items: list[dict[str, object]],
    scoreable_ids: Sequence[str],
    local_dir: Path,
    bounds: Mapping[str, TokenBound],
    prices: Mapping[str, ModelPrice | None],
    costs: Mapping[str, Decimal],
    batch_size: int,
    prepare: bool,
) -> dict[str, object]:
    identity = json.dumps(
        {
            "schema": 1,
            "items": items,
            "scoreable_ids": list(scoreable_ids),
            "batch_size": batch_size,
            "arms": [
                {
                    "key": a.key,
                    "commit": a.commit,
                    "provider": a.provider,
                    "model": a.model,
                    "root": str(a.spec.repo_root.resolve()),
                }
                for a in arms
            ],
            "prices": {
                a.model: asdict(price)
                for a in arms
                if a.model and (price := prices[a.model]) is not None
            },
            "bounds": {k: asdict(v) for k, v in bounds.items()},
        },
        sort_keys=True,
        default=str,
    )
    fingerprint = sha256_text(identity)
    manifest = local_dir / "campaign.json"
    ledger = local_dir / "spend.json"
    if manifest.exists():
        if json.loads(manifest.read_text()) != {"sha256": fingerprint} or not ledger.exists():
            raise ComparisonError("campaign checkpoint identity or ledger mismatch")
    else:
        if any(local_dir.glob("*/batch-*.json")) or ledger.exists():
            raise ComparisonError("orphan campaign checkpoint")
        _atomic_write_text(ledger, json.dumps({"reserved_usd": "0"}))
        _atomic_write_text(manifest, json.dumps({"sha256": fingerprint}))
    guard = SpendGuard(ledger)
    fingerprints: dict[str, str] = {}
    canary_ids = set(scoreable_ids[:5])
    canary = [item for item in items if item["item_id"] in canary_ids]

    def batch(arm: ConsumerArm, number: int, selected: list[dict[str, object]]) -> None:
        path = local_dir / arm.key / f"batch-{number:05d}.json"
        selected_ids = [str(item["item_id"]) for item in selected]
        if path.exists():
            stored = json.loads(path.read_text())
            run = _load_run(stored["run"])
            if stored["campaign_sha256"] != fingerprint or stored["item_ids"] != selected_ids:
                raise ComparisonError("batch checkpoint identity mismatch")
            if sha256_text(json.dumps(stored["run"], sort_keys=True)) != stored["sha256"]:
                raise ComparisonError("batch checkpoint fingerprint mismatch")
        else:
            # Durable reservation precedes the subprocess, including crash/failure paths.
            guard.reserve(costs[arm.key] * len(selected))
            input_path = local_dir / arm.key / "input.jsonl"
            _atomic_write_text(input_path, "".join(json.dumps(item) + "\n" for item in selected))
            try:
                run = run_consumer_stack(
                    arm.spec,
                    input_path,
                    prepare=prepare,
                    llm_provider=arm.provider,
                    llm_model=arm.model,
                )
            finally:
                input_path.unlink(missing_ok=True)
            payload = _serialize_run(run)
            stored = {
                "campaign_sha256": fingerprint,
                "item_ids": selected_ids,
                "run": payload,
                "sha256": sha256_text(json.dumps(payload, sort_keys=True)),
            }
            _atomic_write_text(path, json.dumps(stored, sort_keys=True))
        if arm.provider:
            _assert_consumer_llm_rows(run, selected_ids)
        else:
            _assert_consumer_config(run)
            _assert_consumer_rows(run, selected_ids)
        fingerprints[f"{arm.key}/{number}"] = sha256_text(path.read_text())

    for arm in arms:
        if arm.provider:
            batch(arm, 0, canary)
    # Neither runner exposes structured usage: use the charged five-item bound.
    projection = sum((costs[a.key] * len(items) for a in arms), Decimal(0))
    _atomic_write_text(
        local_dir / "projection.json",
        json.dumps(
            {
                "projection_usd": str(projection),
                "canary_items_per_paid_arm": 5,
                "basis": "conservative aggregate token bound; includes all calls and retries",
            }
        ),
    )
    if projection >= CAP_USD:
        raise SpendLimitError(f"canary projects ${projection}; full run must be under $25")
    for arm in arms:
        remaining = [
            item for item in items if not arm.provider or item["item_id"] not in canary_ids
        ]
        for start in range(0, len(remaining), batch_size):
            batch(
                arm,
                start // batch_size + (1 if arm.provider else 0),
                remaining[start : start + batch_size],
            )
    return {
        "campaign_sha256": fingerprint,
        "projection_usd": str(projection),
        "reserved_usd": str(guard.spent),
        "snapshots": fingerprints,
    }


def _stage_sets(run: StackRun, item_id: str) -> dict[str, frozenset[str]]:
    """Read snapshots in pipeline order, never alphabetic report order."""
    raw = run.stages[item_id]
    order: tuple[str, ...]
    if run.stack == "folio-mapper":
        order = (
            ("committed",)
            if run.lane == "llm-on"
            else ("stage1_filter", "embedding_rerank", "committed")
        )
    elif run.lane == "llm-on":
        order = tuple(raw)
    else:
        order = ("EntityRuler", "StringMatch", "Reconciliation", "Resolution")
    if not order or (run.lane != "llm-on" and set(raw) != set(order)):
        raise ValueError("missing or unexpected consumer stages")
    snapshots = {}
    for stage in order:
        values = raw.get(stage)
        if not isinstance(values, (list, tuple, set, frozenset)) or not all(
            isinstance(value, str) and value for value in values
        ):
            raise ValueError("consumer snapshot must contain IRIs")
        snapshots[stage] = frozenset(values)
    if run.stack == "folio-mapper" and snapshots["committed"] != run.rows[item_id]:
        raise ValueError("committed snapshot differs from final output")
    # Reserve "committed" for survival, rather than a loss at the commit step.
    if run.stack == "folio-mapper":
        del snapshots["committed"]
    snapshots["final_output"] = run.rows[item_id]
    return snapshots


def attribute_consumer(
    run: StackRun,
    gold: Mapping[str, Set[str]],
    *,
    resolved_votes: Mapping[str, Sequence[Mapping[str, float]]],
    resolve_attribution: Mapping[str, Any],
    nomatch_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Pure first-loss attribution and strict scores for one consumer arm.

    Gold contains only positive, scoreable items; no-match IDs are separate, as
    in comparison.score_stack. Votes must come from U2.resolve_grader_votes.
    Enrich LLM mappings must retain the runner's stage order. First loss wins
    even when a later stage recovers the relation; scores use final output.
    Resolve misses mean all U2 stages outside top_100. Count-only mapper LLM
    stages cannot establish production of uncommitted IRIs: those are unknown,
    not false, in the cross-tab. No input is mutated and no runner is invoked.
    """
    if run.stack not in {"folio-enrich", "folio-mapper"} or run.lane not in {
        "deterministic",
        "incumbent",
        "llm-on",
    }:
        raise ValueError("unknown consumer stack or lane")
    ids = set(gold) | set(nomatch_ids)
    if (
        set(gold) & set(nomatch_ids)
        or len(set(nomatch_ids)) != len(nomatch_ids)
        or any(not iris for iris in gold.values())
    ):
        raise ValueError("gold and no-match cohorts must be disjoint and explicit")
    if set(run.rows) != ids or set(run.stages) != ids:
        raise ValueError("consumer snapshot item IDs differ from scoring cohort")
    baseline = {}
    for row in resolve_attribution["relations"]:
        key = (row["item_id"], row["iri"])
        if key in baseline or row["stage"] not in STAGES:
            raise ValueError("duplicate or invalid resolve attribution relation")
        baseline[key] = row["stage"]
    if set(baseline) != {(k, iri) for k, iris in gold.items() for iri in iris}:
        raise ValueError("resolve attribution gold relations differ from consumer cohort")

    counts = MicroCounts()
    relations: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    stage_names: dict[str, None] = {}
    opaque = run.stack == "folio-mapper" and run.lane == "llm-on"
    for item_id in sorted(ids):
        snapshots = _stage_sets(run, item_id)
        predicted = run.rows[item_id]
        if item_id not in gold:
            continue
        iris = gold[item_id]
        counts.items += 1
        counts.gold += len(iris)
        counts.predicted += len(predicted)
        counts.tp += len(predicted & iris)
        counts.fp += len(predicted - iris)
        counts.fn += len(iris - predicted)
        counts.exact_items += predicted == iris
        counts.empty_prediction_items += not predicted
        votes = resolved_votes.get(item_id, ())
        if len(votes) > 3:
            raise ValueError("more than three grader votes")
        stage_names.update(
            dict.fromkeys(
                ("committed", "not_committed")
                if opaque
                else (*snapshots, "never_produced", "committed")
            )
        )
        for iri in sorted(iris):
            agreement = sum(v.get(iri, 0.0) >= DEFAULT_FLOOR for v in votes)
            if agreement < 2:
                raise ValueError("gold relation has fewer than two qualifying votes")
            produced: bool | None = any(iri in values for values in snapshots.values())
            if opaque:
                stage = "committed" if iri in predicted else "not_committed"
                produced = True if iri in predicted else None
            else:
                seen = False
                stage = "never_produced"
                for name, values in snapshots.items():
                    if iri in values:
                        seen = True
                        stage = "committed"
                    elif seen:
                        stage = name
                        break
            relation = dict(item_id=item_id, iri=iri, stage=stage, agreement=agreement)
            relations.append(relation)
            resolve_stage = baseline[(item_id, iri)]
            if resolve_stage != "top_100":
                missed.append(
                    dict(
                        **relation,
                        resolve_stage=resolve_stage,
                        produced=produced,
                        committed=iri in predicted,
                    )
                )
    metrics = counts.to_json()
    fp = sum(bool(run.rows[k]) for k in nomatch_ids)
    metrics.update(
        nomatch_items=len(nomatch_ids),
        nomatch_false_positives=fp,
        nomatch_fp_rate=round(fp / len(nomatch_ids), 6) if nomatch_ids else 0.0,
    )
    return {
        "schema_version": 1,
        "stack": run.stack,
        "lane": run.lane,
        "metrics": metrics,
        "relations": relations,
        "gold_relation_count": len(relations),
        "overall": {
            stage: {
                "count": sum(r["stage"] == stage for r in relations),
                "by_agreement": {
                    str(n): sum(r["stage"] == stage and r["agreement"] == n for r in relations)
                    for n in (2, 3)
                },
            }
            for stage in stage_names
        },
        "resolve_misses": {
            "relations": missed,
            "by_stage": {
                stage: {
                    "count": sum(r["resolve_stage"] == stage for r in missed),
                    "produced": sum(
                        r["resolve_stage"] == stage and r["produced"] is True for r in missed
                    ),
                    "committed": sum(
                        r["resolve_stage"] == stage and r["committed"] for r in missed
                    ),
                    "produced_unknown": sum(
                        r["resolve_stage"] == stage and r["produced"] is None for r in missed
                    ),
                }
                for stage in STAGES
                if stage != "top_100"
            },
        },
    }
