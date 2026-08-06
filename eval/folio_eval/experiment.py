"""The iteration protocol runner (U6; R9, R10, R12; KTD7, KTD8, AE4).

KD3 makes the loop three attempts per check-in; KTD8 makes each attempt one branch, one commit,
and one append-only record. This module is the bookkeeping in between: it captures the before
state, refuses to start when gold or the ontology moved out from under the window, measures the
after state, evaluates the AE4 overfit tripwire, and appends exactly one record — never rewriting
one that already landed.

Two calls, one state file in between
-------------------------------------

``start_attempt`` and ``finish_attempt`` are separate CLI invocations (the change happens on disk,
and often in a subprocess or another tool, between them), so a **pending attempt** — the captured
hypothesis, the window identity, and the "before" scores — is written to a gitignored JSON file
(``PendingAttempt``, KTD1: it is a per-run scratch artifact, never committed) and read back by
``finish_attempt``. Only one attempt may be pending at a time, matching KTD8's "one attempt at a
time" discipline.

Window discipline (KTD8)
-------------------------

The **window baseline** is the ``(gold_version, ontology_hash)`` pair every attempt in the current
window must match: the most recent :class:`BoundaryRecord` in the log, or — before any boundary
exists — the very first attempt record ever written (which implicitly establishes the window).
``start_attempt`` refuses to proceed when the caller's gold version or ontology hash disagrees
with that baseline, unless ``rebaseline=True`` is passed, in which case a boundary record is
appended first and the new window starts empty — which also means the next attempt's
``scores_before`` cannot be reused from a prior attempt (there is none in the new window) and must
come from a fresh run, exactly the "re-score on the new gold before the next iteration" rule KTD8
names.

``scores_before`` resolution order: an explicit ``prior_scores`` override, then the last attempt
record already in the current window (no rerun needed), then a fresh ``score_tune_firm2()`` call.

The AE4 tripwire
-----------------

:func:`evaluate_ae4_tripwire` reuses :mod:`.report`'s bootstrap machinery exactly —
:class:`.report.PairedItem`, :func:`.report.bootstrap_ci`, :func:`.report.net_changed_items`,
:func:`.report.changed_item_breakdown` — over the Firm-2 signal slice's before/after item
outcomes. It flags an attempt when the changed-item bootstrap CI never crosses back above zero
(the whole interval reads as a regression) **or** when any single Firm-2 item flips from correct
to incorrect, whichever fires first; frozen is never touched here (R6, KTD4).

No committed file may carry a firm surface string (KTD1). The hypothesis and every free-text
reason are leak-scanned against the caller's gold surface set before a record is appended
(:func:`append_record`, reusing :func:`.clusters.assert_no_surfaces`); a hit refuses the write
rather than silently dropping the offending text.

The incremental triage hook
----------------------------

:func:`incremental_triage` re-runs U5's own-origin-synonymy classification
(:func:`.audit._score_driven_suspects`, reused rather than reimplemented) over cluster rows an
attempt's re-scoring pass produced, and appends any not-yet-seen suspects to a gitignored live
stream so a check-in (U10) has a running suspect list between audit gates, without rebuilding the
full U5 packet after every attempt (R11).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .audit import GoldRow, _score_driven_suspects
from .clusters import assert_no_surfaces
from .report import (
    DEFAULT_BOOTSTRAP_RESAMPLES,
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_ITEM_REPORT_DIR,
    BootstrapCI,
    PairedItem,
    bootstrap_ci,
    changed_item_breakdown,
    net_changed_items,
)
from .score import ItemScore, MicroCounts
from .selftest import DEFAULT_SELFTEST_TARGET, run_determinism_selftest

_EVAL_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _EVAL_ROOT.parent

#: Committed (KTD1: IDs, hashes, counts, and free-text hypothesis/reason -- leak-scanned first).
DEFAULT_EXPERIMENTS_LOG = _EVAL_ROOT / "reports" / "experiments.jsonl"
#: Gitignored: the one in-flight attempt's captured state between ``start`` and ``finish``.
DEFAULT_PENDING_PATH = DEFAULT_ITEM_REPORT_DIR / "experiment_pending.json"
#: Gitignored: the running suspect stream the incremental triage hook maintains.
DEFAULT_LIVE_SUSPECTS_PATH = DEFAULT_ITEM_REPORT_DIR / "live_suspects.jsonl"

#: KD3: three attempts per check-in (reverted attempts count).
CHECK_IN_ATTEMPTS = 3

RECORD_ATTEMPT = "attempt"
RECORD_BOUNDARY = "boundary"

DECISIONS = ("keep", "revert", "park")


class ExperimentError(RuntimeError):
    """Raised for a malformed or impossible experiment-log operation."""


class WindowRefusalError(ExperimentError):
    """Raised when gold or the ontology moved mid-window without an explicit rebaseline (KTD8)."""


class PendingAttemptError(ExperimentError):
    """Raised for pending-attempt state violations: none in progress, or one already is."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, (int, str)):
        return int(value)
    raise ValueError(f"expected an integer, got {value!r}")


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


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
# Per-item outcomes (no surface strings -- item_id + counts only)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """One item's (tp, fp, fn, exact) outcome for one slice. Never carries a surface string."""

    item_id: str
    tp: int
    fp: int
    fn: int
    exact: bool

    def to_json(self) -> dict[str, object]:
        return {"item_id": self.item_id, "tp": self.tp, "fp": self.fp, "fn": self.fn, "exact": self.exact}

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> ItemOutcome:
        return cls(
            item_id=str(payload["item_id"]),
            tp=_as_int(payload.get("tp"), 0),
            fp=_as_int(payload.get("fp"), 0),
            fn=_as_int(payload.get("fn"), 0),
            exact=bool(payload.get("exact", False)),
        )

    @classmethod
    def from_item_score(cls, score: ItemScore) -> ItemOutcome:
        return cls(item_id=score.item_id, tp=score.tp, fp=score.fp, fn=score.fn, exact=score.exact)


