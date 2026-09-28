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
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

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
from .intake import sha256_text

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


# Checked offline 2026-09-27: no list prices in this repo or enrich's pricing.py.
# That module fetches LiteLLM dynamically; it provides no pinned price evidence.
# TODO: orchestrator must supply sourced, dated list prices before any paid run.
PINNED_PRICES: Mapping[str, ModelPrice | None] = {
    "gemini-3-flash-preview": None,
    "gpt-6-luna": None,
}


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
            price = prices.get(arm.model or "")
            if price is None:
                raise PriceUnavailable("missing pinned price for consumer model")
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