@dataclass(frozen=True, slots=True)
class SliceOutcome:
    """One slice's (tune or firm2) item outcomes, ready to aggregate or pair."""

    slice_name: str
    items: tuple[ItemOutcome, ...]

    def aggregate(self) -> MicroCounts:
        counts = MicroCounts()
        for outcome in self.items:
            counts.items += 1
            counts.tp += outcome.tp
            counts.fp += outcome.fp
            counts.fn += outcome.fn
            counts.exact_items += 1 if outcome.exact else 0
        return counts


def build_scores_json(tune: SliceOutcome, firm2: SliceOutcome) -> dict[str, object]:
    """The ``scores_before``/``scores_after`` shape a record carries: tune aggregate, Firm-2
    aggregate plus its item outcomes (the AE4 pairing unit for the *next* attempt). Frozen never
    appears here (R6, KTD4)."""
    return {
        "tune": tune.aggregate().to_json(),
        "firm2": {
            "aggregate": firm2.aggregate().to_json(),
            "items": [item.to_json() for item in sorted(firm2.items, key=lambda entry: entry.item_id)],
        },
    }


def firm2_items_from_scores_json(payload: Mapping[str, object]) -> tuple[ItemOutcome, ...]:
    firm2 = payload.get("firm2")
    if not isinstance(firm2, Mapping):
        return ()
    items_raw = firm2.get("items")
    if not isinstance(items_raw, list):
        return ()
    return tuple(ItemOutcome.from_json(entry) for entry in items_raw if isinstance(entry, Mapping))


# --------------------------------------------------------------------------------------
# AE4 tripwire
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TripwireResult:
    """AE4's verdict: the Firm-2 changed-item CI, the raw breakdown, and the two flags it ORs."""

    ci: BootstrapCI
    breakdown: Mapping[str, int]
    ci_negative: bool
    any_regression: bool
    flagged: bool

    def to_json(self) -> dict[str, object]:
        return {
            "ci": self.ci.to_json(),
            "breakdown": dict(self.breakdown),
            "ci_negative": self.ci_negative,
            "any_regression": self.any_regression,
            "flagged": self.flagged,
        }


def pair_outcomes(before: Sequence[ItemOutcome], after: Sequence[ItemOutcome]) -> list[PairedItem]:
    """Pair Firm-2 before/after outcomes by ``item_id`` -- the AE4 resampling unit.

    Builds real :class:`.report.PairedItem` records directly (rather than routing through
    :func:`.report.pair_items`, which is typed against full ``ItemScore`` objects) so the bootstrap
    machinery downstream is exactly the one :mod:`.report` already ships.
    """
    before_by_id = {outcome.item_id: outcome for outcome in before}
    after_by_id = {outcome.item_id: outcome for outcome in after}
    shared = sorted(set(before_by_id) & set(after_by_id))
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
        for item_id in shared
    ]


def evaluate_ae4_tripwire(
    before: Sequence[ItemOutcome],
    after: Sequence[ItemOutcome],
    *,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> TripwireResult:
    """AE4: flag when the Firm-2 changed-item CI is negative, or any Firm-2 item regresses
    correct -> incorrect -- whichever fires. Both read off the same bootstrap pass."""
    paired = pair_outcomes(before, after)
    ci = bootstrap_ci(paired, net_changed_items, n_resamples=n_resamples, seed=seed)
    breakdown = changed_item_breakdown(paired)
    ci_negative = ci.high < 0.0
    any_regression = breakdown["regressed"] > 0
    return TripwireResult(
        ci=ci,
        breakdown=breakdown,
        ci_negative=ci_negative,
        any_regression=any_regression,
        flagged=ci_negative or any_regression,
    )


# --------------------------------------------------------------------------------------
# The committed log: attempt and boundary records
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """One KTD8 attempt record. Append-only; never rewritten once it lands."""

    attempt_id: str
    iteration: int
    gold_version: int
    ontology_hash: str
    config_hash: str
    commit_sha: str
    hypothesis: str
    cluster_targeted: str
    cluster_size: int
    scores_before: Mapping[str, object]
    scores_after: Mapping[str, object]
    tripwire: Mapping[str, object]
    triage: Mapping[str, object]
    decision: str
    reason: str
    recorded_at: str

    def to_json(self) -> dict[str, object]:
        return {
            "record_type": RECORD_ATTEMPT,
            "attempt_id": self.attempt_id,
            "iteration": self.iteration,
            "gold_version": self.gold_version,
            "ontology_hash": self.ontology_hash,
            "config_hash": self.config_hash,
            "commit_sha": self.commit_sha,
            "hypothesis": self.hypothesis,
            "cluster_targeted": self.cluster_targeted,
            "cluster_size": self.cluster_size,
            "scores_before": dict(self.scores_before),
            "scores_after": dict(self.scores_after),
            "tripwire": dict(self.tripwire),
            "triage": dict(self.triage),
            "decision": self.decision,
            "reason": self.reason,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> ExperimentRecord:
        return cls(
            attempt_id=str(payload.get("attempt_id", "")),
            iteration=_as_int(payload.get("iteration"), 0),
            gold_version=_as_int(payload.get("gold_version"), 0),
            ontology_hash=str(payload.get("ontology_hash", "")),
            config_hash=str(payload.get("config_hash", "")),
            commit_sha=str(payload.get("commit_sha", "")),
            hypothesis=str(payload.get("hypothesis", "")),
            cluster_targeted=str(payload.get("cluster_targeted", "")),
            cluster_size=_as_int(payload.get("cluster_size"), 0),
            scores_before=_mapping(payload.get("scores_before")),
            scores_after=_mapping(payload.get("scores_after")),
            tripwire=_mapping(payload.get("tripwire")),
            triage=_mapping(payload.get("triage")),
            decision=str(payload.get("decision", "")),
            reason=str(payload.get("reason", "")),
            recorded_at=str(payload.get("recorded_at", "")),
        )


@dataclass(frozen=True, slots=True)
class BoundaryRecord:
    """A deliberate re-baseline: a gold bump or ontology bump at a gate/check-in boundary."""

    gold_version: int
    ontology_hash: str
    after_attempt_count: int
    reason: str
    recorded_at: str

    def to_json(self) -> dict[str, object]:
        return {
            "record_type": RECORD_BOUNDARY,
            "gold_version": self.gold_version,
            "ontology_hash": self.ontology_hash,
            "after_attempt_count": self.after_attempt_count,
            "reason": self.reason,
            "recorded_at": self.recorded_at,
        }


def load_raw_records(path: Path) -> list[dict[str, object]]:
    """Read a jsonl file as plain dicts. Empty list when the file does not exist yet."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_record(path: Path, payload: Mapping[str, object], *, surfaces: Iterable[str]) -> Path:
    """Append one JSON line, refusing a firm-surface leak before it ever touches disk (KTD1)."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    assert_no_surfaces(text, surfaces, what=str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _attempt_records(records: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [record for record in records if record.get("record_type") == RECORD_ATTEMPT]


def _boundary_records(records: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [record for record in records if record.get("record_type") == RECORD_BOUNDARY]


@dataclass(frozen=True, slots=True)
class WindowBaseline:
    """The current window's pinned ``(gold_version, ontology_hash)`` every attempt must match."""

    gold_version: int
    ontology_hash: str
    established_at: str


def current_window_baseline(records: Sequence[Mapping[str, object]]) -> WindowBaseline | None:
    """The most recent boundary record, else the first attempt ever, else ``None`` (empty log)."""
    boundaries = _boundary_records(records)
    if boundaries:
        last = boundaries[-1]
        return WindowBaseline(
            gold_version=_as_int(last.get("gold_version"), 0),
            ontology_hash=str(last.get("ontology_hash", "")),
            established_at=str(last.get("recorded_at", "")),
        )
    attempts = _attempt_records(records)
    if attempts:
        first = attempts[0]
        return WindowBaseline(
            gold_version=_as_int(first.get("gold_version"), 0),
            ontology_hash=str(first.get("ontology_hash", "")),
            established_at=str(first.get("recorded_at", "")),
        )
    return None


def _last_attempt_in_window(records: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    boundaries = _boundary_records(records)
    after = _as_int(boundaries[-1].get("after_attempt_count"), 0) if boundaries else 0
    in_window = _attempt_records(records)[after:]
    return in_window[-1] if in_window else None


# --------------------------------------------------------------------------------------
# Window status
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowStatus:
    """The check-in tally: attempts in the current window, and where the baseline sits."""

    total_attempts: int
    window_attempts: int
    baseline_gold_version: int | None
    baseline_ontology_hash: str | None
    check_in_due: bool
    last_iteration: int
    last_decision: str | None
    keep_count: int
    revert_count: int
    park_count: int

    def to_json(self) -> dict[str, object]:
        return {
            "total_attempts": self.total_attempts,
            "window_attempts": self.window_attempts,
            "baseline_gold_version": self.baseline_gold_version,
            "baseline_ontology_hash": self.baseline_ontology_hash,
            "check_in_due": self.check_in_due,
            "last_iteration": self.last_iteration,
            "last_decision": self.last_decision,
            "keep_count": self.keep_count,
            "revert_count": self.revert_count,
            "park_count": self.park_count,
        }


def compute_status(records: Sequence[Mapping[str, object]]) -> WindowStatus:
    """Reverted (and parked) attempts count toward the tally exactly like kept ones (KTD8)."""
    attempts = _attempt_records(records)
    boundaries = _boundary_records(records)
    baseline = current_window_baseline(records)
    after = _as_int(boundaries[-1].get("after_attempt_count"), 0) if boundaries else 0
    in_window = attempts[after:]
    return WindowStatus(
        total_attempts=len(attempts),
        window_attempts=len(in_window),
        baseline_gold_version=baseline.gold_version if baseline else None,
        baseline_ontology_hash=baseline.ontology_hash if baseline else None,
        check_in_due=len(in_window) >= CHECK_IN_ATTEMPTS,
        last_iteration=_as_int(attempts[-1].get("iteration"), 0) if attempts else 0,
        last_decision=str(attempts[-1].get("decision")) if attempts else None,
        keep_count=sum(1 for record in in_window if record.get("decision") == "keep"),
        revert_count=sum(1 for record in in_window if record.get("decision") == "revert"),
        park_count=sum(1 for record in in_window if record.get("decision") == "park"),
    )


def status(experiments_log: Path = DEFAULT_EXPERIMENTS_LOG) -> WindowStatus:
    return compute_status(load_raw_records(experiments_log))


# --------------------------------------------------------------------------------------
# Pending attempt (gitignored scratch state between start and finish)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PendingAttempt:
    """Everything ``start_attempt`` captured, read back by ``finish_attempt``."""

    attempt_id: str
    iteration: int
    hypothesis: str
    cluster_targeted: str
    cluster_size: int
    gold_version: int
    ontology_hash: str
    config_hash: str
    started_at: str
    determinism_selftest: Mapping[str, object]
    scores_before: Mapping[str, object]
    firm2_before_items: tuple[ItemOutcome, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "iteration": self.iteration,
            "hypothesis": self.hypothesis,
            "cluster_targeted": self.cluster_targeted,
            "cluster_size": self.cluster_size,
            "gold_version": self.gold_version,
            "ontology_hash": self.ontology_hash,
            "config_hash": self.config_hash,
            "started_at": self.started_at,
            "determinism_selftest": dict(self.determinism_selftest),
            "scores_before": dict(self.scores_before),
            "firm2_before_items": [item.to_json() for item in self.firm2_before_items],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> PendingAttempt:
        items_raw = payload.get("firm2_before_items")
        items = items_raw if isinstance(items_raw, list) else []
        return cls(
            attempt_id=str(payload["attempt_id"]),
            iteration=_as_int(payload.get("iteration"), 0),
            hypothesis=str(payload.get("hypothesis", "")),
            cluster_targeted=str(payload.get("cluster_targeted", "")),
            cluster_size=_as_int(payload.get("cluster_size"), 0),
            gold_version=_as_int(payload.get("gold_version"), 0),
            ontology_hash=str(payload.get("ontology_hash", "")),
            config_hash=str(payload.get("config_hash", "")),
            started_at=str(payload.get("started_at", "")),
            determinism_selftest=_mapping(payload.get("determinism_selftest")),
            scores_before=_mapping(payload.get("scores_before")),
            firm2_before_items=tuple(
                ItemOutcome.from_json(entry) for entry in items if isinstance(entry, Mapping)
            ),
        )


# --------------------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------------------


def git_commit_sha(repo_root: Path | None = None) -> str:
    """``HEAD`` of the repo the attempt's commit is expected to land in (KTD8)."""
    root = repo_root or _REPO_ROOT
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ExperimentError(f"git rev-parse HEAD failed in {root}: {result.stderr.strip()}")
    return result.stdout.strip()


# --------------------------------------------------------------------------------------
# Incremental triage hook (reuses audit._score_driven_suspects, R11)
# --------------------------------------------------------------------------------------


def incremental_triage(
    cluster_rows: Sequence[Mapping[str, object]],
    gold_rows: Sequence[GoldRow],
    *,
    stream_path: Path = DEFAULT_LIVE_SUSPECTS_PATH,
) -> list[dict[str, object]]:
    """New score-driven suspects since the last call, appended to the live suspect stream.

    Reuses :func:`.audit._score_driven_suspects` -- U5's own-origin-synonymy classification --
    rather than reimplementing it, so a check-in (U10) sees a running suspect list between audit
    gates without a full packet rebuild after every attempt (R11). Returns only the newly seen
    entries (already-streamed ``item_id``s are deduped).
    """
    by_id = {row.item_id: row for row in gold_rows}
    fresh = _score_driven_suspects(cluster_rows, by_id)
    existing = load_raw_records(stream_path)
    seen = {str(entry.get("item_id")) for entry in existing}
    new_entries = [entry for entry in fresh if str(entry.get("item_id")) not in seen]
    if new_entries:
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        with stream_path.open("a", encoding="utf-8") as handle:
            for entry in new_entries:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return new_entries


# --------------------------------------------------------------------------------------
# start_attempt / finish_attempt
# --------------------------------------------------------------------------------------


ScoreFn = Callable[[], tuple[SliceOutcome, SliceOutcome]]


def start_attempt(
    *,
    hypothesis: str,
    cluster_targeted: str,
    cluster_size: int,
    gold_version: int,
    ontology_hash: str,
    config_hash: str,
    surfaces: Iterable[str],
    score_tune_firm2: ScoreFn | None = None,
    prior_scores: tuple[SliceOutcome, SliceOutcome] | None = None,
    rebaseline: bool = False,
    rebaseline_reason: str = "",
    determinism_target: str = DEFAULT_SELFTEST_TARGET,
    run_selftest: bool = True,
    experiments_log: Path = DEFAULT_EXPERIMENTS_LOG,
    pending_path: Path = DEFAULT_PENDING_PATH,
    now: str | None = None,
) -> PendingAttempt:
    """Open one iteration attempt: leak-scan, window check, determinism self-test, before scores.

    Refuses to start (raises :class:`WindowRefusalError`) when ``gold_version``/``ontology_hash``
    disagree with the current window's baseline unless ``rebaseline=True``, which records a
    :class:`BoundaryRecord` first and starts a fresh window (KTD8). Refuses (raises
    :class:`PendingAttemptError`) when another attempt is already pending, and (raises
    ``clusters.SurfaceLeakError``) when ``hypothesis`` contains a firm surface string.
    """
    if pending_path.exists():
        raise PendingAttemptError(
            f"an attempt is already pending at {pending_path} — finish or discard it first"
        )
    surface_list = tuple(surfaces)
    assert_no_surfaces(hypothesis, surface_list, what="hypothesis")

    records = load_raw_records(experiments_log)
    baseline = current_window_baseline(records)

    if baseline is not None:
        moved = baseline.gold_version != gold_version or baseline.ontology_hash != ontology_hash
        if moved and not rebaseline:
            raise WindowRefusalError(
                "refusing to start an attempt: gold/ontology moved mid-window "
                f"(window baseline gold_version={baseline.gold_version} "
                f"ontology={baseline.ontology_hash[:12]}; current gold_version={gold_version} "
                f"ontology={ontology_hash[:12]}) — gold bumps only happen at gate/check-in "
                "boundaries; pass rebaseline=True to start a new window (KTD8)"
            )
        if moved and rebaseline:
            boundary = BoundaryRecord(
                gold_version=gold_version,
                ontology_hash=ontology_hash,
                after_attempt_count=len(_attempt_records(records)),
                reason=rebaseline_reason,
                recorded_at=now or _now(),
            )
            append_record(experiments_log, boundary.to_json(), surfaces=surface_list)
            records = [*records, boundary.to_json()]

    started_at = now or _now()
    selftest_json: dict[str, object] = {}
    if run_selftest:
        selftest_json = run_determinism_selftest(determinism_target).to_json()

    source_record = _last_attempt_in_window(records)
    if prior_scores is not None:
        scores_before = build_scores_json(*prior_scores)
    elif source_record is not None:
        scores_after_raw = source_record.get("scores_after")
        if not isinstance(scores_after_raw, Mapping):
            raise ExperimentError(
                f"prior attempt {source_record.get('attempt_id')!r} has no scores_after to reuse"
            )
        scores_before = dict(scores_after_raw)
    elif score_tune_firm2 is not None:
        scores_before = build_scores_json(*score_tune_firm2())
    else:
        raise ExperimentError(
            "no scores_before source available: pass prior_scores, or score_tune_firm2 for a "
            "fresh run, or start this attempt after a prior one exists in the window"
        )
    firm2_before_items = firm2_items_from_scores_json(scores_before)

    iteration = len(_attempt_records(records)) + 1
    pending = PendingAttempt(
        attempt_id=f"attempt-{iteration:04d}",
        iteration=iteration,
        hypothesis=hypothesis,
        cluster_targeted=cluster_targeted,
        cluster_size=cluster_size,
        gold_version=gold_version,
        ontology_hash=ontology_hash,
        config_hash=config_hash,
        started_at=started_at,
        determinism_selftest=selftest_json,
        scores_before=scores_before,
        firm2_before_items=firm2_before_items,
    )
    _atomic_write_text(
        pending_path, json.dumps(pending.to_json(), ensure_ascii=False, sort_keys=True) + "\n"
    )
    return pending


def finish_attempt(
    *,
    decision: str,
    reason: str,
    surfaces: Iterable[str],
    score_tune_firm2: ScoreFn | None = None,
    after_scores: tuple[SliceOutcome, SliceOutcome] | None = None,
    commit_sha: str | None = None,
    cluster_rows: Sequence[Mapping[str, object]] | None = None,
    gold_rows: Sequence[GoldRow] | None = None,
    live_suspects_path: Path = DEFAULT_LIVE_SUSPECTS_PATH,
    experiments_log: Path = DEFAULT_EXPERIMENTS_LOG,
    pending_path: Path = DEFAULT_PENDING_PATH,
    now: str | None = None,
    ci_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    ci_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> ExperimentRecord:
    """Close the pending attempt: re-score, evaluate AE4, append the record, clear the pending state.

    ``decision`` must be one of :data:`DECISIONS` (``keep``, ``revert``, ``park``) -- reverted and
    parked attempts append exactly like kept ones and count toward the check-in tally (KTD8). When
    ``cluster_rows``/``gold_rows`` are supplied, the incremental triage hook runs too and its new-
    suspect count rides along on the record under ``triage``.
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}: {decision!r}")
    if not pending_path.exists():
        raise PendingAttemptError(
            f"no attempt in progress at {pending_path} — call start_attempt first"
        )
    pending = PendingAttempt.from_json(json.loads(pending_path.read_text(encoding="utf-8")))

    if after_scores is not None:
        tune_after, firm2_after = after_scores
    elif score_tune_firm2 is not None:
        tune_after, firm2_after = score_tune_firm2()
    else:
        raise ExperimentError(
            "finish_attempt needs after_scores or score_tune_firm2 to measure the outcome"
        )

    scores_after = build_scores_json(tune_after, firm2_after)
    firm2_after_items = firm2_items_from_scores_json(scores_after)
    tripwire = evaluate_ae4_tripwire(
        pending.firm2_before_items, firm2_after_items, n_resamples=ci_resamples, seed=ci_seed
    )

    triage: dict[str, object] = {"new_suspects": 0}
    if cluster_rows is not None and gold_rows is not None:
        new_entries = incremental_triage(cluster_rows, gold_rows, stream_path=live_suspects_path)
        triage = {"new_suspects": len(new_entries)}

    record = ExperimentRecord(
        attempt_id=pending.attempt_id,
        iteration=pending.iteration,
        gold_version=pending.gold_version,
        ontology_hash=pending.ontology_hash,
        config_hash=pending.config_hash,
        commit_sha=commit_sha or git_commit_sha(),
        hypothesis=pending.hypothesis,
        cluster_targeted=pending.cluster_targeted,
        cluster_size=pending.cluster_size,
        scores_before=pending.scores_before,
        scores_after=scores_after,
        tripwire=tripwire.to_json(),
        triage=triage,
        decision=decision,
        reason=reason,
        recorded_at=now or _now(),
    )
    append_record(experiments_log, record.to_json(), surfaces=surfaces)
    pending_path.unlink()
    return record


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - I/O orchestration
    """``python -m folio_eval.experiment start|finish|status`` -- U6's iteration protocol runner."""
    import argparse
    import sys

    from .answer_rule import DEFAULT_CONFIG_FILENAME, load_config
    from .clusters import ReplayPredictor, collect_raw_candidates, surface_strings
    from .score import Hierarchy, PipelineAdapter, build_folio_provider, build_pipeline, score_items
    from .selftest import OntologyPinError, assert_ontology_pin, ensure_hash_seed
    from .splits import (
        DEFAULT_GOLD_DIR,
        DEFAULT_SPLIT_MANIFEST,
        SIGNAL_SLICE,
        TUNE_SLICE,
        load_gold,
        load_split_manifest,
    )

    parser = argparse.ArgumentParser(
        prog="python -m folio_eval.experiment",
        description="U6: iteration protocol runner (start/finish an attempt, or check window status).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common_paths = argparse.ArgumentParser(add_help=False)
    common_paths.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR / "gold_v1.jsonl")
    common_paths.add_argument("--gold-manifest", type=Path, default=None)
    common_paths.add_argument(
        "--config", type=Path, default=DEFAULT_GOLD_DIR / DEFAULT_CONFIG_FILENAME
    )
    common_paths.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    common_paths.add_argument("--label-search-limit", type=int, default=10)
    common_paths.add_argument("--multi-strategy-recall", action="store_true")
    common_paths.add_argument("--experiments-log", type=Path, default=DEFAULT_EXPERIMENTS_LOG)
    common_paths.add_argument("--pending", type=Path, default=DEFAULT_PENDING_PATH)
    common_paths.add_argument("--allow-ontology-bump", action="store_true")

    start_p = sub.add_parser("start", parents=[common_paths])
    start_p.add_argument("--hypothesis", required=True)
    start_p.add_argument("--cluster-targeted", required=True)
    start_p.add_argument("--cluster-size", type=int, required=True)
    start_p.add_argument("--rebaseline", action="store_true")
    start_p.add_argument("--rebaseline-reason", default="")
    start_p.add_argument("--determinism-target", default=DEFAULT_SELFTEST_TARGET)

    finish_p = sub.add_parser("finish", parents=[common_paths])
    finish_p.add_argument("--decision", required=True, choices=DECISIONS)
    finish_p.add_argument("--reason", required=True)
    finish_p.add_argument("--commit-sha", default=None)

    status_p = sub.add_parser("status")
    status_p.add_argument("--experiments-log", type=Path, default=DEFAULT_EXPERIMENTS_LOG)

    args = parser.parse_args(argv)

    if args.command == "status":
        result = status(args.experiments_log)
        print(json.dumps(result.to_json(), indent=2, sort_keys=True))
        return 0

    ensure_hash_seed()
    gold = load_gold(args.gold, manifest_path=args.gold_manifest)
    try:
        pin = assert_ontology_pin(gold.ontology_cache_sha256)
    except OntologyPinError as error:
        if not args.allow_ontology_bump:
            print(f"ABORT: {error}", file=sys.stderr)
            return 2
        print(f"WARNING (--allow-ontology-bump): {error}", file=sys.stderr)
        pin = assert_ontology_pin("")
    split = load_split_manifest(args.split_manifest, gold)
    config = load_config(args.config)
    surfaces = surface_strings(gold)

    def score_tune_firm2() -> tuple[SliceOutcome, SliceOutcome]:
        from folio import FOLIO

        folio = FOLIO()
        provider = build_folio_provider(folio)
        pipeline = build_pipeline(
            provider,
            label_search_limit=args.label_search_limit,
            with_multi_strategy_recall=args.multi_strategy_recall,
        )
        hierarchy = Hierarchy.from_folio(folio)
        adapter = PipelineAdapter(pipeline)
        outcomes: dict[str, SliceOutcome] = {}
        for name in (TUNE_SLICE, SIGNAL_SLICE):
            items = split.slice_items(name, gold)
            cache = collect_raw_candidates(items, adapter, label=name)
            run = score_items(
                items, ReplayPredictor(cache), config=config, hierarchy=hierarchy, slice_name=name
            )
            outcomes[name] = SliceOutcome(
                slice_name=name,
                items=tuple(ItemOutcome.from_item_score(entry) for entry in run.item_scores),
            )
        return outcomes[TUNE_SLICE], outcomes[SIGNAL_SLICE]

    if args.command == "start":
        pending = start_attempt(
            hypothesis=args.hypothesis,
            cluster_targeted=args.cluster_targeted,
            cluster_size=args.cluster_size,
            gold_version=gold.gold_version,
            ontology_hash=pin.sha256,
            config_hash=config.content_sha256(),
            surfaces=surfaces,
            score_tune_firm2=score_tune_firm2,
            rebaseline=args.rebaseline,
            rebaseline_reason=args.rebaseline_reason,
            determinism_target=args.determinism_target,
            experiments_log=args.experiments_log,
            pending_path=args.pending,
        )
        print(json.dumps(pending.to_json(), indent=2, sort_keys=True))
        print(f"attempt pending: {pending.attempt_id} (iteration {pending.iteration})")
        return 0

    if args.command == "finish":
        record = finish_attempt(
            decision=args.decision,
            reason=args.reason,
            surfaces=surfaces,
            score_tune_firm2=score_tune_firm2,
            commit_sha=args.commit_sha,
            experiments_log=args.experiments_log,
            pending_path=args.pending,
        )
        print(json.dumps(record.to_json(), indent=2, sort_keys=True))
        recommendation = "revert" if record.tripwire.get("flagged") else "keep"
        print(f"AE4 flagged={record.tripwire.get('flagged')}  recorded decision={record.decision}"
              f"  recommendation={recommendation}")
        return 0

    return 2  # pragma: no cover - argparse subparsers already exhaust the choices


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
